"""
main.py — FastAPI 앱 진입점

페이지 라우트:
  GET  /              → dashboard.html (JWT 인증 필요, 미인증 시 /login 리다이렉트)
  GET  /mobile        → dashboard_mobile.html
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
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse,
    RedirectResponse, StreamingResponse,
)

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
    get_current_user,
    verify_invite,
)
from blog_generator import generate_blog_stream
from youtube_generator import generate_youtube_stream
from blog_history import get_blog_stats, purge_expired_texts, save_blog_entry, get_history_list, get_blog_text
from blog_generator import build_prompt_text
from config_loader import load_config, save_blog_config, save_prompt
from conversation_flow import generate_conversation_flow
from db_manager import create_clinic, init_db, seed_demo_clinic, seed_demo_owner
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
                    await asyncio.to_thread(send_beta_reminder, row["email"], row["name"], invite_url)
                    _error_logger.info("beta E4 리마인더 발송: %s", row["email"])
                except Exception as exc:
                    _error_logger.warning("beta E4 리마인더 실패 (id=%s): %s", row["id"], exc)
        except Exception as e:
            _error_logger.error("beta_reminder_scheduler 오류: %s", e)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """서버 시작/종료 시 리소스 초기화 및 정리."""
    init_db()
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
    yield
    _sched.cancel()
    _reminder_sched.cancel()
    for task in (_sched, _reminder_sched):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Cligent", lifespan=lifespan)


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


_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}


@app.get("/blog")
async def blog_page(request: Request):
    """블로그 생성기 — 인증 필요"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "index.html", headers=_NO_CACHE)


@app.get("/mobile")
async def mobile_dashboard(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "dashboard_mobile.html", headers=_NO_CACHE)


@app.get("/app")
async def app_shell(request: Request):
    """앱 쉘 — 사이드바 고정 레이아웃 (iframe으로 콘텐츠 로드)"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "app.html", headers=_NO_CACHE)


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
    """대시보드 — 미인증 시 /login 리다이렉트, 모바일이면 /mobile"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")

    ua = request.headers.get("user-agent", "").lower()
    if any(k in ua for k in ("android", "iphone", "ipad", "mobile")):
        return RedirectResponse("/mobile")

    return FileResponse(ROOT / "templates" / "dashboard.html", headers=_NO_CACHE)


# ── 인증 API ──────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def api_login(request: Request):
    """
    로그인 — httpOnly JWT 쿠키 발급

    요청: {"email": "...", "password": "..."}
    응답: {"ok": true, "must_change_pw": false}
    """
    body = await request.json()
    email = body.get("email", "").strip()
    password = body.get("password", "")

    user = authenticate_user(email, password)
    if not user:
        return JSONResponse(
            {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."},
            status_code=401,
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


@app.get("/api/auth/me")
async def api_me(user: dict = Depends(get_current_user)):
    """현재 로그인 사용자 정보"""
    with __import__('db_manager').get_db() as conn:
        clinic = conn.execute(
            "SELECT api_key_configured, onboarding_started_at FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    api_key_configured = bool(clinic["api_key_configured"]) if clinic else False
    return JSONResponse({
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "clinic_id": user["clinic_id"],
        "must_change_pw": bool(user["must_change_pw"]),
        "api_key_configured": api_key_configured,
    })


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
            "SELECT name, phone, address, specialty, hours, intro, blog_features FROM clinics WHERE id = ?",
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

    if not name:
        return JSONResponse({"detail": "한의원 이름은 필수입니다."}, status_code=400)

    hours_json = _j.dumps(hours, ensure_ascii=False) if hours else None

    with __import__('db_manager').get_db() as conn:
        conn.execute(
            "UPDATE clinics SET name=?, phone=?, address=?, specialty=?, hours=?, intro=?, blog_features=? WHERE id=?",
            (name, phone or None, address or None, specialty or None, hours_json, intro or None, blog_features or None, user["clinic_id"]),
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
    관리자 전용 — 데일리 리포트 수동 생성.

    인증: Authorization: Bearer <ADMIN_SECRET> 헤더.
    body(선택): { "date": "YYYY-MM-DD" }  — 생략 시 오늘 날짜
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret:
        return JSONResponse({"detail": "관리자 기능이 비활성화되어 있습니다."}, status_code=403)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != admin_secret:
        return JSONResponse({"detail": "인증 실패"}, status_code=401)

    try:
        body = await request.json()
        date_str = (body.get("date") or "").strip()
    except Exception:
        date_str = ""

    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        from daily_report import generate_daily_report
        out_path = await asyncio.to_thread(generate_daily_report, date_str)
        return JSONResponse({"ok": True, "date": date_str, "path": out_path})
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
                save_blog_entry(keyword, tone, char_count, cost_krw, seo_keywords, blog_text)
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
    clinic_info  = body.get("clinic_info", "")

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
        )
        # 사용자가 AI에 붙여넣기 쉽도록 system + user를 구분해서 반환
        return {
            "system_prompt": result["system_prompt"],
            "user_message": result["user_message"],
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
    return FileResponse(ROOT / "templates" / "chat.html")


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
    베타 신청 접수 (공개 엔드포인트).

    요청: { "name", "clinic_name", "email", "phone"(선택), "note"(선택) }
    응답: { "ok": true }
    IP당 5분 창 3회 제한.
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

    if not name or not clinic_name:
        return JSONResponse({"detail": "이름과 한의원명을 입력해 주세요."}, status_code=400)
    if not _EMAIL_RE.match(email):
        return JSONResponse({"detail": "유효한 이메일 주소를 입력해 주세요."}, status_code=400)

    now_iso = datetime.now(timezone.utc).isoformat()

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

        conn.execute(
            "INSERT INTO beta_applicants (name, clinic_name, phone, email, note, applied_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, clinic_name, phone, email, note, now_iso),
        )

    # E1: 신청자 확인 이메일 (fail-soft)
    try:
        from plan_notify import send_beta_apply_confirm
        await asyncio.to_thread(send_beta_apply_confirm, email, name)
    except Exception as exc:
        _logging.getLogger(__name__).warning("beta E1 이메일 실패: %s", exc)

    # E2: 관리자 알림 (fail-soft)
    try:
        from plan_notify import send_beta_admin_notify
        await asyncio.to_thread(send_beta_admin_notify, name, clinic_name, email, note)
    except Exception as exc:
        _logging.getLogger(__name__).warning("beta E2 관리자 알림 실패: %s", exc)

    return JSONResponse({"ok": True})


# ── 베타 모집 — 어드민 API ─────────────────────────────────────────

@app.get("/admin/applicants")
async def admin_applicants_page():
    """어드민 신청자 관리 페이지 — 인증은 JS에서 API 호출 시 처리"""
    return FileResponse(ROOT / "templates" / "admin_applicants.html")


@app.get("/api/admin/applicants")
async def api_admin_applicants(request: Request):
    """
    신청자 목록 + 통계 반환.
    인증: Authorization: Bearer <ADMIN_SECRET>
    """
    _require_admin(request)

    from db_manager import get_db as _get_db
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, clinic_name, phone, email, note, "
            "applied_at, invited_at, clicked_at, invite_token, status "
            "FROM beta_applicants ORDER BY applied_at DESC"
        ).fetchall()

        stats = conn.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'pending'    THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status = 'invited'    THEN 1 ELSE 0 END) AS invited,
              SUM(CASE WHEN status = 'registered' THEN 1 ELSE 0 END) AS registered,
              SUM(CASE WHEN clicked_at IS NOT NULL THEN 1 ELSE 0 END) AS clicked
            FROM beta_applicants
            """
        ).fetchone()

    return JSONResponse({
        "applicants": [dict(r) for r in rows],
        "stats": dict(stats) if stats else {},
    })


@app.post("/api/admin/invite-batch")
async def api_admin_invite_batch(request: Request):
    """
    선택한 신청자에게 초대 링크 일괄 발송.
    인증: Authorization: Bearer <ADMIN_SECRET>

    요청: { "ids": [1, 2, 3] }
    응답: { "invited": [...], "failed": [...] }

    - ADMIN_CLINIC_ID / ADMIN_USER_ID 환경 변수로 어드민 클리닉/사용자 지정
    - create_invite() ValueError(슬롯 부족, 이미 등록)는 per-row failed[] 처리
    """
    _require_admin(request)

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

                await asyncio.to_thread(send_beta_invite_email, row["email"], row["name"], invite_url)
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
