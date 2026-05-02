"""
main.py — FastAPI 앱 진입점

페이지 라우트:
  GET  /              → 인증 시 /app 리다이렉트, 미인증 시 landing.html
  GET  /app           → app.html (반응형 사이드바 + iframe 쉘)
  GET  /login         → login.html
  GET  /onboard       → onboard.html (초대 토큰 검증)
  GET  /blog          → index.html (블로그 생성기)
  GET  /settings/setup → settings_setup.html (대표원장 전용 RBAC 위자드)

인증 API:
  POST /api/auth/login          → JWT 쿠키 발급
  POST /api/auth/logout         → JWT 쿠키 삭제
  GET  /api/auth/me             → 현재 사용자 정보
  POST /api/auth/change-password → 비밀번호 변경
  POST /api/auth/invite         → 초대 토큰 생성 (director+ 전용)
  GET  /api/auth/invite/verify  → 초대 토큰 검증 (온보딩 페이지용)
  POST /api/auth/onboard        → 온보딩 완료 (비밀번호 설정)

모듈/RBAC API:
  GET  /api/modules/my          → 내 역할에 허용된 모듈 목록
  GET  /api/modules/info        → 전체 모듈 정보
  POST /api/modules/config      → 직원 모듈 권한 저장
  GET  /api/settings/rbac       → RBAC 설정 조회
  POST /api/settings/rbac       → RBAC 설정 저장

한의원 프로필 API:
  GET  /api/settings/clinic/profile → 한의원 프로필 조회 (인증 필요)
  POST /api/settings/clinic/profile → 한의원 프로필 저장 (chief_director 전용)
  GET  /api/settings/plan/usage    → 플랜 & 사용량 조회 (인증 필요)

블로그 API:
  GET  /api/blog/stats          → 블로그 생성 통계
  POST /build-prompt            → 프롬프트 조립 (API 호출 없음, T1 복사용)
  POST /conversation-flow       → 대화 흐름 생성
  POST /generate                → 블로그 SSE 스트리밍
  POST /generate-image-prompts  → 이미지 프롬프트 SSE 스트리밍
"""

import asyncio
import base64
import json as _json
import logging as _logging
import os
import re as _re
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse,
    RedirectResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

# src/ 폴더를 파이썬 경로에 추가 (상대 임포트 없이 사용)
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env", override=True)

# ── 서버 시작 전 환경 변수 검증 ──────────────────────────────────
_secret = os.getenv("SECRET_KEY", "")
if not _secret:
    raise RuntimeError(
        "SECRET_KEY 환경 변수가 설정되지 않았습니다. "
        ".env 파일에 SECRET_KEY=<랜덤 32자 이상 문자열>을 추가하세요."
    )

# ── 옵저버빌리티 초기화 (Sentry + structlog, B2 / 2026-04-27) ─────
from observability import init_observability, RequestLoggingMiddleware
from version import __version__ as APP_VERSION

init_observability()

import anthropic
from collections import defaultdict
from time import time as _time_now

# ── API 키 암호화/복호화 → src/crypto_utils.py 로 이동 (v0.9.0).
# main.py 내부 호출자(어드민 OpenAI 키 라우트 등) 및 tests/test_onboarding 의
# monkeypatch.setattr(_main, "_get_fernet", ...) 호환을 위한 backward-compat alias.
from crypto_utils import (
    _get_fernet,
    encrypt_key as _encrypt_key,
    decrypt_key as _decrypt_key,
    mask_key as _mask_key,
)

from auth_manager import (
    COOKIE_NAME,
    authenticate_user,
    change_password,
    complete_onboarding,
    create_access_token,
    decode_token,
    verify_invite,
)
from blog_history import purge_expired_texts, update_naver_url
from db_manager import init_db, seed_demo_clinic, seed_demo_owner, seed_first_announcement
# module_manager / settings_manager → routers/clinic.py 로 이동 (v0.9.0).
# agent_router / agent_middleware / build_prompt_text / load_config /
# conversation_flow / youtube_generator / blog history GET helpers →
# routers/blog.py 로 이동 (v0.9.0 C1).
# generate_blog_stream / generate_image_prompts_stream / save_blog_entry /
# check_blog_limit / check_and_notify / log_usage → routers/blog.py 로 이동 (v0.9.0 C2).
from plan_guard import (
    get_effective_plan, resolve_effective_plan,
    check_prompt_copy_limit, _count_total_blogs, _count_total_prompt_copies,
    _FREE_BLOG_LIMIT, _PROMPT_COPY_LIMIT,
)


# TERMS_VERSION, APPLICANT_EXPIRY_DAYS → routers/auth.py 로 이동.
# include_router 이후 backward-compat alias 재할당 (아래 참조).

_error_logger = _logging.getLogger("cligent.errors")


def _log_error_to_file(request: Request, exc: Exception, user_id: str = "anonymous") -> None:
    """서버 에러를 data/error_logs/YYYY-MM-DD.jsonl에 기록"""
    try:
        error_dir = ROOT / "data" / "error_logs"
        error_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = _json.dumps({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "path": str(request.url.path),
            "method": request.method,
            "user": user_id,
            "error_type": type(exc).__name__,
            "error_msg": str(exc)[:500],
            "traceback": traceback.format_exc()[-2000:],
        }, ensure_ascii=False)
        with open(error_dir / f"{today}.jsonl", "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# IP 베타 신청 레이트 리밋 → routers/auth.py 로 이동 (_ip_apply_buckets,
# _IP_APPLY_WINDOW, _IP_APPLY_LIMIT, _check_ip_apply_limit).
# include_router 이후 _ip_apply_buckets backward-compat alias 재할당 (아래 참조).






# _check_ip_apply_limit, _ip_apply_buckets, _IP_APPLY_* → routers/auth.py 로 이동.


# 라우터 분할 후 단일 진실원 → src/dependencies.py
# main.py 의 기존 호출자를 위해 동일 이름으로 alias 유지 (점진적 분할 대응)
from dependencies import (
    is_admin_clinic as _is_admin_clinic,
    require_admin as _require_admin,
    require_admin_or_session as _require_admin_or_session,
    require_announce_admin as _require_announce_admin,
)


async def _midnight_scheduler() -> None:
    """매일 자정 5분 후 전날 데일리 리포트 자동 생성"""
    while True:
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            from daily_report import generate_daily_report
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            await asyncio.to_thread(generate_daily_report, yesterday)
            _error_logger.info("데일리 리포트 생성 완료: %s", yesterday)
        except Exception as e:
            _error_logger.error("데일리 리포트 생성 실패: %s", e)


async def _naver_check_scheduler() -> None:
    """30분마다 pending_checks 폴링 — 색인 확인 + 이메일 알림 발송"""
    while True:
        await asyncio.sleep(30 * 60)
        try:
            from naver_checker import run_pending_checks, get_unnotified, mark_notified
            from plan_notify import send_naver_found_email, send_naver_expired_email

            # 색인 확인된 항목 처리
            found_items = await asyncio.to_thread(run_pending_checks)
            for item in found_items:
                update_naver_url(item["blog_stat_id"], item["found_url"])

            # found 알림 미발송 항목 이메일 발송
            for item in await asyncio.to_thread(get_unnotified, "found"):
                try:
                    with __import__('db_manager').get_db() as conn:
                        row = conn.execute(
                            "SELECT u.email FROM users u "
                            "JOIN clinics c ON u.clinic_id = c.id "
                            "WHERE u.role = 'chief_director' LIMIT 1"
                        ).fetchone()
                    if row:
                        await asyncio.to_thread(
                            send_naver_found_email, row["email"], item["title"], item["found_url"]
                        )
                    mark_notified(item["id"])
                except Exception as exc:
                    _error_logger.warning("naver found 알림 실패 (id=%s): %s", item["id"], exc)

            # expired 알림 미발송 항목 이메일 발송
            for item in await asyncio.to_thread(get_unnotified, "expired"):
                try:
                    with __import__('db_manager').get_db() as conn:
                        row = conn.execute(
                            "SELECT u.email FROM users u "
                            "JOIN clinics c ON u.clinic_id = c.id "
                            "WHERE u.role = 'chief_director' LIMIT 1"
                        ).fetchone()
                    if row:
                        await asyncio.to_thread(
                            send_naver_expired_email, row["email"], item["title"]
                        )
                    mark_notified(item["id"])
                except Exception as exc:
                    _error_logger.warning("naver expired 알림 실패 (id=%s): %s", item["id"], exc)

        except Exception as e:
            _error_logger.error("_naver_check_scheduler 오류: %s", e)


async def _error_logs_purge_scheduler() -> None:
    """24시간마다 90일 초과 error_logs/{date}.jsonl 파일 자동 삭제."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            err_dir = ROOT / "data" / "error_logs"
            if not err_dir.exists():
                continue
            cutoff = (datetime.now(timezone.utc).date() - timedelta(days=90))
            removed = 0
            _date_re = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
            for p in err_dir.glob("*.jsonl"):
                if not _date_re.match(p.stem):
                    continue
                try:
                    file_date = datetime.fromisoformat(p.stem).date()
                except Exception:
                    continue
                if file_date < cutoff:
                    try:
                        p.unlink()
                        removed += 1
                    except Exception:
                        pass
            if removed > 0:
                _error_logger.info("error_logs: 90일 초과 %d개 파일 삭제", removed)
        except Exception as e:
            _error_logger.error("error_logs_purge_scheduler 오류: %s", e)


async def _login_history_purge_scheduler() -> None:
    """24시간마다 90일 초과 login_history 자동 삭제 (PIPA 준수)."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            from db_manager import get_db as _get_db
            with _get_db() as conn:
                # SQLite native datetime 비교 — created_at 형식 차이(공백 vs T) 회피
                cur = conn.execute(
                    "DELETE FROM login_history "
                    "WHERE datetime(created_at) < datetime('now', '-90 days')"
                )
                if cur.rowcount > 0:
                    _error_logger.info("login_history: 90일 초과 %d건 삭제", cur.rowcount)
        except Exception as e:
            _error_logger.error("login_history_purge_scheduler 오류: %s", e)


async def _blog_chat_session_purge_scheduler() -> None:
    """24시간마다 24h 이상 미활성 blog_chat_sessions 행 자동 삭제 (TTL).

    in-memory LRU와 DB 양쪽을 정리. blog_chat_state.cleanup_stale_sessions 위임.
    """
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            from blog_chat_state import cleanup_stale_sessions
            deleted = cleanup_stale_sessions(ttl_hours=24)
            if deleted > 0:
                _error_logger.info("blog_chat_sessions: TTL 24h 초과 %d건 삭제", deleted)
        except Exception as e:
            _error_logger.error("blog_chat_session_purge_scheduler 오류: %s", e)


async def _applicant_expiry_scheduler() -> None:
    """24시간마다 expires_at 지난 pending 신청자를 status='expired'로 자동 전환."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            from db_manager import get_db as _get_db
            with _get_db() as conn:
                cur = conn.execute(
                    """
                    UPDATE beta_applicants
                    SET status = 'expired'
                    WHERE status = 'pending'
                      AND expires_at IS NOT NULL
                      AND expires_at < ?
                    """,
                    (now_iso,),
                )
                if cur.rowcount > 0:
                    _error_logger.info("applicant_expiry: %d건 expired 처리", cur.rowcount)
        except Exception as e:
            _error_logger.error("applicant_expiry_scheduler 오류: %s", e)


async def _beta_reminder_scheduler() -> None:
    """E4: 6시간마다 72h 이상 미클릭 초대 신청자에게 리마인더 이메일 발송"""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
            from db_manager import get_db as _get_db
            from plan_notify import send_beta_reminder
            base_url = os.getenv("BASE_URL", "https://cligent.kr")

            with _get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT id, name, email, invite_token FROM beta_applicants
                    WHERE status = 'invited'
                      AND invited_at < ?
                      AND clicked_at IS NULL
                    """,
                    (cutoff,),
                ).fetchall()

            for row in rows:
                invite_url = f"{base_url}/onboard?token={row['invite_token']}"
                try:
                    await asyncio.to_thread(
                        send_beta_reminder, row["email"], row["name"], invite_url, row["id"],
                    )
                    _error_logger.info("beta E4 리마인더 발송: %s", row["email"])
                except Exception as exc:
                    _error_logger.warning("beta E4 리마인더 실패 (id=%s): %s", row["id"], exc)
        except Exception as e:
            _error_logger.error("beta_reminder_scheduler 오류: %s", e)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """서버 시작/종료 시 리소스 초기화 및 정리."""
    init_db()
    # 첫 공지 시드 (테이블이 비어 있을 때만)
    seed_first_announcement()
    # 공지 첨부 이미지 업로드 폴더 보장
    (ROOT / "static" / "uploads" / "announcements").mkdir(parents=True, exist_ok=True)
    # error_logs 폴더 보장
    (ROOT / "data" / "error_logs").mkdir(parents=True, exist_ok=True)
    # 개발 환경: 클리닉이 없으면 데모 클리닉 자동 생성
    if os.getenv("ENV", "dev") == "dev":
        clinic_id = seed_demo_clinic()
        seed_demo_owner(clinic_id)
    # 만료된 블로그 전문(全文) 자동 삭제 (30일 경과 항목)
    removed = purge_expired_texts()
    if removed:
        _logging.getLogger(__name__).info("blog_texts: 만료 항목 %d건 삭제", removed)
    # 데일리 리포트 자동 스케줄러
    _sched = asyncio.create_task(_midnight_scheduler())
    # E4 베타 리마인더 스케줄러 (6h 주기)
    _reminder_sched = asyncio.create_task(_beta_reminder_scheduler())
    # 신청 30일 미가입 자동 만료 (24h 주기)
    _expiry_sched = asyncio.create_task(_applicant_expiry_scheduler())
    # 로그인 이력 90일 초과 자동 삭제 (PIPA, 24h 주기)
    _login_purge_sched = asyncio.create_task(_login_history_purge_scheduler())
    # 에러 로그 90일 초과 자동 삭제 (24h 주기)
    _err_purge_sched = asyncio.create_task(_error_logs_purge_scheduler())
    # 네이버 발행 확인 스케줄러 (30분 주기)
    _naver_sched = asyncio.create_task(_naver_check_scheduler())
    # 블로그 챗 세션 TTL 24h 정리 (Step 1, 1D-4)
    _chat_purge_sched = asyncio.create_task(_blog_chat_session_purge_scheduler())
    yield
    _sched.cancel()
    _reminder_sched.cancel()
    _expiry_sched.cancel()
    _login_purge_sched.cancel()
    _err_purge_sched.cancel()
    _naver_sched.cancel()
    _chat_purge_sched.cancel()
    for task in (_sched, _reminder_sched, _expiry_sched, _login_purge_sched,
                 _err_purge_sched, _naver_sched, _chat_purge_sched):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Cligent", lifespan=lifespan)

# 모든 HTTP 요청 로깅 + request_id 부여 + 5xx → error_logs/{date}.jsonl 자동 기록
app.add_middleware(RequestLoggingMiddleware)

static_dir = ROOT / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ── 예외 핸들러 ──────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException):
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_exc_handler(request: Request, exc: RequestValidationError):
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _global_exc_handler(request: Request, exc: Exception):
    """캐치되지 않은 서버 에러: 로그 기록 후 500 반환"""
    user_id = "anonymous"
    try:
        from auth_manager import COOKIE_NAME, decode_token
        token = request.cookies.get(COOKIE_NAME)
        if token:
            payload = decode_token(token)
            user_id = str(payload.get("sub", "unknown"))
    except Exception:
        pass
    _log_error_to_file(request, exc, user_id)
    _error_logger.exception("Unhandled exception [%s %s] user=%s", request.method, request.url.path, user_id)
    return JSONResponse({"detail": "서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."}, status_code=500)


# ── 라우터 등록 (v0.9.0 분할) ─────────────────────────────────────
# main.py 4,000줄 → 도메인별 6개 라우터로 분할 진행 중
from routers import auth as _auth_router  # noqa: E402
from routers import clinic as _clinic_router  # noqa: E402
from routers import billing as _billing_router  # noqa: E402
from routers import blog as _blog_router  # noqa: E402
from routers import admin as _admin_router  # noqa: E402
from routers import dashboard as _dashboard_router  # noqa: E402

app.include_router(_auth_router.router)
app.include_router(_clinic_router.router)
app.include_router(_billing_router.router)
app.include_router(_blog_router.router)
# admin 라우터는 dashboard 보다 먼저 include — /announcements/new 가
# dashboard 의 /announcements/{ann_id} (int path) 보다 먼저 매칭되도록.
app.include_router(_admin_router.router)
app.include_router(_dashboard_router.router)

# 기존 호출자 backward-compat: tests/test_beta_apply 가 main 에서 import.
_ip_apply_buckets = _auth_router._ip_apply_buckets
TERMS_VERSION = _auth_router.TERMS_VERSION
APPLICANT_EXPIRY_DAYS = _auth_router.APPLICANT_EXPIRY_DAYS

# tests/test_agent_api 가 main 에서 monkeypatch / import 함 (v0.9.0 C1).
_create_anthropic_client = _blog_router._create_anthropic_client
_check_rate_limit = _blog_router._check_rate_limit
_rate_buckets = _blog_router._rate_buckets


# ── 페이지 라우트 ─────────────────────────────────────────────────
# /login, /onboard, /terms, /privacy, /business, /forgot-password,
# /api/auth/forgot-password → routers/auth.py 로 이동 (v0.9.0).

# _NO_CACHE alias 는 routers/dashboard.py·blog.py 가 직접 import (v0.9.0 / 6/6).




# /dashboard, /help → routers/dashboard.py 로 이동 (v0.9.0 / 6/6).
# /settings, /settings/setup → routers/clinic.py 로 이동.
# /, /robots.txt, /sitemap.xml → routers/auth.py 로 이동.


@app.get("/api/version")
async def api_version() -> JSONResponse:
    """현재 배포 버전 — 어드민·푸터 표시용. 공개 엔드포인트."""
    return JSONResponse({"version": APP_VERSION})


# ── 인증 API → routers/auth.py 로 이동 (v0.9.0).
# ── 직원 관리·한의원 프로필·AI 설정·블로그 설정·RBAC·모듈
#    → routers/clinic.py 로 이동 (v0.9.0).
# ── 플랜 & 사용량 조회 → routers/billing.py 로 이동 (v0.9.0).
# ── 블로그 생성·SSE·blog-chat → routers/blog.py 로 이동 (v0.9.0 C1·C2).
# ── /api/feedback + 헬퍼 → routers/dashboard.py 로 이동 (v0.9.0 / 6/6).
# ── 어드민 페이지·API + 공지 작성·OpenAI 키
#    → routers/admin.py 로 이동 (v0.9.0 / 6/6 / 2026-05-02).

# ═══════════════════════════════════════════════════════════════════
# 이미지 생성 라우트 (Phase 4) 는 별도 라우터 분리 예정.
# 응답 정책: 401 로그인 필요 / 403 클리닉 격리 / 404 세션 없음 /
#            429 한도 초과(종량제 안내) / 502 OpenAI 오류.
# ═══════════════════════════════════════════════════════════════════
