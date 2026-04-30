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
from typing import Generator, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
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

init_observability()

import anthropic
from collections import defaultdict
from time import time as _time_now

# ── API 키 암호화/복호화 (Fernet, SECRET_KEY 파생) ─────────────────
def _get_fernet():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"cligent_v1", iterations=100_000)
    raw = kdf.derive(_secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))

def _encrypt_key(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()

def _decrypt_key(enc: str) -> str:
    return _get_fernet().decrypt(enc.encode()).decode()

def _mask_key(plain: str) -> str:
    if len(plain) <= 8:
        return "****"
    return plain[:10] + "****" + plain[-4:]

from auth_manager import (
    COOKIE_NAME,
    authenticate_user,
    change_password,
    complete_onboarding,
    create_access_token,
    create_invite,
    create_reinvite,
    decode_token,
    get_current_user,
    verify_invite,
)
from blog_generator import generate_blog_stream
from youtube_generator import generate_youtube_stream
from blog_history import get_blog_stats, purge_expired_texts, save_blog_entry, get_history_list, get_blog_text, update_naver_url
from blog_generator import build_prompt_text
from config_loader import load_config, save_blog_config, save_prompt
from conversation_flow import generate_conversation_flow
from db_manager import create_clinic, init_db, seed_demo_clinic, seed_demo_owner, seed_first_announcement
from image_prompt_generator import generate_image_prompts_stream
from module_manager import (
    get_allowed_modules,
    get_module_info,
    role_has_access,
    save_staff_permissions,
)
from settings_manager import get_setup_wizard_data, save_wizard_result
from agent_router import AgentRouter
from agent_middleware import AgentMiddleware
from plan_guard import (
    check_blog_limit, get_effective_plan, resolve_effective_plan,
    check_prompt_copy_limit, _count_total_blogs, _count_total_prompt_copies,
    _FREE_BLOG_LIMIT, _PROMPT_COPY_LIMIT,
)
from plan_notify import check_and_notify
from usage_tracker import log_usage

agent_router = AgentRouter()
agent_middleware = AgentMiddleware()

# 신청 폼 약관 버전 (개인정보처리방침 변경 시 갱신, applicant 동의 시점 추적용)
TERMS_VERSION = "v1.0-2026-04-29"
APPLICANT_EXPIRY_DAYS = 30

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


# 분당 요청 수 추적 (clinic_id → [timestamp, ...])
_rate_buckets: dict = defaultdict(list)
_RATE_LIMIT = 60  # 분당 최대 요청 수

# IP 기반 레이트 리밋 (베타 신청 공개 엔드포인트 전용)
# 5분 창 안에 최대 3회 허용
_ip_apply_buckets: dict = defaultdict(list)
_IP_APPLY_WINDOW = 300   # 5분 (초)
_IP_APPLY_LIMIT  = 3


def _create_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)


def _check_rate_limit(clinic_id: str) -> bool:
    """True = 허용, False = 초과 (60 req/min per clinic)"""
    now = _time_now()
    bucket = _rate_buckets[clinic_id]
    _rate_buckets[clinic_id] = [t for t in bucket if now - t < 60]
    if len(_rate_buckets[clinic_id]) >= _RATE_LIMIT:
        return False
    _rate_buckets[clinic_id].append(now)
    return True


def _check_ip_apply_limit(ip: str) -> bool:
    """True = 허용, False = 초과 (5분 창 3회 per IP, 베타 신청 전용)"""
    now = _time_now()
    _ip_apply_buckets[ip] = [t for t in _ip_apply_buckets[ip] if now - t < _IP_APPLY_WINDOW]
    if len(_ip_apply_buckets[ip]) >= _IP_APPLY_LIMIT:
        return False
    _ip_apply_buckets[ip].append(now)
    return True


def _require_admin(request: Request) -> None:
    """Bearer <ADMIN_SECRET> 검증. 실패 시 HTTPException 발생."""
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret:
        raise HTTPException(status_code=403, detail="관리자 기능이 비활성화되어 있습니다.")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != admin_secret:
        raise HTTPException(status_code=401, detail="인증 실패")


def _require_admin_or_session(request: Request) -> None:
    """세션 쿠키(chief_director + ADMIN_CLINIC_ID) 또는 ADMIN_SECRET Bearer 둘 중 하나 통과 시 OK.
    브라우저 진입에는 세션, CLI 스크립트에는 Bearer를 사용.
    """
    # 1) ADMIN_SECRET Bearer 우선
    admin_secret = os.getenv("ADMIN_SECRET", "")
    auth = request.headers.get("Authorization", "")
    if admin_secret and auth.startswith("Bearer ") and auth[7:] == admin_secret:
        return
    # 2) 세션 쿠키 — chief_director + ADMIN_CLINIC_ID
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")
    try:
        payload = decode_token(token)
        user_id = int(payload.get("sub", 0))
        from db_manager import get_db as _g
        with _g() as conn:
            row = conn.execute(
                "SELECT id, role, clinic_id FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="세션이 만료되었습니다.")
        admin_cid = os.getenv("ADMIN_CLINIC_ID", "1")
        if row["role"] != "chief_director" or int(row["clinic_id"]) != int(admin_cid):
            raise HTTPException(status_code=403, detail="관리자 권한이 없습니다.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="세션 검증 실패")


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
            for p in err_dir.glob("*.jsonl"):
                if not _DATE_RE.match(p.stem):
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
    yield
    _sched.cancel()
    _reminder_sched.cancel()
    _expiry_sched.cancel()
    _login_purge_sched.cancel()
    _err_purge_sched.cancel()
    _naver_sched.cancel()
    for task in (_sched, _reminder_sched, _expiry_sched, _login_purge_sched, _err_purge_sched, _naver_sched):
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


# ── 페이지 라우트 ─────────────────────────────────────────────────

@app.get("/login")
async def login_page():
    return FileResponse(ROOT / "templates" / "login.html")


@app.get("/onboard")
async def onboard_page():
    return FileResponse(ROOT / "templates" / "onboard.html")


# ── 약관·방침·사업자정보 (B3, 2026-04-27) ───────────────────────
@app.get("/terms")
async def terms_page():
    """이용약관"""
    return FileResponse(ROOT / "templates" / "legal" / "terms.html")


@app.get("/privacy")
async def privacy_page():
    """개인정보처리방침"""
    return FileResponse(ROOT / "templates" / "legal" / "privacy.html")


@app.get("/business")
async def business_page():
    """사업자정보"""
    return FileResponse(ROOT / "templates" / "legal" / "business.html")


# ── 비밀번호 찾기 (자가 재설정, 2026-04-27) ───────────────────
@app.get("/forgot-password")
async def forgot_password_page():
    """비밀번호 찾기 페이지 — 인증 불필요"""
    return FileResponse(ROOT / "templates" / "forgot_password.html", headers=_NO_CACHE)


_FORGOT_PW_RATE_LIMIT_SEC = 60
_forgot_pw_last_request: dict[str, float] = {}


@app.post("/api/auth/forgot-password")
async def api_forgot_password(request: Request):
    """비밀번호 재설정 토큰 발급 + 이메일 발송.

    보안:
    - 등록 여부와 무관하게 동일 응답(200) — 이메일 enumeration 방지
    - 같은 이메일 60초당 1회 제한 (429)
    - 토큰은 기존 invite 흐름 재사용 (72시간 유효)
    """
    body = await request.json()
    email = (body.get("email") or "").strip().lower()

    if not email or "@" not in email or len(email) > 200:
        # 형식 오류라도 200 (enumeration 방지)
        return JSONResponse({"ok": True}, status_code=200)

    # Rate limiting (in-memory, single-worker 가정)
    now = _time_now()
    last = _forgot_pw_last_request.get(email)
    if last and (now - last) < _FORGOT_PW_RATE_LIMIT_SEC:
        wait = int(_FORGOT_PW_RATE_LIMIT_SEC - (now - last)) or 1
        return JSONResponse(
            {"detail": f"잠시 후 다시 시도해주세요 ({wait}초)."},
            status_code=429,
        )
    _forgot_pw_last_request[email] = now

    # 사용자 조회
    with __import__('db_manager').get_db() as conn:
        user = conn.execute(
            "SELECT id, role, clinic_id FROM users WHERE email = ? AND is_active = 1",
            (email,),
        ).fetchone()

    # 등록되지 않은 이메일은 silent 200
    if not user:
        return JSONResponse({"ok": True}, status_code=200)

    # 토큰 생성 (기존 reinvite 재사용)
    try:
        token = create_reinvite(
            clinic_id=int(user["clinic_id"]),
            email=email,
            role=user["role"],
            created_by=int(user["id"]),
        )
    except Exception:
        _error_logger.exception("forgot-password 토큰 생성 실패 email=%s", email)
        return JSONResponse({"ok": True}, status_code=200)

    # 이메일 발송 (실패해도 사용자는 동일 응답 받음 — 디버깅은 로그로)
    base_url = os.getenv("BASE_URL", str(request.base_url).rstrip("/"))
    reset_url = f"{base_url}/onboard?token={token}"

    html = f"""<!DOCTYPE html>
<html><body style="font-family:'Apple SD Gothic Neo','Pretendard',sans-serif;color:#1a1a18;line-height:1.6;">
<div style="max-width:560px;margin:24px auto;padding:32px;border:1px solid #e2e0d8;border-radius:12px;">
  <div style="font-size:20px;font-weight:800;color:#064e3b;margin-bottom:16px;">Cligent</div>
  <h2 style="font-size:18px;margin:0 0 12px;">비밀번호 재설정 링크입니다</h2>
  <p>안녕하세요. Cligent 비밀번호 재설정을 요청하셨습니다.</p>
  <p>아래 버튼을 누르시면 새 비밀번호를 설정할 수 있습니다.</p>
  <p style="margin:24px 0;">
    <a href="{reset_url}"
       style="display:inline-block;background:#064e3b;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;">
      비밀번호 재설정
    </a>
  </p>
  <p style="font-size:13px;color:#6b6960;">
    버튼이 작동하지 않으면 아래 주소를 복사해 브라우저에 붙여넣어 주세요:<br>
    <span style="word-break:break-all;">{reset_url}</span>
  </p>
  <hr style="border:none;border-top:1px solid #e2e0d8;margin:24px 0;">
  <p style="font-size:13px;color:#6b6960;">
    이 링크는 <strong>72시간</strong> 동안 유효합니다.<br>
    본인이 요청하지 않으셨다면 이 메일을 무시하셔도 됩니다.
  </p>
</div>
</body></html>
"""

    try:
        from plan_notify import _send_smtp
        _send_smtp(email, "[Cligent] 비밀번호 재설정 링크", html)
    except Exception:
        _error_logger.exception("forgot-password 이메일 발송 실패 email=%s", email)

    return JSONResponse({"ok": True}, status_code=200)


_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/blog")
async def blog_page(request: Request):
    """블로그 생성기 — 인증 필요"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "index.html", headers=_NO_CACHE)


@app.get("/app")
async def app_shell(request: Request):
    """앱 쉘 — 사이드바 고정 레이아웃 (iframe으로 콘텐츠 로드)"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "app.html", headers=_NO_CACHE)


@app.get("/dashboard")
async def dashboard_page(request: Request):
    """대시보드 — app.html iframe 안에서 로드되는 직접 서빙 라우트.
    `/` 가 인증 시 `/app`으로 리다이렉트하므로, iframe 무한 재귀 방지를 위해 별도 경로 사용."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "dashboard.html", headers=_NO_CACHE)


@app.get("/blog")
async def blog_page(request: Request):
    """블로그 생성기"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "index.html", headers=_NO_CACHE)


@app.get("/settings")
async def settings_page(request: Request):
    """설정 페이지"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "settings.html", headers=_NO_CACHE)


@app.get("/help")
async def help_page(request: Request):
    """도움말 페이지"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "help.html", headers=_NO_CACHE)


@app.get("/settings/setup")
async def settings_setup(request: Request):
    """RBAC 초기 설정 위자드 — 대표원장 전용"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")

    template_path = ROOT / "templates" / "settings_setup.html"
    html = template_path.read_text(encoding="utf-8")
    wizard_data = get_setup_wizard_data()
    html = html.replace("__RBAC_DATA__", _json.dumps(wizard_data, ensure_ascii=False))
    return HTMLResponse(content=html, headers=_NO_CACHE)


@app.get("/")
async def root(request: Request):
    """루트 — 인증 시 반응형 /app, 미인증 시 마케팅 랜딩 (B4, 2026-04-27)."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return RedirectResponse("/app")
    # 미인증 → 마케팅 랜딩 페이지
    return FileResponse(ROOT / "templates" / "landing.html")


# ── SEO: robots.txt / sitemap.xml ─────────────────────────────────

@app.get("/robots.txt")
async def robots_txt() -> Response:
    """검색엔진 크롤러용 robots.txt — 공개 페이지만 허용, 앱·관리자 차단."""
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /admin\n"
        "Disallow: /admin/\n"
        "Disallow: /app\n"
        "Disallow: /dashboard\n"
        "Disallow: /blog\n"
        "Disallow: /chat\n"
        "Disallow: /settings\n"
        "Disallow: /onboard\n"
        "Disallow: /login\n"
        "Disallow: /forgot-password\n"
        "Disallow: /youtube\n"
        "Disallow: /help\n"
        "\n"
        "Sitemap: https://cligent.kr/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
async def sitemap_xml() -> Response:
    """검색엔진 색인용 사이트맵 — 공개 페이지 4개."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    urls = [
        ("https://cligent.kr/",         "weekly",  "1.0"),
        ("https://cligent.kr/terms",    "monthly", "0.3"),
        ("https://cligent.kr/privacy",  "monthly", "0.3"),
        ("https://cligent.kr/business", "monthly", "0.3"),
    ]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, prio in urls:
        parts.append(
            f"  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{prio}</priority>\n"
            f"  </url>"
        )
    parts.append("</urlset>")
    body = "\n".join(parts) + "\n"
    return Response(content=body, media_type="application/xml; charset=utf-8")


# ── 인증 API ──────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def api_login(request: Request):
    """
    로그인 — httpOnly JWT 쿠키 발급

    요청: {"email": "...", "password": "..."}
    응답: {"ok": true, "must_change_pw": false}
    로그인 시도(성공/실패) 모두 login_history에 기록 (PIPA 90일 보존).
    """
    body = await request.json()
    email = body.get("email", "").strip()
    password = body.get("password", "")

    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    user, reason = authenticate_user(email, password)

    from auth_manager import record_login_attempt
    if not user:
        record_login_attempt(
            user_id=None, email=email, clinic_id=None,
            ip=ip, user_agent=user_agent,
            success=False, failure_reason=reason,
        )
        return JSONResponse(
            {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."},
            status_code=401,
        )

    record_login_attempt(
        user_id=user["id"], email=user["email"], clinic_id=user["clinic_id"],
        ip=ip, user_agent=user_agent,
        success=True,
    )

    token = create_access_token(user["id"], user["clinic_id"], user["role"])
    response = JSONResponse({"ok": True, "must_change_pw": bool(user["must_change_pw"])})
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ENV", "dev") != "dev",  # 프로덕션에서만 Secure
        max_age=8 * 3600,
    )
    return response


@app.post("/api/auth/logout")
async def api_logout():
    """로그아웃 — JWT 쿠키 삭제"""
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        httponly=True,
        samesite="lax",
        max_age=0,
        expires=0,
        path="/",
    )
    return response


def _is_admin_clinic(user: dict) -> bool:
    """베타 정책: ADMIN_CLINIC_ID와 일치하는 클리닉만 직원 초대·관리 기능 허용.

    정식 서비스 출시 시 본 함수를 제거하거나 항상 True 반환으로 전환.
    """
    admin_cid = os.getenv("ADMIN_CLINIC_ID", "1")
    try:
        return int(user.get("clinic_id", 0)) == int(admin_cid)
    except (TypeError, ValueError):
        return False


@app.get("/api/auth/me")
async def api_me(user: dict = Depends(get_current_user)):
    """현재 로그인 사용자 정보"""
    with __import__('db_manager').get_db() as conn:
        clinic = conn.execute(
            "SELECT api_key_configured, onboarding_started_at FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    api_key_configured = bool(clinic["api_key_configured"]) if clinic else False
    is_admin = _is_admin_clinic(user) and user["role"] == "chief_director"
    return JSONResponse({
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "clinic_id": user["clinic_id"],
        "must_change_pw": bool(user["must_change_pw"]),
        "api_key_configured": api_key_configured,
        "can_invite": _is_admin_clinic(user),
        "is_admin": is_admin,
    })


@app.get("/api/auth/login-history")
async def api_my_login_history(user: dict = Depends(get_current_user)):
    """현재 사용자 본인의 로그인 이력 (PIPA 권리 행사). 최근 90일 최대 50건."""
    from db_manager import get_db as _get_db
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, ip, user_agent, success, failure_reason, created_at "
            "FROM login_history "
            "WHERE user_id = ? "
            "  AND datetime(created_at) >= datetime('now', '-90 days') "
            "ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    return JSONResponse({"rows": [dict(r) for r in rows]})


@app.post("/api/auth/change-password")
async def api_change_password(request: Request, user: dict = Depends(get_current_user)):
    """비밀번호 변경"""
    body = await request.json()
    new_pw = body.get("new_password", "")
    if len(new_pw) < 8:
        return JSONResponse({"detail": "비밀번호는 8자 이상이어야 합니다."}, status_code=400)
    change_password(user["id"], new_pw)
    return JSONResponse({"ok": True})


@app.post("/api/auth/invite")
async def api_create_invite(request: Request, user: dict = Depends(get_current_user)):
    """
    직원 초대 토큰 생성 — director 이상 전용

    요청: {"email": "staff@example.com", "role": "team_member"}
    응답: {"token": "...", "invite_url": "http://localhost:8000/onboard?token=..."}
    """
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "초대 권한이 없습니다."}, status_code=403)

    # 베타 정책: 본인 클리닉 외 직원 초대 차단 (이용약관 제2조·제4조)
    if not _is_admin_clinic(user):
        return JSONResponse(
            {"detail": "베타 단계에서는 직원 초대 기능이 일시 비활성화되어 있습니다. 정식 서비스 출시 이후 단계적으로 지원됩니다."},
            status_code=403,
        )

    body = await request.json()
    email = body.get("email", "").strip()
    role = body.get("role", "team_member")

    if not email:
        return JSONResponse({"detail": "이메일을 입력해주세요."}, status_code=400)

    try:
        token = create_invite(user["clinic_id"], email, role, user["id"])
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)

    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/onboard?token={token}"
    return JSONResponse({"token": token, "invite_url": invite_url})


@app.get("/api/auth/invite/verify")
async def api_verify_invite(token: str):
    """초대 토큰 유효성 확인 (온보딩 페이지 초기 로드용)"""
    invite = verify_invite(token)
    if not invite:
        return JSONResponse({"valid": False, "detail": "유효하지 않거나 만료된 초대 링크입니다."}, status_code=400)
    return JSONResponse({"valid": True, "email": invite["email"], "role": invite["role"]})


@app.post("/api/auth/onboard")
async def api_onboard(request: Request):
    """
    온보딩 완료 — 비밀번호 설정 + JWT 발급

    요청: {"token": "...", "password": "..."}
    """
    body = await request.json()
    token = body.get("token", "")
    password = body.get("password", "")

    if len(password) < 8:
        return JSONResponse({"detail": "비밀번호는 8자 이상이어야 합니다."}, status_code=400)

    try:
        user = complete_onboarding(token, password)
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)

    jwt_token = create_access_token(user["id"], user["clinic_id"], user["role"])
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ENV", "dev") != "dev",
        max_age=8 * 3600,
    )
    return response


# ── RBAC 설정 API ─────────────────────────────────────────────────

@app.get("/api/settings/staff")
async def get_staff_list(user: dict = Depends(get_current_user)):
    """설정 페이지용 직원 목록 — DB에서 직접 조회"""
    with __import__('db_manager').get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, role, is_active FROM users WHERE clinic_id = ? ORDER BY id",
            (user["clinic_id"],),
        ).fetchall()
    staff = [dict(r) for r in rows]
    # 각 직원의 모듈 권한 읽기
    import json as _j
    staff_path = ROOT / "data" / "staff_permissions.json"
    perms = _j.loads(staff_path.read_text()) if staff_path.exists() else {}
    for s in staff:
        key = str(s["id"])
        s["modules"] = perms.get(key, {}).get("modules", [])
        s["name"] = perms.get(key, {}).get("name", s["email"].split("@")[0])
    return JSONResponse({"staff": staff})


@app.post("/api/settings/staff/modules")
async def save_staff_modules(request: Request, user: dict = Depends(get_current_user)):
    """직원 모듈 권한 즉시 저장 (토글 자동저장용)"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)
    body = await request.json()
    staff_id = str(body.get("staff_id", "")).strip()
    modules = body.get("modules", [])
    if not staff_id:
        return JSONResponse({"detail": "staff_id가 필요합니다."}, status_code=400)
    # 이름 조회
    with __import__('db_manager').get_db() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (staff_id,)).fetchone()
    name = row["email"].split("@")[0] if row else staff_id
    result = save_staff_permissions(staff_id, name, modules)
    return JSONResponse({"ok": True, **result})


@app.patch("/api/settings/staff/{staff_id}")
async def update_staff(staff_id: str, request: Request, user: dict = Depends(get_current_user)):
    """직원 이름·역할 변경 — director 이상 전용"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    body = await request.json()
    new_name = body.get("name", "").strip()
    new_role = body.get("role", "").strip()

    VALID_ROLES = {"team_member", "team_leader", "manager", "director", "chief_director"}

    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

        # chief_director 역할은 변경 금지
        if row["role"] == "chief_director" and new_role and new_role != "chief_director":
            return JSONResponse({"detail": "대표원장 역할은 변경할 수 없습니다."}, status_code=403)

        if new_role:
            if new_role not in VALID_ROLES:
                return JSONResponse({"detail": "유효하지 않은 역할입니다."}, status_code=400)
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, staff_id))

    if new_name:
        import json as _j
        staff_path = ROOT / "data" / "staff_permissions.json"
        perms = _j.loads(staff_path.read_text()) if staff_path.exists() else {}
        if staff_id not in perms:
            perms[staff_id] = {"modules": []}
        perms[staff_id]["name"] = new_name
        staff_path.write_text(_j.dumps(perms, ensure_ascii=False, indent=2))

    return JSONResponse({"ok": True})


@app.post("/api/settings/staff/{staff_id}/reinvite")
async def reinvite_staff(staff_id: str, request: Request, user: dict = Depends(get_current_user)):
    """비밀번호 재설정 링크 생성 — director 이상 전용"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    # 베타 정책: 본인 클리닉 외 직원 관리 기능 차단
    if not _is_admin_clinic(user):
        return JSONResponse(
            {"detail": "베타 단계에서는 직원 관리 기능이 일시 비활성화되어 있습니다."},
            status_code=403,
        )

    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE id = ? AND clinic_id = ? AND is_active = 1",
            (staff_id, user["clinic_id"]),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

    token = create_reinvite(
        clinic_id=user["clinic_id"],
        email=row["email"],
        role=row["role"],
        created_by=user["id"],
    )
    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/onboard?token={token}"
    return JSONResponse({"ok": True, "invite_url": invite_url})


@app.delete("/api/settings/staff/{staff_id}")
async def deactivate_staff(staff_id: str, user: dict = Depends(get_current_user)):
    """직원 비활성화 (소프트 딜리트) — director 이상 전용"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)
        if row["role"] == "chief_director":
            return JSONResponse({"detail": "대표원장은 비활성화할 수 없습니다."}, status_code=403)
        if str(staff_id) == str(user["id"]):
            return JSONResponse({"detail": "본인 계정은 비활성화할 수 없습니다."}, status_code=403)

        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (staff_id,))

    return JSONResponse({"ok": True})


@app.post("/api/settings/staff/{staff_id}/activate")
async def activate_staff(staff_id: str, user: dict = Depends(get_current_user)):
    """비활성화된 직원 재활성화 — director 이상 전용"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (staff_id,))

    return JSONResponse({"ok": True})


@app.get("/api/settings/clinic/profile")
async def get_clinic_profile(user: dict = Depends(get_current_user)):
    """한의원 프로필 조회 — 인증된 사용자라면 누구나 조회 가능"""
    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT name, phone, address, specialty, hours, intro, blog_features, naver_blog_id FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "한의원 정보를 찾을 수 없습니다."}, status_code=404)
    import json as _j
    hours = None
    try:
        hours = _j.loads(row["hours"]) if row["hours"] else None
    except Exception:
        hours = None
    return JSONResponse({
        "name": row["name"] or "",
        "phone": row["phone"] or "",
        "address": row["address"] or "",
        "specialty": row["specialty"] or "",
        "hours": hours,
        "intro": row["intro"] or "",
        "blog_features": row["blog_features"] or "",
        "naver_blog_id": row["naver_blog_id"] or "",
    })


@app.post("/api/settings/clinic/profile")
async def save_clinic_profile(request: Request, user: dict = Depends(get_current_user)):
    """한의원 프로필 저장 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()
    import json as _j

    name = body.get("name", "").strip()
    phone = body.get("phone", "").strip()
    address = body.get("address", "").strip()
    specialty = body.get("specialty", "").strip()
    hours = body.get("hours")  # dict or None
    intro = body.get("intro", "").strip()
    blog_features = body.get("blog_features", "").strip()
    naver_blog_id = body.get("naver_blog_id", "").strip()

    if not name:
        return JSONResponse({"detail": "한의원 이름은 필수입니다."}, status_code=400)

    hours_json = _j.dumps(hours, ensure_ascii=False) if hours else None

    with __import__('db_manager').get_db() as conn:
        conn.execute(
            "UPDATE clinics SET name=?, phone=?, address=?, specialty=?, hours=?, intro=?, blog_features=?, naver_blog_id=? WHERE id=?",
            (name, phone or None, address or None, specialty or None, hours_json, intro or None, blog_features or None, naver_blog_id or None, user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


_VALID_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
}

@app.get("/api/settings/clinic/ai")
async def get_clinic_ai(user: dict = Depends(get_current_user)):
    """AI 설정 조회 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 접근할 수 있습니다."}, status_code=403)
    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT model, monthly_budget_krw, api_key_enc FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "한의원 정보를 찾을 수 없습니다."}, status_code=404)

    # DB에 API 키가 없으면 .env 키 사용 여부를 표시
    api_key_set = bool(row["api_key_enc"])
    env_key = os.getenv("ANTHROPIC_API_KEY", "")
    api_key_masked = ""
    if api_key_set:
        try:
            plain = _decrypt_key(row["api_key_enc"])
            api_key_masked = _mask_key(plain)
        except Exception:
            api_key_masked = "복호화 오류"
    elif env_key:
        api_key_masked = _mask_key(env_key) + " (.env)"

    return JSONResponse({
        "model": row["model"] or "claude-sonnet-4-6",
        "monthly_budget_krw": row["monthly_budget_krw"] or 10000,
        "api_key_masked": api_key_masked,
        "api_key_source": "db" if api_key_set else ("env" if env_key else "none"),
    })


@app.post("/api/settings/clinic/ai")
async def save_clinic_ai(request: Request, user: dict = Depends(get_current_user)):
    """AI 설정 저장 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()

    model = body.get("model", "").strip()
    budget = body.get("monthly_budget_krw")
    api_key_new = body.get("api_key", "").strip()  # 빈 문자열이면 기존 유지

    if model and model not in _VALID_MODELS:
        return JSONResponse({"detail": "지원하지 않는 모델입니다."}, status_code=400)
    if budget is not None:
        try:
            budget = int(budget)
            if budget < 0:
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse({"detail": "예산은 0 이상의 정수여야 합니다."}, status_code=400)

    api_key_enc = None
    clear_key = body.get("clear_key", False)  # 명시적 키 삭제 요청

    if api_key_new:
        if not api_key_new.startswith("sk-ant-"):
            return JSONResponse({"detail": "올바른 Anthropic API 키 형식이 아닙니다. (sk-ant- 로 시작해야 함)"}, status_code=400)
        api_key_enc = _encrypt_key(api_key_new)

    with __import__('db_manager').get_db() as conn:
        if api_key_enc:
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw), api_key_enc=?, api_key_configured=1 WHERE id=?",
                (model, user["clinic_id"], budget, api_key_enc, user["clinic_id"]),
            )
        elif clear_key:
            # 키 명시적 삭제 시 api_key_configured 리셋
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw), api_key_enc=NULL, api_key_configured=0 WHERE id=?",
                (model, user["clinic_id"], budget, user["clinic_id"]),
            )
        else:
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw) WHERE id=?",
                (model, user["clinic_id"], budget, user["clinic_id"]),
            )
    return JSONResponse({"ok": True})


@app.post("/api/settings/clinic/ai/validate")
async def validate_clinic_ai_key(request: Request, user: dict = Depends(get_current_user)):
    """
    API 키 유효성 검증 — 온보딩 위자드용.
    실제 Anthropic API를 호출해 키가 유효한지 확인한다.
    chief_director만 호출 가능 (AI 설정 권한과 동일).
    """
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 API 키를 설정할 수 있습니다."}, status_code=403)

    body = await request.json()
    api_key = body.get("api_key", "").strip()

    if not api_key:
        return JSONResponse({"detail": "API 키를 입력해주세요."}, status_code=400)
    if not api_key.startswith("sk-ant-"):
        return JSONResponse({"detail": "올바른 Claude API 키 형식이 아닙니다. (sk-ant- 로 시작해야 함)"}, status_code=400)

    import anthropic
    import httpx
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=8.0)
        # models.list()는 가장 저렴한 검증용 호출
        client.models.list(limit=1)
        return JSONResponse({"ok": True})
    except anthropic.AuthenticationError:
        return JSONResponse({"detail": "유효하지 않은 API 키입니다. 키를 다시 확인해주세요."}, status_code=401)
    except anthropic.RateLimitError:
        return JSONResponse({"detail": "잠시 후 다시 시도해주세요. (요청 한도 초과)"}, status_code=429)
    except (anthropic.APIConnectionError, httpx.TimeoutException):
        return JSONResponse({"detail": "Anthropic 서버에 연결할 수 없습니다. 인터넷 연결을 확인해주세요."}, status_code=503)
    except Exception:
        return JSONResponse({"detail": "키 검증 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}, status_code=500)


@app.post("/api/settings/clinic/ai/onboarding-start")
async def mark_onboarding_start(user: dict = Depends(get_current_user)):
    """온보딩 위자드 첫 표시 시각 기록 (첫 블로그까지 시간 측정용)"""
    from datetime import datetime, timezone
    with __import__('db_manager').get_db() as conn:
        conn.execute(
            "UPDATE clinics SET onboarding_started_at = COALESCE(onboarding_started_at, ?) WHERE id = ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"), user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


@app.get("/api/settings/blog")
async def get_blog_settings(user: dict = Depends(get_current_user)):
    """블로그 설정 조회 — director 이상"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 접근할 수 있습니다."}, status_code=403)
    cfg = load_config()
    return JSONResponse({"flow": cfg.get("flow", {}), "blog": cfg.get("blog", {})})


@app.post("/api/settings/blog")
async def save_blog_settings(request: Request, user: dict = Depends(get_current_user)):
    """블로그 설정 저장 — director 이상"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()

    flow = body.get("flow", {})
    blog = body.get("blog", {})

    # 유효성 검사
    if "questions_count" in flow:
        qc = int(flow["questions_count"])
        if not (1 <= qc <= 5):
            return JSONResponse({"detail": "질문 개수는 1~5 사이여야 합니다."}, status_code=400)
        flow["questions_count"] = qc
    if "questions_enabled" in flow:
        flow["questions_enabled"] = bool(flow["questions_enabled"])

    if "min_chars" in blog:
        blog["min_chars"] = int(blog["min_chars"])
    if "max_chars" in blog:
        blog["max_chars"] = int(blog["max_chars"])
    if "min_chars" in blog and "max_chars" in blog:
        if blog["min_chars"] >= blog["max_chars"]:
            return JSONResponse({"detail": "최소 글자 수는 최대 글자 수보다 작아야 합니다."}, status_code=400)

    VALID_TONES = {"전문적", "친근한", "설명적"}
    if "tone" in blog and blog["tone"] not in VALID_TONES:
        return JSONResponse({"detail": f"톤은 {', '.join(VALID_TONES)} 중 하나여야 합니다."}, status_code=400)

    save_blog_config(flow, blog)
    return JSONResponse({"ok": True})


@app.get("/api/settings/blog/prompt")
async def get_blog_prompt(user: dict = Depends(get_current_user)):
    """블로그 프롬프트 파일 내용 조회 — director 이상"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 접근할 수 있습니다."}, status_code=403)
    from config_loader import load_prompt
    content = load_prompt("blog")
    return JSONResponse({"content": content})


@app.post("/api/settings/blog/prompt")
async def save_blog_prompt(request: Request, user: dict = Depends(get_current_user)):
    """블로그 프롬프트 파일 저장 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 프롬프트를 수정할 수 있습니다."}, status_code=403)
    body = await request.json()
    content = body.get("content", "")
    if not content.strip():
        return JSONResponse({"detail": "프롬프트 내용이 비어 있습니다."}, status_code=400)
    save_prompt("blog", content)
    return JSONResponse({"ok": True})


@app.post("/api/settings/blog/prompt/reset")
async def reset_blog_prompt(user: dict = Depends(get_current_user)):
    """블로그 프롬프트를 기본값(blog.default.txt)으로 초기화 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 프롬프트를 초기화할 수 있습니다."}, status_code=403)
    default_path = ROOT / "prompts" / "blog.default.txt"
    if not default_path.exists():
        return JSONResponse({"detail": "기본 프롬프트 파일이 없습니다."}, status_code=404)
    content = default_path.read_text(encoding="utf-8")
    save_prompt("blog", content)
    return JSONResponse({"ok": True, "content": content})


@app.get("/api/settings/rbac")
async def get_rbac(user: dict = Depends(get_current_user)):
    return JSONResponse(get_setup_wizard_data())


@app.get("/api/settings/plan/usage")
async def get_plan_usage(user: dict = Depends(get_current_user)):
    """
    플랜 & 사용량 조회 — 설정 > 시스템 & 보안 > 플랜 & 사용량 탭용

    응답 예시:
    {
      "plan_id": "trial",
      "plan_name": "무료 체험",
      "trial_days_left": 7,
      "used_this_month": 2,
      "monthly_limit": 3,
      "usage_pct": 67
    }
    """
    clinic_id = user["clinic_id"]

    try:
        from db_manager import get_db
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")
        with get_db() as conn:
            clinic_row = conn.execute(
                """
                SELECT c.plan_id, c.plan_expires_at, c.trial_expires_at,
                       p.name AS plan_name, p.monthly_blog_limit
                FROM clinics c
                LEFT JOIN plans p ON c.plan_id = p.id
                WHERE c.id = ?
                """,
                (clinic_id,),
            ).fetchone()

            if not clinic_row:
                return JSONResponse({"detail": "클리닉 정보를 찾을 수 없습니다."}, status_code=404)

            usage_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM usage_logs
                WHERE clinic_id = ?
                  AND feature = 'blog_generation'
                  AND used_at >= ?
                """,
                (clinic_id, month_start),
            ).fetchone()
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).error("plan usage query failed (clinic_id=%s): %s", clinic_id, exc)
        return JSONResponse({"detail": "서버 오류"}, status_code=500)

    used = usage_row["cnt"] if usage_row else 0
    monthly_limit = clinic_row["monthly_blog_limit"]

    effective = resolve_effective_plan(
        clinic_row["plan_id"],
        clinic_row["plan_expires_at"],
        clinic_row["trial_expires_at"],
    )
    effective_plan = effective["plan_id"]

    plan_name_map = {
        "free": "무료",
        "trial": "무료 체험",
        "standard": "스탠다드",
        "pro": "프로",
    }
    plan_name = plan_name_map.get(effective_plan, clinic_row["plan_name"] or effective_plan)

    # 사용률: 무료 플랜일 때만 계산 (유료/체험은 무제한)
    if not effective["has_unlimited"] and monthly_limit and monthly_limit > 0:
        usage_pct = min(100, int(used / monthly_limit * 100))
    else:
        usage_pct = 0

    return JSONResponse({
        "plan_id": effective_plan,
        "plan_name": plan_name,
        "trial_days_left": effective["trial_days_left"],
        "used_this_month": used,
        "monthly_limit": monthly_limit,
        "usage_pct": usage_pct,
    })


@app.get("/api/blog/beta-usage")
async def get_beta_usage(user: dict = Depends(get_current_user)):
    """베타 기간 사용량 조회 — 블로그 생성 / 프롬프트 복사 / API 키 여부"""
    clinic_id = user["clinic_id"]
    blog_count = max(0, _count_total_blogs(clinic_id))
    copy_count = max(0, _count_total_prompt_copies(clinic_id))

    api_key_configured = False
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key_configured FROM clinics WHERE id = ?", (clinic_id,)
            ).fetchone()
        api_key_configured = bool(row["api_key_configured"]) if row else False
    except Exception:
        pass

    return JSONResponse({
        "blog_count": blog_count,
        "blog_limit": _FREE_BLOG_LIMIT,
        "copy_count": copy_count,
        "copy_limit": _PROMPT_COPY_LIMIT,
        "api_key_configured": api_key_configured,
    })


_FEEDBACK_BATCH = 5  # 이 개수마다 리포트 갱신

def _write_feedback_report() -> None:
    """feedback.jsonl 전체를 읽어 data/feedback_report.md 생성 (개발자 전용)."""
    import json as _json
    log_path  = ROOT / "data" / "feedback.jsonl"
    ack_path  = ROOT / "data" / "feedback_ack.txt"
    rep_path  = ROOT / "data" / "feedback_report.md"
    if not log_path.exists():
        return
    with open(log_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    total = len(lines)
    acked = int(ack_path.read_text().strip()) if ack_path.exists() else 0
    unread = lines[acked:]
    if not unread:
        return
    items = []
    for l in unread:
        try:
            items.append(_json.loads(l))
        except Exception:
            pass
    page_labels = {"blog": "블로그", "dashboard": "대시보드", "help": "도움말"}
    rows = "\n".join(
        f"- [{page_labels.get(i.get('page',''), i.get('page','?'))}] {i.get('ts','')} — {i.get('message','')}"
        for i in items
    )
    report = (
        f"# 피드백 리포트 (미확인 {len(unread)}건 / 전체 {total}건)\n\n"
        f"확인 후 `data/feedback_ack.txt`의 숫자를 {total}으로 변경하면 다음 리포트에서 제외됩니다.\n\n"
        f"## 미확인 피드백\n\n{rows}\n"
    )
    rep_path.write_text(report, encoding="utf-8")


@app.post("/api/feedback")
async def submit_feedback(request: Request, user: dict = Depends(get_current_user)):
    """피드백 / 오류 신고 저장 (개발자만 열람 — 사용자에게 미노출)"""
    import json as _json
    body = await request.json()
    message = (body.get("message") or "").strip()
    page    = (body.get("page") or "unknown").strip()[:100]
    if not message:
        return JSONResponse({"detail": "메시지를 입력해주세요."}, status_code=400)
    if len(message) > 2000:
        return JSONResponse({"detail": "2000자 이내로 입력해주세요."}, status_code=400)
    from datetime import datetime as _dt
    now_str = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from db_manager import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO feedback (clinic_id, user_id, page, message) VALUES (?,?,?,?)",
                (user["clinic_id"], user["id"], page, message),
            )
            conn.commit()
    except Exception as e:
        return JSONResponse({"detail": f"저장 실패: {e}"}, status_code=500)
    # jsonl 기록 + 5개마다 리포트 갱신
    try:
        log_path = ROOT / "data" / "feedback.jsonl"
        entry = _json.dumps({
            "ts": now_str, "page": page,
            "clinic_id": user["clinic_id"],
            "user": user.get("email", ""),
            "message": message,
        }, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        with open(log_path, encoding="utf-8") as f:
            total = sum(1 for l in f if l.strip())
        ack_path = ROOT / "data" / "feedback_ack.txt"
        acked = int(ack_path.read_text().strip()) if ack_path.exists() else 0
        if (total - acked) >= _FEEDBACK_BATCH:
            _write_feedback_report()
    except Exception:
        pass
    return JSONResponse({"ok": True})


@app.post("/api/blog/track-prompt-copy")
async def track_prompt_copy(user: dict = Depends(get_current_user)):
    """프롬프트 복사 횟수 기록 — 한도 초과 시 429 반환"""
    clinic_id = user["clinic_id"]
    check_prompt_copy_limit(clinic_id)
    log_usage(clinic_id, "prompt_copy", {})
    return JSONResponse({"ok": True})


@app.post("/api/admin/daily-report")
async def admin_daily_report(request: Request):
    """
    관리자 전용 — 데일리 리포트 즉시 생성.
    인증: 세션(chief_director + ADMIN_CLINIC_ID) 또는 ADMIN_SECRET Bearer.
    body(선택): { "date": "YYYY-MM-DD" } — 생략 시 오늘 날짜.
    """
    _require_admin_or_session(request)

    try:
        body = await request.json()
        date_str = (body.get("date") or "").strip()
    except Exception:
        date_str = ""

    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not _DATE_RE.match(date_str):
        return JSONResponse({"detail": "date 형식: YYYY-MM-DD"}, status_code=400)

    try:
        from daily_report import generate_daily_report
        out_path = await asyncio.to_thread(generate_daily_report, date_str)
        return JSONResponse({"ok": True, "date": date_str, "path": str(out_path)})
    except Exception as e:
        return JSONResponse({"detail": f"리포트 생성 실패: {e}"}, status_code=500)


@app.post("/api/admin/clinic")
async def admin_create_clinic(request: Request):
    """
    관리자 전용 — 신규 한의원 생성 (trial_expires_at 자동 설정).

    인증: Authorization: Bearer <ADMIN_SECRET> 헤더.
    ADMIN_SECRET 환경 변수 미설정 시 비활성화.

    요청 예시:
        { "name": "강남 한의원", "max_slots": 5 }

    응답 예시:
        { "clinic_id": 3, "trial_expires_at": "2026-05-06T00:00:00+00:00" }
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret:
        return JSONResponse({"detail": "관리자 기능이 비활성화되어 있습니다."}, status_code=403)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != admin_secret:
        return JSONResponse({"detail": "인증 실패"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"detail": "name 필드가 필요합니다."}, status_code=400)

    max_slots = int(body.get("max_slots", 5))

    from db_manager import get_db
    from datetime import timedelta
    trial_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    clinic_id = create_clinic(name, max_slots)
    return JSONResponse({"clinic_id": clinic_id, "trial_expires_at": trial_expires_at})


@app.post("/api/settings/rbac")
async def save_rbac(request: Request, user: dict = Depends(get_current_user)):
    """RBAC 설정 저장 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 RBAC 설정을 변경할 수 있습니다."}, status_code=403)

    body = await request.json()
    module_permissions = body.get("module_permissions", {})
    settings_permissions = body.get("settings_permissions", {})

    if not module_permissions or not settings_permissions:
        return JSONResponse(
            {"detail": "module_permissions와 settings_permissions가 필요합니다."},
            status_code=400,
        )

    save_wizard_result(module_permissions, settings_permissions)
    return JSONResponse({"ok": True})


# ── 모듈 권한 API ─────────────────────────────────────────────────

@app.get("/api/modules/my")
async def my_modules(user: dict = Depends(get_current_user)):
    """현재 로그인 사용자에게 허용된 모듈 목록"""
    allowed = get_allowed_modules(role=user["role"], staff_id=None)
    return JSONResponse({"role": user["role"], "modules": allowed})


@app.get("/api/modules/info")
async def modules_info(user: dict = Depends(get_current_user)):
    return JSONResponse(get_module_info())


@app.post("/api/modules/config")
async def save_module_config(request: Request, user: dict = Depends(get_current_user)):
    """직원 모듈 권한 저장 — director 이상 전용"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    body = await request.json()
    staff_id = body.get("staff_id", "").strip()
    name = body.get("name", "").strip()
    modules = body.get("modules", [])

    if not staff_id or not name:
        return JSONResponse({"detail": "staff_id와 name은 필수입니다."}, status_code=400)

    result = save_staff_permissions(staff_id, name, modules)
    return JSONResponse({"ok": True, "staff_id": staff_id, **result})


# ── 블로그 API ────────────────────────────────────────────────────

@app.get("/api/blog/stats")
async def blog_stats(user: dict = Depends(get_current_user)):
    stats = get_blog_stats()
    # 온보딩 소요 시간 계산 (onboarding_started_at → first_blog_at)
    try:
        with __import__('db_manager').get_db() as conn:
            row = conn.execute(
                "SELECT onboarding_started_at, first_blog_at FROM clinics WHERE id = ?",
                (user["clinic_id"],),
            ).fetchone()
        if row and row["onboarding_started_at"] and row["first_blog_at"]:
            from datetime import datetime as _dt
            fmt = "%Y-%m-%dT%H:%M:%S+00:00"
            started = _dt.strptime(row["onboarding_started_at"], fmt)
            finished = _dt.strptime(row["first_blog_at"], fmt)
            stats["onboarding_seconds"] = max(0, int((finished - started).total_seconds()))
        else:
            stats["onboarding_seconds"] = None
    except Exception:
        stats["onboarding_seconds"] = None
    return JSONResponse(stats)


@app.post("/api/blog/history/{entry_id}/publish-check")
async def publish_check(entry_id: int, user: dict = Depends(get_current_user)):
    """발행 확인 대기 등록 — 네이버 블로그 아이디 필요"""
    from naver_checker import add_pending_check, get_pending_by_stat_id, is_naver_configured
    from blog_history import get_history_list

    if not is_naver_configured():
        raise HTTPException(status_code=400, detail="네이버 API 키가 설정되지 않았습니다. (.env NAVER_CLIENT_ID/SECRET 필요)")

    # 네이버 블로그 아이디 조회
    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT naver_blog_id FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    naver_blog_id = (row["naver_blog_id"] or "").strip() if row else ""
    if not naver_blog_id:
        raise HTTPException(status_code=400, detail="네이버 블로그 아이디가 설정되지 않았습니다. 설정 > 한의원 프로필에서 등록하세요.")

    # 블로그 항목 조회
    history = get_history_list(page=1, per_page=9999)
    target = next((e for e in history["items"] if e["id"] == entry_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="블로그 항목을 찾을 수 없습니다.")

    pending = add_pending_check(
        blog_stat_id=entry_id,
        keyword=target["keyword"],
        title=target.get("title") or target["keyword"],
        naver_blog_id=naver_blog_id,
    )
    return JSONResponse({"status": "ok", "pending": pending})


@app.get("/api/blog/notifications")
async def blog_notifications(user: dict = Depends(get_current_user)):
    """대시보드 알림 조회 — found + expired 미확인 항목"""
    from naver_checker import get_dashboard_notifications
    items = get_dashboard_notifications()
    return JSONResponse({"items": items})


@app.get("/api/blog/publish-status")
async def blog_publish_status(user: dict = Depends(get_current_user)):
    """블로그 항목별 발행 확인 상태 일괄 반환 — by_id={stat_id: {status, found_url, ...}}"""
    from naver_checker import _load as _load_pending
    items = _load_pending()
    by_id: dict = {}
    for it in items:
        by_id[int(it.get("blog_stat_id", 0))] = {
            "status": it.get("status"),
            "found_url": it.get("found_url"),
            "started_at": it.get("started_at"),
            "check_count": it.get("check_count", 0),
        }
    return JSONResponse({"by_id": by_id})


@app.post("/api/blog/notifications/{pending_id}/dismiss")
async def dismiss_notification(pending_id: int, user: dict = Depends(get_current_user)):
    """알림 dismiss"""
    from naver_checker import mark_notified
    mark_notified(pending_id)
    return JSONResponse({"status": "ok"})


@app.get("/api/blog/history")
async def blog_history(
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    return JSONResponse(get_history_list(page=page, per_page=per_page))


@app.get("/api/blog/history/{entry_id}/text")
async def blog_history_text(entry_id: int, user: dict = Depends(get_current_user)):
    text = get_blog_text(entry_id)
    if text is None:
        raise HTTPException(status_code=404, detail="전문을 찾을 수 없거나 만료되었습니다.")
    return JSONResponse({"text": text})


def _stream_and_save(
    base_gen: Generator, keyword: str, tone: str, seo_keywords: list,
    clinic_id: Optional[int] = None,
) -> Generator:
    """SSE 스트림 통과 + done 이벤트 감지 시 이력 저장 및 첫 블로그 시각 기록"""
    collected: list = []
    for chunk in base_gen:
        yield chunk
        raw = chunk.removeprefix("data: ").strip()
        try:
            data = _json.loads(raw)
            if "text" in data:
                collected.append(data["text"])
            elif "replace" in data:
                # 키워드 보강 후처리로 전체 텍스트 교체
                collected = [data["replace"]]
            elif data.get("done"):
                blog_text = "".join(collected)
                char_count = len(blog_text)
                cost_krw = data.get("usage", {}).get("cost_krw", 0)
                entry_id = save_blog_entry(
                    keyword, tone, char_count, cost_krw, seo_keywords, blog_text,
                    clinic_id=clinic_id,
                )
                # done 직후 entry_id 이벤트를 별도로 발송 (프론트에서 발행 확인 버튼 활성화)
                yield f"data: {_json.dumps({'entry_id': entry_id}, ensure_ascii=False)}\n\n"
                # 첫 블로그 생성 완료 시각 기록 (COALESCE — 이후 호출은 무시)
                if clinic_id:
                    try:
                        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                        with __import__('db_manager').get_db() as conn:
                            conn.execute(
                                "UPDATE clinics SET first_blog_at = COALESCE(first_blog_at, ?) WHERE id = ?",
                                (now_iso, clinic_id),
                            )
                    except Exception:
                        pass  # 통계 기록 실패 시 생성 서비스에 영향 없음
        except Exception:
            pass


@app.post("/conversation-flow")
async def get_conversation_flow(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    keyword = body.get("keyword", "").strip()

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    config = load_config()
    if not config["flow"].get("questions_enabled", True):
        return {"questions": []}

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": ".env 파일에 ANTHROPIC_API_KEY를 설정해주세요."}

    try:
        questions = generate_conversation_flow(keyword, api_key)
        return {"questions": questions}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/build-prompt")
async def build_prompt_endpoint(request: Request, user: dict = Depends(get_current_user)):
    """
    Claude API 호출 없이 프롬프트 텍스트만 조립해서 반환한다.
    T1(프롬프트 복사) 기능 — plan_guard 한도 차감 없음.
    """
    body = await request.json()
    keyword      = body.get("keyword", "").strip()
    answers      = body.get("answers", {})
    materials    = body.get("materials", {})
    mode         = body.get("mode", "정보")
    reader_level = body.get("reader_level", "일반인")
    seo_keywords = body.get("seo_keywords", [])
    clinic_info       = body.get("clinic_info", "")
    format_id         = body.get("format_id", None)
    explanation_types = body.get("explanation_types", [])

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    try:
        result = build_prompt_text(
            keyword=keyword,
            answers=answers,
            materials=materials,
            mode=mode,
            reader_level=reader_level,
            seo_keywords=seo_keywords,
            clinic_info=clinic_info,
            format_id=format_id,
            explanation_types=explanation_types,
        )
        return {
            "system_prompt": result["system_prompt"],
            "user_message": result["user_message"],
            "format_id": result.get("format_id"),
            "hook_id": result.get("hook_id"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/generate")
async def generate(request: Request, user: dict = Depends(get_current_user)):
    # 플랜 한도 체크 (무료 월 3편, 초과 시 429 반환)
    check_blog_limit(user["clinic_id"])

    body = await request.json()
    keyword      = body.get("keyword", "").strip()
    answers      = body.get("answers", {})
    materials    = body.get("materials", {})
    mode         = body.get("mode", "정보")
    reader_level = body.get("reader_level", "일반인")
    seo_keywords = body.get("seo_keywords", [])   # ["키워드1", "키워드2"]
    # 쉼표 구분, 공백은 키워드 내부 문자로 보존 (앞뒤만 제거)
    if isinstance(seo_keywords, str):
        seo_keywords = [k.strip() for k in seo_keywords.split(",") if k.strip()]
    else:
        normalized: list[str] = []
        for kw in seo_keywords:
            for part in str(kw).split(","):
                part = part.strip()
                if part:
                    normalized.append(part)
        seo_keywords = normalized
    clinic_info       = body.get("clinic_info", "").strip()       # 블로그 생성기 추가 입력
    explanation_types = body.get("explanation_types", [])          # 선택된 설명 방식 목록
    char_count        = body.get("char_count", None)               # {"min": N, "max": M} or None
    format_id         = body.get("format_id", None)                # v0.3 형식 선택 (없으면 자동)

    if not keyword:
        async def _err():
            yield f"data: {_json.dumps({'error': '주제를 입력해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def _err():
            yield f"data: {_json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    tone = answers.get("tone", "전문적") if answers else "전문적"

    # DB에서 원장 소개글 + 클리닉 특징/장점 자동 조회
    with __import__('db_manager').get_db() as _conn:
        _clinic_row = _conn.execute(
            "SELECT intro, blog_features FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    _db_intro = (_clinic_row["intro"] or "").strip() if _clinic_row else ""
    _db_features = (_clinic_row["blog_features"] or "").strip() if _clinic_row else ""

    # 블로그 생성기 입력값이 있으면 DB의 blog_features에 저장 (다음 접속 시 유지)
    if clinic_info:
        with __import__('db_manager').get_db() as _conn:
            _conn.execute(
                "UPDATE clinics SET blog_features = ? WHERE id = ?",
                (clinic_info, user["clinic_id"]),
            )
        _db_features = clinic_info  # 방금 저장한 값을 이번 생성에도 즉시 반영

    # intro + blog_features 합쳐서 주입 (둘 다 있을 때만)
    _parts = [p for p in [_db_intro, _db_features] if p]
    clinic_info = "\n\n".join(_parts)

    # 사용량 기록 (실패해도 서비스 계속)
    log_usage(user["clinic_id"], "blog_generation", {"keyword": keyword, "mode": mode})
    # 한도 80% 알림 — 비동기 스레드로 실행, 응답 경로에 영향 없음
    check_and_notify(user["clinic_id"])

    return StreamingResponse(
        _stream_and_save(
            generate_blog_stream(
                keyword, answers, api_key, materials, mode, reader_level,
                seo_keywords=seo_keywords, clinic_info=clinic_info,
                explanation_types=explanation_types,
                char_count=char_count,
                format_id=format_id,
            ),
            keyword, tone, seo_keywords,
            clinic_id=user["clinic_id"],
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/generate-youtube")
async def generate_youtube(request: Request, user: dict = Depends(get_current_user)):
    """
    YouTube 6단계 파이프라인 실행 — SSE 스트리밍.

    Request body:
        topic:   str (필수) — 영상 주제
        length:  "short" | "long"          (기본: "long")
        style:   "educational" | "marketing" (기본: "educational")
    """
    body = await request.json()
    topic = body.get("topic", "").strip()
    if not topic:
        async def _err():
            import json as _j
            yield f"data: {_j.dumps({'type': 'error', 'step': 'init', 'msg': '영상 주제를 입력해주세요.'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    options = {
        "length": body.get("length", "long"),
        "style": body.get("style", "educational"),
    }

    return StreamingResponse(
        generate_youtube_stream(
            topic=topic,
            clinic_id=user["clinic_id"],
            options=options,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/youtube")
async def youtube_page(request: Request, current_user: dict = Depends(get_current_user)):
    """YouTube 생성기 페이지"""
    return FileResponse(ROOT / "templates" / "youtube.html")


@app.post("/generate-image-prompts")
async def generate_image_prompts(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    keyword     = body.get("keyword", "").strip()
    blog_content = body.get("blog_content", "").strip()
    style       = body.get("style", "photorealistic")
    tone        = body.get("tone", "warm")

    if not keyword or not blog_content:
        async def _err():
            yield f"data: {_json.dumps({'error': '주제와 블로그 본문이 필요합니다.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def _err():
            yield f"data: {_json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    return StreamingResponse(
        generate_image_prompts_stream(keyword, blog_content, api_key, style, tone),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 에이전트 하네스 ────────────────────────────────────────────────

@app.get("/chat")
async def chat_page(request: Request, current_user: dict = Depends(get_current_user)):
    """AI 도우미 — 베타 이후 자연어 라우팅 어시스턴트로 재구현 예정 (현재 비활성).
    URL 직접 접근 시 대시보드로 리다이렉트."""
    return RedirectResponse("/dashboard")


@app.get("/api/agents/available")
async def get_available_agents(current_user: dict = Depends(get_current_user)):
    agents = agent_router.get_available_agents(role=current_user["role"])
    return {"agents": agents}


@app.post("/api/agent/chat")
async def agent_chat(request: Request, current_user: dict = Depends(get_current_user)):
    body = await request.json()
    message = body.get("message", "").strip()
    requested_agent = body.get("agent")

    # Rate limit 확인 (clinic 단위)
    clinic_id = str(current_user.get("clinic_id", "default"))
    if not _check_rate_limit(clinic_id):
        return {"agent_name": None, "response": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.", "error": True}

    # 에이전트 결정
    if requested_agent:
        try:
            agent_router.get_agent_config(requested_agent)  # 화이트리스트 검증 (Path Traversal 방지)
        except ValueError:
            return {"agent_name": None, "response": "유효하지 않은 에이전트입니다.", "error": True}
        available = [a["name"] for a in agent_router.get_available_agents(current_user["role"])]
        if requested_agent not in available:
            return {"agent_name": requested_agent, "response": "접근 권한이 없습니다.", "error": True}
        agent_name = requested_agent
    else:
        agent_name = agent_router.classify_intent(message)

    if not agent_name:
        return {"agent_name": None, "response": "매칭되는 에이전트가 없습니다. 더 구체적으로 질문해 주세요."}

    system_prompt = agent_router.get_system_prompt(agent_name)
    config = agent_router.get_agent_config(agent_name)

    # Claude API 호출 (최대 3회 시도 — timeout/429 대응)
    client = _create_anthropic_client()
    response_msg = None
    for attempt in range(3):
        try:
            response_msg = client.messages.create(
                model=config.get("model", "claude-sonnet-4-6"),
                max_tokens=config.get("max_tokens", 2000),
                system=system_prompt,
                messages=[{"role": "user", "content": message}],
            )
            break
        except (anthropic.APITimeoutError, anthropic.RateLimitError):
            if attempt == 2:
                return {"agent_name": agent_name, "response": "현재 AI 서비스에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.", "error": True}
            import time as _time
            _time.sleep(1)
        except anthropic.APIError:
            return {"agent_name": agent_name, "response": "AI 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.", "error": True}

    response_text = response_msg.content[0].text

    # 할루시네이션 감지 + 경고 주석 추가
    hallucination_risk = agent_middleware.check_hallucination_risk(response_text)
    if hallucination_risk:
        response_text += "\n\n⚠️ 의료 정보는 반드시 담당 원장의 확인을 거치시기 바랍니다."

    # 로깅 + 비용 추적 (메시지 원문 비저장)
    agent_middleware.log_request(
        user_id=str(current_user.get("id", "unknown")),
        agent_name=agent_name,
        message=message,
        input_tokens=response_msg.usage.input_tokens,
        output_tokens=response_msg.usage.output_tokens,
    )

    return {
        "agent_name": agent_name,
        "response": response_text,
        "hallucination_warning": hallucination_risk,
    }


# ── 베타 모집 — 공개 페이지 & API ─────────────────────────────────

@app.get("/join")
async def join_page():
    """베타 신청 페이지 — 공개 접근 허용"""
    return FileResponse(ROOT / "templates" / "join.html")


@app.post("/api/beta/apply")
async def beta_apply(request: Request):
    """
    베타/일반 신청 접수 (공개 엔드포인트).

    요청: {
      "name", "clinic_name", "email", "phone"(선택), "note"(선택),
      "terms_consent": true,        # 필수 — 개인정보 수집·이용 동의
      "marketing_consent": false,   # 선택 — 마케팅 정보 수신 동의
      "application_type": "beta"    # 기본 'beta', 향후 'general'
    }
    IP당 5분 창 3회 제한. 30일 미가입 시 자동 만료(status='expired').
    """
    import re as _re
    _EMAIL_RE = _re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    ip = request.client.host if request.client else "unknown"
    if not _check_ip_apply_limit(ip):
        return JSONResponse({"detail": "잠시 후 다시 시도해 주세요."}, status_code=429)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    name = (body.get("name") or "").strip()
    clinic_name = (body.get("clinic_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    phone = (body.get("phone") or "").strip()
    note = (body.get("note") or "").strip()
    terms_consent = bool(body.get("terms_consent"))
    marketing_consent = 1 if bool(body.get("marketing_consent")) else 0
    application_type = (body.get("application_type") or "beta").strip().lower()
    if application_type not in ("beta", "general"):
        application_type = "beta"

    if not name or not clinic_name:
        return JSONResponse({"detail": "이름과 한의원명을 입력해 주세요."}, status_code=400)
    if not _EMAIL_RE.match(email):
        return JSONResponse({"detail": "유효한 이메일 주소를 입력해 주세요."}, status_code=400)
    if not terms_consent:
        return JSONResponse(
            {"detail": "개인정보 수집·이용에 동의해 주세요."}, status_code=400,
        )

    user_agent = (request.headers.get("user-agent") or "")[:500]

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    expires_iso = (now_dt + timedelta(days=APPLICANT_EXPIRY_DAYS)).isoformat()

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        # 중복 신청 방지 (pending/invited 상태에서 같은 이메일)
        existing = conn.execute(
            "SELECT id, status FROM beta_applicants WHERE email = ?", (email,)
        ).fetchone()
        if existing and existing["status"] in ("pending", "invited"):
            return JSONResponse({"ok": True, "duplicate": True})
        if existing and existing["status"] == "registered":
            return JSONResponse({"detail": "이미 가입된 이메일입니다."}, status_code=409)

        cur = conn.execute(
            """
            INSERT INTO beta_applicants (
                name, clinic_name, phone, email, note, applied_at,
                application_type, marketing_consent, consented_terms_version,
                ip_address, user_agent, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, clinic_name, phone, email, note, now_iso,
                application_type, marketing_consent, TERMS_VERSION,
                ip, user_agent, expires_iso,
            ),
        )
        applicant_id = cur.lastrowid

    # E1: 신청자 확인 이메일 (fail-soft)
    try:
        from plan_notify import send_beta_apply_confirm
        await asyncio.to_thread(send_beta_apply_confirm, email, name, applicant_id)
    except Exception as exc:
        _logging.getLogger(__name__).warning("beta E1 이메일 실패: %s", exc)

    # E2: 관리자 알림 (fail-soft)
    try:
        from plan_notify import send_beta_admin_notify
        await asyncio.to_thread(
            send_beta_admin_notify, name, clinic_name, email, note, applicant_id,
        )
    except Exception as exc:
        _logging.getLogger(__name__).warning("beta E2 관리자 알림 실패: %s", exc)

    return JSONResponse({"ok": True})


# ── 베타 모집 — 어드민 API ─────────────────────────────────────────

@app.post("/api/settings/clinic/naver-blog-id")
async def save_naver_blog_id(request: Request, user: dict = Depends(get_current_user)):
    """네이버 블로그 아이디 저장 — chief_director 전용"""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()
    naver_blog_id = body.get("naver_blog_id", "").strip()
    with __import__('db_manager').get_db() as conn:
        conn.execute(
            "UPDATE clinics SET naver_blog_id=? WHERE id=?",
            (naver_blog_id or None, user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


@app.get("/admin")
async def admin_index_page(request: Request):
    """어드민 메인 — 하위 페이지 카드 인덱스. 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_index.html")


@app.get("/admin/clinics")
async def admin_clinics_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_clinics.html")


@app.get("/admin/usage")
async def admin_usage_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_usage.html")


@app.get("/admin/feedback")
async def admin_feedback_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_feedback.html")


@app.get("/admin/login-history")
async def admin_login_history_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_login_history.html")


@app.get("/api/admin/login-history")
async def api_admin_login_history(request: Request):
    """
    로그인 이력 조회 + 의심 IP 감지.
    쿼리: email=, user_id=, ip=, success=(0|1), days=(기본 7), limit=(기본 200, 최대 1000).
    의심 IP 기준: 1시간 내 같은 IP에서 5회 이상 실패.
    """
    _require_admin_or_session(request)

    qp = request.query_params
    email      = (qp.get("email") or "").strip().lower() or None
    user_id    = qp.get("user_id")
    ip_filter  = (qp.get("ip") or "").strip() or None
    success_q  = qp.get("success")
    try:
        days = max(1, min(int(qp.get("days") or "7"), 90))
    except ValueError:
        days = 7
    try:
        limit = max(1, min(int(qp.get("limit") or "200"), 1000))
    except ValueError:
        limit = 200

    # cutoff는 SQLite native datetime 함수로 — created_at 문자열 형식 차이(공백 vs T) 회피
    days_modifier = f"-{days} days"
    sql = (
        "SELECT id, user_id, email, clinic_id, ip, user_agent, success, failure_reason, created_at "
        "FROM login_history "
        "WHERE datetime(created_at) >= datetime('now', ?) "
    )
    params: list = [days_modifier]
    if email:
        sql += "AND lower(email) = ? "
        params.append(email)
    if user_id and user_id.isdigit():
        sql += "AND user_id = ? "
        params.append(int(user_id))
    if ip_filter:
        sql += "AND ip = ? "
        params.append(ip_filter)
    if success_q in ("0", "1"):
        sql += "AND success = ? "
        params.append(int(success_q))
    sql += "ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        stats = conn.execute(
            """
            SELECT
              COUNT(*)                                              AS total,
              SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END)           AS ok,
              SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)           AS fail
            FROM login_history
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (days_modifier,),
        ).fetchone()
        # 의심 IP: 최근 1시간 내 같은 IP에서 5회 이상 실패
        suspicious = conn.execute(
            """
            SELECT ip, COUNT(*) AS fails
            FROM login_history
            WHERE success = 0 AND ip IS NOT NULL
              AND datetime(created_at) >= datetime('now', '-1 hour')
            GROUP BY ip
            HAVING COUNT(*) >= 5
            ORDER BY fails DESC
            LIMIT 20
            """
        ).fetchall()

    return JSONResponse({
        "rows": [dict(r) for r in rows],
        "stats": dict(stats) if stats else {},
        "suspicious_ips": [dict(r) for r in suspicious],
        "days": days,
        "limit": limit,
    })


@app.get("/admin/errors")
async def admin_errors_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_errors.html")


@app.get("/admin/blogs")
async def admin_blogs_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_blogs.html")


@app.get("/api/admin/blogs")
async def api_admin_blogs(request: Request):
    """전체 클리닉의 블로그 통합 조회 + 발행 상태 통합.
    쿼리: clinic_id, q(keyword/title 부분일치), publish_status(none/pending/found/missing),
          date_from, date_to, page=1, per_page=50(최대 200)."""
    _require_admin_or_session(request)
    qp = request.query_params
    clinic_id_q = qp.get("clinic_id")
    q           = (qp.get("q") or "").strip().lower() or None
    pub_status  = (qp.get("publish_status") or "").strip() or None
    date_from   = (qp.get("date_from") or "").strip() or None
    date_to     = (qp.get("date_to") or "").strip() or None
    try:
        page = max(1, int(qp.get("page") or "1"))
    except ValueError:
        page = 1
    try:
        per_page = max(1, min(int(qp.get("per_page") or "50"), 200))
    except ValueError:
        per_page = 50

    # 1) blog_stats 로드
    from blog_history import _load_json as _bh_load_json, STATS_PATH as _STATS_PATH
    stats: list = _bh_load_json(_STATS_PATH, default=[])

    # 2) 클리닉명 매핑
    clinic_map: dict = {}
    from db_manager import get_db as _get_db
    with _get_db() as conn:
        for row in conn.execute("SELECT id, name FROM clinics").fetchall():
            clinic_map[int(row["id"])] = row["name"]

    # 3) 발행 상태 — pending_checks.json
    from naver_checker import _load as _load_pending
    pending_items = _load_pending()
    publish_map: dict = {}
    for it in pending_items:
        publish_map[int(it.get("blog_stat_id", 0))] = {
            "status": it.get("status"),
            "found_url": it.get("found_url"),
            "started_at": it.get("started_at"),
            "check_count": it.get("check_count", 0),
        }

    # 4) 통합 + 필터
    out = []
    for e in stats:
        cid = e.get("clinic_id")
        cname = clinic_map.get(int(cid)) if cid else None
        # 발행 상태 종합
        pub = publish_map.get(int(e.get("id", -1)))
        if e.get("naver_url"):
            ps = "found"
        elif pub and pub.get("status") == "found":
            ps = "found"
        elif pub and pub.get("status") == "missing":
            ps = "missing"
        elif pub:
            ps = "pending"
        else:
            ps = "none"

        # 필터
        if clinic_id_q and clinic_id_q.isdigit() and (cid != int(clinic_id_q)):
            continue
        if pub_status and pub_status != ps:
            continue
        if date_from and (e.get("created_at") or "")[:10] < date_from:
            continue
        if date_to and (e.get("created_at") or "")[:10] > date_to:
            continue
        if q:
            blob = (
                (e.get("keyword") or "") + " " +
                (e.get("title") or "")
            ).lower()
            if q not in blob:
                continue

        out.append({
            "id": e.get("id"),
            "clinic_id": cid,
            "clinic_name": cname or "—",
            "keyword": e.get("keyword"),
            "title": e.get("title"),
            "tone": e.get("tone"),
            "char_count": e.get("char_count", 0),
            "cost_krw": e.get("cost_krw", 0),
            "naver_url": e.get("naver_url") or (pub.get("found_url") if pub else None),
            "publish_status": ps,
            "publish_check_count": pub.get("check_count") if pub else 0,
            "created_at": e.get("created_at"),
        })

    # 최신순
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    total = len(out)
    start = (page - 1) * per_page
    items = out[start: start + per_page]

    # 통계 (필터 적용 결과 기준)
    by_status = {"none": 0, "pending": 0, "found": 0, "missing": 0}
    by_clinic: dict = {}
    for r in out:
        by_status[r["publish_status"]] = by_status.get(r["publish_status"], 0) + 1
        cn = r["clinic_name"]
        by_clinic[cn] = by_clinic.get(cn, 0) + 1
    top_clinics = sorted(by_clinic.items(), key=lambda x: -x[1])[:10]

    return JSONResponse({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "stats": {
            "by_status": by_status,
            "top_clinics": [{"name": n, "count": c} for n, c in top_clinics],
        },
        "clinics": [{"id": k, "name": v} for k, v in sorted(clinic_map.items())],
    })


# error_logs 디렉토리 (observability.py와 동일 위치)
_ERROR_LOG_DIR = ROOT / "data" / "error_logs"
_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _read_error_log_file(date_str: str) -> list:
    """단일 일자 jsonl 파일 읽기 — 손상된 줄은 skip."""
    if not _DATE_RE.match(date_str):
        return []
    path = _ERROR_LOG_DIR / f"{date_str}.jsonl"
    if not path.exists():
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(_json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


@app.get("/api/admin/errors/dates")
async def api_admin_error_dates(request: Request):
    """가용 일자 목록 (최신부터). 사이드바 날짜 픽커용."""
    _require_admin_or_session(request)
    if not _ERROR_LOG_DIR.exists():
        return JSONResponse({"dates": []})
    dates = []
    for p in _ERROR_LOG_DIR.glob("*.jsonl"):
        if _DATE_RE.match(p.stem):
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
            dates.append({"date": p.stem, "size": size})
    dates.sort(key=lambda x: x["date"], reverse=True)
    return JSONResponse({"dates": dates})


@app.get("/api/admin/errors/summary")
async def api_admin_error_summary(request: Request):
    """최근 N일 통계: 일자별 카운트, top error_type, top path."""
    _require_admin_or_session(request)
    try:
        days = max(1, min(int(request.query_params.get("days") or "7"), 90))
    except ValueError:
        days = 7

    today = datetime.now(timezone.utc).date()
    daily_counts = []
    error_types: dict = {}
    paths: dict = {}
    total = 0

    for d in range(days):
        date = today - timedelta(days=d)
        rows = _read_error_log_file(date.isoformat())
        cnt = len(rows)
        daily_counts.append({"date": date.isoformat(), "count": cnt})
        total += cnt
        for row in rows:
            et = row.get("error_type") or "Unknown"
            error_types[et] = error_types.get(et, 0) + 1
            p = row.get("path") or "/"
            paths[p] = paths.get(p, 0) + 1

    top_types = sorted(error_types.items(), key=lambda x: -x[1])[:10]
    top_paths = sorted(paths.items(), key=lambda x: -x[1])[:10]

    return JSONResponse({
        "days": days,
        "total": total,
        "daily_counts": list(reversed(daily_counts)),  # 오래된 → 최신 (차트용)
        "top_error_types": [{"type": t, "count": c} for t, c in top_types],
        "top_paths":       [{"path": p, "count": c} for p, c in top_paths],
    })


@app.get("/api/admin/errors")
async def api_admin_errors(request: Request):
    """일자별 에러 로그 + 필터.
    쿼리: date=YYYY-MM-DD(기본 오늘), status, error_type, path_q, limit(기본 200, 최대 1000)."""
    _require_admin_or_session(request)
    qp = request.query_params
    date_str = (qp.get("date") or "").strip() or datetime.now(timezone.utc).date().isoformat()
    if not _DATE_RE.match(date_str):
        return JSONResponse({"detail": "date 형식: YYYY-MM-DD"}, status_code=400)
    status_q = qp.get("status")
    error_type = (qp.get("error_type") or "").strip() or None
    path_q = (qp.get("path_q") or "").strip().lower() or None
    try:
        limit = max(1, min(int(qp.get("limit") or "200"), 1000))
    except ValueError:
        limit = 200

    rows = _read_error_log_file(date_str)

    # 필터
    out = []
    for row in rows:
        if status_q and str(row.get("status")) != status_q:
            continue
        if error_type and row.get("error_type") != error_type:
            continue
        if path_q and path_q not in (row.get("path") or "").lower():
            continue
        out.append(row)

    # 최신부터, limit 적용
    out.reverse()
    truncated = len(out) > limit
    out = out[:limit]

    return JSONResponse({
        "date": date_str,
        "rows": out,
        "total_in_file": len(rows),
        "matched": sum(1 for r in rows if (
            (not status_q or str(r.get("status")) == status_q) and
            (not error_type or r.get("error_type") == error_type) and
            (not path_q or path_q in (r.get("path") or "").lower())
        )),
        "truncated": truncated,
        "limit": limit,
    })


# ─── 어드민 API: 클리닉/사용량/피드백 ──────────────────────────────

@app.get("/api/admin/clinics")
async def api_admin_clinics(request: Request):
    """전체 클리닉 목록 + 핵심 메타. 세션 또는 ADMIN_SECRET Bearer."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.created_at, c.plan_id, c.plan_expires_at,
                   c.trial_expires_at, c.api_key_configured, c.first_blog_at,
                   c.is_admin_clinic, c.naver_blog_id,
                   (SELECT COUNT(*) FROM usage_logs u
                      WHERE u.clinic_id = c.id AND u.feature = 'blog_generate'
                        AND u.used_at >= datetime('now','start of month')) AS blog_this_month,
                   (SELECT COUNT(*) FROM usage_logs u WHERE u.clinic_id = c.id) AS usage_total,
                   (SELECT MAX(used_at) FROM usage_logs u WHERE u.clinic_id = c.id) AS last_seen,
                   (SELECT COUNT(*) FROM users WHERE clinic_id = c.id AND is_active = 1) AS active_users
            FROM clinics c
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    return JSONResponse({"clinics": [dict(r) for r in rows]})


@app.patch("/api/admin/clinic/{clinic_id}")
async def api_admin_update_clinic(clinic_id: int, request: Request):
    """클리닉 메타 일부 수정 — plan_id / trial_expires_at / plan_expires_at."""
    _require_admin_or_session(request)
    body = await request.json()
    fields: list[str] = []
    values: list = []
    if "plan_id" in body:
        plan = (body["plan_id"] or "").strip()
        if plan:
            fields.append("plan_id = ?"); values.append(plan)
    if "trial_expires_at" in body:
        fields.append("trial_expires_at = ?"); values.append(body["trial_expires_at"] or None)
    if "plan_expires_at" in body:
        fields.append("plan_expires_at = ?"); values.append(body["plan_expires_at"] or None)
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다.")
    values.append(clinic_id)
    with _get_db() as conn:
        cur = conn.execute(f"UPDATE clinics SET {', '.join(fields)} WHERE id = ?", values)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="클리닉을 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@app.get("/api/admin/usage")
async def api_admin_usage(request: Request):
    """전체·클리닉별 사용량 집계."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        # 전체 합산
        total_blog = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs "
            "WHERE feature = 'blog_generate' AND used_at >= datetime('now','start of month')"
        ).fetchone()
        total_blog_all = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs WHERE feature = 'blog_generate'"
        ).fetchone()
        total_copy = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs "
            "WHERE feature = 'prompt_copy' AND used_at >= datetime('now','start of month')"
        ).fetchone()
        # 클리닉별 이번 달 블로그 랭킹
        ranking = conn.execute(
            """
            SELECT c.id, c.name, c.plan_id,
                   COUNT(u.id) AS blog_this_month
            FROM clinics c
            LEFT JOIN usage_logs u
              ON u.clinic_id = c.id AND u.feature = 'blog_generate'
                 AND u.used_at >= datetime('now','start of month')
            GROUP BY c.id
            ORDER BY blog_this_month DESC, c.created_at DESC
            LIMIT 50
            """
        ).fetchall()
    # 에러 카운트 (data/error_logs/{date}.jsonl 오늘자만 빠르게)
    error_count_today = 0
    try:
        from datetime import datetime as _dt
        today = _dt.utcnow().strftime("%Y-%m-%d")
        err_path = ROOT / "data" / "error_logs" / f"{today}.jsonl"
        if err_path.exists():
            with open(err_path, encoding="utf-8") as f:
                error_count_today = sum(1 for line in f if line.strip())
    except Exception:
        pass
    return JSONResponse({
        "blog_this_month": int(total_blog["c"]) if total_blog else 0,
        "blog_all_time": int(total_blog_all["c"]) if total_blog_all else 0,
        "prompt_copy_this_month": int(total_copy["c"]) if total_copy else 0,
        "error_count_today": error_count_today,
        "ranking": [dict(r) for r in ranking],
    })


@app.get("/api/admin/feedback")
async def api_admin_feedback(
    request: Request,
    status: str = "all",  # all / new / viewed
    page: int = 1,
    per_page: int = 30,
):
    """피드백 목록. status=new(미확인) / viewed(확인됨) / all."""
    _require_admin_or_session(request)
    where = ""
    if status == "new":
        where = "WHERE viewed_at IS NULL"
    elif status == "viewed":
        where = "WHERE viewed_at IS NOT NULL"
    offset = (max(page, 1) - 1) * per_page
    with _get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM feedback {where}").fetchone()["c"]
        new_count = conn.execute(
            "SELECT COUNT(*) AS c FROM feedback WHERE viewed_at IS NULL"
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT f.id, f.page, f.message, f.created_at, f.viewed_at,
                   f.clinic_id, f.user_id,
                   c.name AS clinic_name,
                   u.email AS user_email
            FROM feedback f
            LEFT JOIN clinics c ON c.id = f.clinic_id
            LEFT JOIN users u ON u.id = f.user_id
            {where}
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return JSONResponse({
        "total": total,
        "new_count": new_count,
        "items": [dict(r) for r in rows],
    })


@app.post("/api/admin/feedback/{fid}/viewed")
async def api_admin_feedback_mark_viewed(fid: int, request: Request):
    """피드백 확인 처리."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        cur = conn.execute(
            "UPDATE feedback SET viewed_at = datetime('now','utc') WHERE id = ? AND viewed_at IS NULL",
            (fid,),
        )
    return JSONResponse({"ok": True, "updated": cur.rowcount})


@app.post("/api/admin/feedback/{fid}/unview")
async def api_admin_feedback_unview(fid: int, request: Request):
    """피드백 확인 취소 (다시 미확인)."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        conn.execute("UPDATE feedback SET viewed_at = NULL WHERE id = ?", (fid,))
    return JSONResponse({"ok": True})


@app.get("/admin/settings")
async def admin_settings_page(request: Request):
    """어드민 시스템 설정 페이지 (네이버 API). 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_settings.html")


@app.get("/api/admin/naver-config")
async def get_naver_config(request: Request):
    """네이버 API 설정 조회 — 세션 또는 ADMIN_SECRET Bearer 인증"""
    _require_admin_or_session(request)
    from naver_checker import APP_SETTINGS_PATH
    import json as _j
    cfg: dict = {}
    try:
        if APP_SETTINGS_PATH.exists():
            with open(APP_SETTINGS_PATH, encoding="utf-8") as f:
                cfg = _j.load(f)
    except Exception:
        pass
    return JSONResponse({
        "naver_client_id": cfg.get("naver_client_id", ""),
        "naver_client_secret_masked": "****" if cfg.get("naver_client_secret") else "",
        "configured": bool(cfg.get("naver_client_id") and cfg.get("naver_client_secret")),
    })


@app.post("/api/admin/naver-config")
async def save_naver_config(request: Request):
    """네이버 API 설정 저장 — 세션 또는 ADMIN_SECRET Bearer 인증"""
    _require_admin_or_session(request)
    body = await request.json()
    client_id = body.get("naver_client_id", "").strip()
    client_secret = body.get("naver_client_secret", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Client ID와 Secret 모두 필요합니다.")
    from naver_checker import APP_SETTINGS_PATH
    import json as _j
    cfg: dict = {}
    try:
        if APP_SETTINGS_PATH.exists():
            with open(APP_SETTINGS_PATH, encoding="utf-8") as f:
                cfg = _j.load(f)
    except Exception:
        pass
    cfg["naver_client_id"] = client_id
    cfg["naver_client_secret"] = client_secret
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(APP_SETTINGS_PATH, "w", encoding="utf-8") as f:
        _j.dump(cfg, f, ensure_ascii=False, indent=2)
    return JSONResponse({"ok": True})


@app.get("/admin/applicants")
async def admin_applicants_page(request: Request):
    """어드민 신청자 관리 페이지. 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_applicants.html")


@app.get("/api/admin/applicants")
async def api_admin_applicants(request: Request):
    """신청자 목록 + 퍼널 통계 반환. 세션 또는 ADMIN_SECRET Bearer 인증."""
    _require_admin_or_session(request)

    application_type = request.query_params.get("type")  # beta / general / 전체(None)
    status_filter    = request.query_params.get("status")  # pending/invited/registered/rejected/expired

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        sql = (
            "SELECT a.*, "
            "  (SELECT COUNT(*) FROM applicant_emails e WHERE e.applicant_id = a.id) AS email_count, "
            "  (SELECT COUNT(*) FROM applicant_emails e WHERE e.applicant_id = a.id AND e.success = 0) AS email_failed "
            "FROM beta_applicants a "
            "WHERE 1=1 "
        )
        params: list = []
        if application_type in ("beta", "general"):
            sql += "AND a.application_type = ? "
            params.append(application_type)
        if status_filter in ("pending", "invited", "registered", "rejected", "expired"):
            sql += "AND a.status = ? "
            params.append(status_filter)
        sql += "ORDER BY a.applied_at DESC"
        rows = conn.execute(sql, params).fetchall()

        # 퍼널 통계 (필터 무관 — 전체 기준)
        stats = conn.execute(
            """
            SELECT
              COUNT(*)                                                         AS total,
              SUM(CASE WHEN status = 'pending'    THEN 1 ELSE 0 END)            AS pending,
              SUM(CASE WHEN status = 'invited'    THEN 1 ELSE 0 END)            AS invited,
              SUM(CASE WHEN status = 'registered' THEN 1 ELSE 0 END)            AS registered,
              SUM(CASE WHEN status = 'rejected'   THEN 1 ELSE 0 END)            AS rejected,
              SUM(CASE WHEN status = 'expired'    THEN 1 ELSE 0 END)            AS expired,
              SUM(CASE WHEN invited_at IS NOT NULL THEN 1 ELSE 0 END)           AS sent,
              SUM(CASE WHEN clicked_at IS NOT NULL THEN 1 ELSE 0 END)           AS clicked
            FROM beta_applicants
            """
        ).fetchone()

    return JSONResponse({
        "applicants": [dict(r) for r in rows],
        "stats": dict(stats) if stats else {},
    })


@app.get("/api/admin/applicants/{applicant_id}/emails")
async def api_admin_applicant_emails(request: Request, applicant_id: int):
    """신청자별 이메일 발송 이력 (timeline UI용)."""
    _require_admin_or_session(request)
    from db_manager import get_db as _get_db
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, email_type, sent_at, success, error_msg "
            "FROM applicant_emails WHERE applicant_id = ? "
            "ORDER BY sent_at DESC, id DESC",
            (applicant_id,),
        ).fetchall()
    return JSONResponse({"emails": [dict(r) for r in rows]})


@app.patch("/api/admin/applicants/{applicant_id}")
async def api_admin_applicant_patch(request: Request, applicant_id: int):
    """admin_notes / admin_tags 수정. 세션 또는 ADMIN_SECRET 인증."""
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    fields: list = []
    params: list = []
    if "admin_notes" in body:
        fields.append("admin_notes = ?")
        params.append((body.get("admin_notes") or "").strip() or None)
    if "admin_tags" in body:
        # 콤마 구분, 트림
        raw = (body.get("admin_tags") or "").strip()
        cleaned = ",".join(t.strip() for t in raw.split(",") if t.strip()) if raw else None
        fields.append("admin_tags = ?")
        params.append(cleaned)
    if not fields:
        return JSONResponse({"detail": "수정할 필드가 없습니다."}, status_code=400)
    params.append(applicant_id)

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        cur = conn.execute(
            f"UPDATE beta_applicants SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"ok": True})


@app.post("/api/admin/applicants/{applicant_id}/reject")
async def api_admin_applicant_reject(request: Request, applicant_id: int):
    """신청 거절. 사유 기록 + status='rejected'."""
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip() or "사유 미기재"

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        row = conn.execute(
            "SELECT status FROM beta_applicants WHERE id = ?", (applicant_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)
        if row["status"] in ("registered",):
            return JSONResponse(
                {"detail": "이미 가입 완료된 신청자는 거절할 수 없습니다."}, status_code=400,
            )
        conn.execute(
            "UPDATE beta_applicants SET status = 'rejected', rejection_reason = ? WHERE id = ?",
            (reason, applicant_id),
        )
    return JSONResponse({"ok": True})


@app.post("/api/admin/applicants/{applicant_id}/resend")
async def api_admin_applicant_resend(request: Request, applicant_id: int):
    """
    수동 재발송. body.email_type:
      - apply_confirm / admin_notify / invite / reminder
    invite/reminder는 invite_token이 있어야 함 (없으면 invite-batch로 다시 진행).
    """
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    email_type = (body.get("email_type") or "").strip()
    if email_type not in ("apply_confirm", "admin_notify", "invite", "reminder"):
        return JSONResponse({"detail": "허용되지 않은 email_type"}, status_code=400)

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, name, clinic_name, phone, email, note, invite_token "
            "FROM beta_applicants WHERE id = ?",
            (applicant_id,),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)

    base_url = os.getenv("BASE_URL", "https://cligent.kr")

    try:
        if email_type == "apply_confirm":
            from plan_notify import send_beta_apply_confirm
            ok = await asyncio.to_thread(
                send_beta_apply_confirm, row["email"], row["name"], applicant_id,
            )
        elif email_type == "admin_notify":
            from plan_notify import send_beta_admin_notify
            ok = await asyncio.to_thread(
                send_beta_admin_notify,
                row["name"], row["clinic_name"], row["email"], row["note"] or "",
                applicant_id,
            )
        else:  # invite / reminder — invite_token 필요
            if not row["invite_token"]:
                return JSONResponse(
                    {"detail": "초대 토큰이 없습니다. invite-batch로 먼저 초대해 주세요."},
                    status_code=400,
                )
            invite_url = f"{base_url}/onboard?token={row['invite_token']}"
            if email_type == "invite":
                from plan_notify import send_beta_invite_email
                ok = await asyncio.to_thread(
                    send_beta_invite_email, row["email"], row["name"], invite_url, applicant_id,
                )
            else:  # reminder
                from plan_notify import send_beta_reminder
                ok = await asyncio.to_thread(
                    send_beta_reminder, row["email"], row["name"], invite_url, applicant_id,
                )
    except Exception as exc:
        _logging.getLogger(__name__).warning("resend 실패 (id=%s): %s", applicant_id, exc)
        return JSONResponse({"detail": f"발송 중 오류: {exc}"}, status_code=500)

    return JSONResponse({"ok": True, "sent": bool(ok)})


@app.post("/api/admin/invite-batch")
async def api_admin_invite_batch(request: Request):
    """
    선택한 신청자에게 초대 링크 일괄 발송.
    인증: 세션(chief_director + ADMIN_CLINIC_ID) 또는 Bearer ADMIN_SECRET.

    요청: { "ids": [1, 2, 3] }
    응답: { "invited": [...], "failed": [...] }
    """
    _require_admin_or_session(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    ids = body.get("ids", [])
    if not ids or not isinstance(ids, list):
        return JSONResponse({"detail": "ids 배열이 필요합니다."}, status_code=400)

    admin_clinic_id = int(os.getenv("ADMIN_CLINIC_ID", "1"))
    admin_user_id   = int(os.getenv("ADMIN_USER_ID", "1"))
    base_url = os.getenv("BASE_URL", "https://cligent.kr")

    from db_manager import get_db as _get_db
    from plan_notify import send_beta_invite_email

    invited = []
    failed  = []

    # asyncio.Semaphore는 코루틴 내부에서 생성해야 함
    sem = asyncio.Semaphore(5)

    async def _send_one(row: dict) -> None:
        async with sem:
            try:
                token = create_invite(
                    clinic_id=admin_clinic_id,
                    email=row["email"],
                    role="chief_director",
                    created_by=admin_user_id,
                )
                invite_url = f"{base_url}/onboard?token={token}"
                now_iso = datetime.now(timezone.utc).isoformat()

                with _get_db() as conn:
                    conn.execute(
                        "UPDATE beta_applicants "
                        "SET status = 'invited', invited_at = ?, invite_token = ? "
                        "WHERE id = ?",
                        (now_iso, token, row["id"]),
                    )

                await asyncio.to_thread(
                    send_beta_invite_email, row["email"], row["name"], invite_url, row["id"],
                )
                invited.append(row["id"])

            except ValueError as exc:
                failed.append({"id": row["id"], "reason": str(exc)})
            except Exception as exc:
                _logging.getLogger(__name__).warning(
                    "invite-batch 실패 (id=%s): %s", row["id"], exc
                )
                failed.append({"id": row["id"], "reason": "초대 생성 오류"})

    with _get_db() as conn:
        rows = conn.execute(
            f"SELECT id, name, email FROM beta_applicants "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND status = 'pending'",
            ids,
        ).fetchall()

    await asyncio.gather(*[_send_one(dict(r)) for r in rows])

    return JSONResponse({"invited": invited, "failed": failed})


# ─── 공지사항 게시판 ──────────────────────────────────────────────
from db_manager import get_db as _get_db  # noqa: E402  (announcements 라우트 전용)

_ANNOUNCE_CATEGORIES = {"update", "maintenance", "general"}
_ANNOUNCE_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ANNOUNCE_MAX_UPLOAD = 5 * 1024 * 1024  # 5MB


def _require_announce_admin(user: dict) -> None:
    """공지 작성·수정·삭제 권한: ADMIN_CLINIC_ID + chief_director."""
    if not (_is_admin_clinic(user) and user["role"] == "chief_director"):
        raise HTTPException(status_code=403, detail="공지 작성 권한이 없습니다.")


@app.get("/announcements")
async def announcements_page(_user: dict = Depends(get_current_user)):
    """공지사항 목록 페이지 (인증 필수)."""
    return FileResponse(ROOT / "templates" / "announcements.html")


@app.get("/announcements/new")
async def announcement_new_page(user: dict = Depends(get_current_user)):
    """공지 작성 페이지 — admin only."""
    _require_announce_admin(user)
    return FileResponse(ROOT / "templates" / "announcement_edit.html")


@app.get("/announcements/{ann_id}")
async def announcement_detail_page(ann_id: int, _user: dict = Depends(get_current_user)):
    """공지 상세 페이지 (인증 필수)."""
    return FileResponse(ROOT / "templates" / "announcement_detail.html")


@app.get("/announcements/{ann_id}/edit")
async def announcement_edit_page(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 수정 페이지 — admin only."""
    _require_announce_admin(user)
    return FileResponse(ROOT / "templates" / "announcement_edit.html")


@app.get("/api/announcements")
async def api_announcements_list(_user: dict = Depends(get_current_user)):
    """공지 목록 — pinned 우선, 그 다음 created_at desc."""
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, category, is_pinned, author, created_at, updated_at "
            "FROM announcements "
            "ORDER BY is_pinned DESC, created_at DESC"
        ).fetchall()
    return JSONResponse({"announcements": [dict(r) for r in rows]})


@app.get("/api/announcements/unread-count")
async def api_announcements_unread_count(user: dict = Depends(get_current_user)):
    """안 읽은 공지 개수."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM announcements a "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM announcement_reads r "
            "  WHERE r.announcement_id = a.id AND r.user_id = ?"
            ")",
            (user["id"],),
        ).fetchone()
    return JSONResponse({"unread": int(row["cnt"]) if row else 0})


@app.get("/api/announcements/{ann_id}")
async def api_announcement_detail(ann_id: int, _user: dict = Depends(get_current_user)):
    """공지 상세."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, title, body_md, category, is_pinned, author, created_at, updated_at "
            "FROM announcements WHERE id = ?",
            (ann_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse(dict(row))


@app.post("/api/announcements")
async def api_announcement_create(request: Request, user: dict = Depends(get_current_user)):
    """공지 신규 작성 — admin only."""
    _require_announce_admin(user)
    body = await request.json()
    title = (body.get("title") or "").strip()
    body_md = (body.get("body_md") or "").strip()
    category = body.get("category", "general")
    is_pinned = 1 if body.get("is_pinned") else 0
    if not title or not body_md:
        raise HTTPException(status_code=400, detail="제목과 본문을 입력해주세요.")
    if category not in _ANNOUNCE_CATEGORIES:
        category = "general"
    author = body.get("author") or "원장"
    with _get_db() as conn:
        cur = conn.execute(
            "INSERT INTO announcements (title, body_md, category, is_pinned, author) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, body_md, category, is_pinned, author),
        )
        new_id = cur.lastrowid
    return JSONResponse({"id": new_id})


@app.patch("/api/announcements/{ann_id}")
async def api_announcement_update(ann_id: int, request: Request, user: dict = Depends(get_current_user)):
    """공지 수정 — admin only."""
    _require_announce_admin(user)
    body = await request.json()
    fields = []
    values = []
    for key in ("title", "body_md", "author"):
        if key in body:
            val = (body[key] or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail=f"{key}는 비워둘 수 없습니다.")
            fields.append(f"{key} = ?")
            values.append(val)
    if "category" in body:
        cat = body["category"]
        if cat not in _ANNOUNCE_CATEGORIES:
            cat = "general"
        fields.append("category = ?")
        values.append(cat)
    if "is_pinned" in body:
        fields.append("is_pinned = ?")
        values.append(1 if body["is_pinned"] else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다.")
    fields.append("updated_at = datetime('now', 'utc')")
    values.append(ann_id)
    with _get_db() as conn:
        cur = conn.execute(
            f"UPDATE announcements SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@app.delete("/api/announcements/{ann_id}")
async def api_announcement_delete(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 삭제 — admin only."""
    _require_announce_admin(user)
    with _get_db() as conn:
        cur = conn.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@app.post("/api/announcements/{ann_id}/read")
async def api_announcement_mark_read(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 읽음 처리."""
    with _get_db() as conn:
        # 존재 확인
        exists = conn.execute("SELECT 1 FROM announcements WHERE id = ?", (ann_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
        conn.execute(
            "INSERT OR IGNORE INTO announcement_reads (user_id, announcement_id) VALUES (?, ?)",
            (user["id"], ann_id),
        )
    return JSONResponse({"ok": True})


@app.post("/api/announcements/upload-image")
async def api_announcement_upload_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """공지 본문 첨부 이미지 업로드 — admin only. 반환: {url}"""
    _require_announce_admin(user)
    import uuid as _uuid
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ANNOUNCE_ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="지원하지 않는 형식입니다 (jpg, png, webp, gif).")
    data = await file.read()
    if len(data) > _ANNOUNCE_MAX_UPLOAD:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다 (최대 5MB).")
    upload_dir = ROOT / "static" / "uploads" / "announcements"
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_uuid.uuid4().hex}{ext}"
    fpath = upload_dir / fname
    fpath.write_bytes(data)
    url = f"/static/uploads/announcements/{fname}"
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO announcement_attachments (filename, url) VALUES (?, ?)",
            (fname, url),
        )
    return JSONResponse({"url": url})


# ─────────────────────────────────────────────────────────────────
# 관리자 OpenAI API 키 등록 (Phase 1, 2026-04-30)
# 베타 단계: BYOAI 비활성, 모든 사용자가 이 키를 공유 사용.
# 저장: server_secrets 테이블 + Fernet 암호화 (secret_manager 모듈)
# 검증: OpenAI models.list() 호출로 즉시 키 유효성 확인.
# ─────────────────────────────────────────────────────────────────

def _resolve_user_id_from_session(request: Request) -> Optional[int]:
    """세션 쿠키에서 user_id 추출 (감사 로그용). 실패 시 None."""
    try:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        payload = decode_token(token)
        if not payload:
            return None
        uid = payload.get("user_id") or payload.get("sub")
        if isinstance(uid, str) and uid.isdigit():
            return int(uid)
        if isinstance(uid, int):
            return uid
    except Exception:
        pass
    return None


@app.get("/api/admin/openai-key")
def api_admin_get_openai_key(request: Request):
    """현재 등록된 OpenAI 키 메타 (마스킹된 값 + 갱신일 + 갱신자). 미등록 시 secret=null."""
    _require_admin_or_session(request)
    from secret_manager import get_secret_meta
    meta = get_secret_meta("openai_api_key")
    return JSONResponse({"secret": meta})


@app.post("/api/admin/openai-key")
async def api_admin_set_openai_key(request: Request):
    """
    OpenAI 키 저장 + 즉시 유효성 검증.
    Body: {"value": "sk-..."}
    검증 실패 시 저장하지 않고 400 반환.
    """
    _require_admin_or_session(request)
    body = await request.json()
    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="키 값이 비어 있습니다.")
    if not value.startswith("sk-"):
        raise HTTPException(status_code=400, detail="OpenAI 키는 'sk-'로 시작해야 합니다.")

    # OpenAI에 가벼운 호출로 키 검증
    # Restricted(이미지 전용) 키는 models.list() 권한이 없어 PermissionDeniedError 발생.
    # 401 AuthenticationError = 키 자체가 무효 → 거부
    # 403 PermissionDeniedError = 키는 유효하지만 Models 스코프만 없음 → 통과
    #   (이미지 스코프는 첫 실호출 시 ai_client에서 검증됨)
    try:
        import openai
        client = openai.OpenAI(api_key=value, timeout=10.0)
        client.models.list()  # 200 → 유효
    except openai.AuthenticationError:
        raise HTTPException(status_code=400, detail="유효하지 않은 OpenAI 키입니다.")
    except openai.PermissionDeniedError:
        # Restricted 키 (이미지 전용 등) — 인증은 통과한 상태이므로 저장 허용
        _error_logger.info("OpenAI 키 검증: PermissionDenied — Restricted 키로 간주하고 저장")
    except openai.APIConnectionError:
        raise HTTPException(status_code=503, detail="OpenAI 연결 실패. 잠시 후 다시 시도하세요.")
    except openai.RateLimitError:
        raise HTTPException(status_code=429, detail="OpenAI 요청 한도 도달. 잠시 후 다시 시도하세요.")
    except HTTPException:
        raise
    except Exception as exc:
        _error_logger.exception("OpenAI 키 검증 중 예외")
        raise HTTPException(status_code=500, detail=f"검증 중 오류: {type(exc).__name__}")

    # 검증 성공 → 저장 (감사용 user_id 동봉)
    user_id = _resolve_user_id_from_session(request)
    from secret_manager import set_server_secret, get_secret_meta
    set_server_secret("openai_api_key", value, user_id=user_id)
    meta = get_secret_meta("openai_api_key")
    return JSONResponse({"ok": True, "secret": meta})


@app.delete("/api/admin/openai-key")
def api_admin_delete_openai_key(request: Request):
    """OpenAI 키 삭제 (테스트·키 회전용)."""
    _require_admin_or_session(request)
    from secret_manager import delete_server_secret
    deleted = delete_server_secret("openai_api_key")
    return JSONResponse({"ok": True, "deleted": deleted})
