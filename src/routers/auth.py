"""
src/routers/auth.py — 인증·온보딩·공개 페이지·beta apply 라우터

라우트:
  HTML 페이지 (공개):  /, /login, /onboard, /forgot-password,
                       /terms, /privacy, /business, /join,
                       /robots.txt, /sitemap.xml
  API 인증:           /api/auth/login, /logout, /me, /login-history,
                       /change-password, /invite, /invite/verify, /onboard,
                       /forgot-password
  Public API:         /api/beta/apply

main.py 4,000줄 분할의 첫 번째 라우터 (v0.9.0 / 2026-05-02).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re as _re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time as _time_now
from typing import Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from auth_manager import (
    COOKIE_NAME,
    authenticate_user,
    change_password,
    complete_onboarding,
    create_access_token,
    create_invite,
    create_reinvite,
    get_current_user,
    record_login_attempt,
    verify_invite,
)
from dependencies import NO_CACHE_HEADERS, get_real_ip, is_admin_clinic
from module_manager import role_has_access

# 프로젝트 루트 (src/routers/auth.py 기준 3단계 위)
ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()
_error_logger = logging.getLogger("cligent.errors")


# ─────────────────────────────────────────────────────────────────
# 공유 상태·상수 (라우터 내부 전용)
# ─────────────────────────────────────────────────────────────────

# 신청 폼 약관 버전 (이용약관 또는 개인정보처리방침 변경 시 갱신)
TERMS_VERSION = "v1.0-2026-05-04"
APPLICANT_EXPIRY_DAYS = 30

# IP 베타 신청 레이트 리밋 (5분 창 3회)
_ip_apply_buckets: Dict[str, list] = defaultdict(list)
_IP_APPLY_WINDOW = 300
_IP_APPLY_LIMIT = 3

# Forgot password 이메일별 60초 제한
_FORGOT_PW_RATE_LIMIT_SEC = 60
_forgot_pw_last_request: Dict[str, float] = {}


def _check_ip_apply_limit(ip: str) -> bool:
    """True = 허용, False = 초과 (5분 창 3회 per IP, 베타 신청 전용)."""
    now = _time_now()
    _ip_apply_buckets[ip] = [t for t in _ip_apply_buckets[ip] if now - t < _IP_APPLY_WINDOW]
    if len(_ip_apply_buckets[ip]) >= _IP_APPLY_LIMIT:
        return False
    _ip_apply_buckets[ip].append(now)
    return True


# ─────────────────────────────────────────────────────────────────
# HTML 페이지
# ─────────────────────────────────────────────────────────────────

@router.get("/")
async def root(request: Request):
    """루트 — 인증 시 /app, 미인증 시 마케팅 랜딩."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return RedirectResponse("/app")
    return FileResponse(ROOT / "templates" / "landing.html")


@router.get("/login")
async def login_page():
    return FileResponse(ROOT / "templates" / "login.html")


@router.get("/onboard")
async def onboard_page():
    return FileResponse(ROOT / "templates" / "onboard.html")


@router.get("/terms")
async def terms_page():
    """이용약관"""
    return FileResponse(ROOT / "templates" / "legal" / "terms.html")


@router.get("/privacy")
async def privacy_page():
    """개인정보처리방침"""
    return FileResponse(ROOT / "templates" / "legal" / "privacy.html")


@router.get("/business")
async def business_page():
    """사업자정보"""
    return FileResponse(ROOT / "templates" / "legal" / "business.html")


@router.get("/forgot-password")
async def forgot_password_page():
    """비밀번호 찾기 페이지 — 인증 불필요."""
    return FileResponse(ROOT / "templates" / "forgot_password.html", headers=NO_CACHE_HEADERS)


@router.get("/join")
async def join_page():
    """베타 신청 페이지 — 공개 접근 허용."""
    return FileResponse(ROOT / "templates" / "join.html")


# ─────────────────────────────────────────────────────────────────
# SEO: robots.txt / sitemap.xml
# ─────────────────────────────────────────────────────────────────

@router.get("/robots.txt")
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


@router.get("/sitemap.xml")
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


# ─────────────────────────────────────────────────────────────────
# 인증 API
# ─────────────────────────────────────────────────────────────────

@router.post("/api/auth/login")
async def api_login(request: Request):
    """로그인 — httpOnly JWT 쿠키 발급. 시도/결과 모두 login_history 기록."""
    body = await request.json()
    email = body.get("email", "").strip()
    password = body.get("password", "")

    ip = get_real_ip(request)
    user_agent = request.headers.get("user-agent")

    user, reason = authenticate_user(email, password)

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
        secure=os.getenv("ENV", "dev") != "dev",
        max_age=8 * 3600,
    )
    return response


@router.post("/api/auth/logout")
async def api_logout():
    """로그아웃 — JWT 쿠키 삭제."""
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


@router.get("/api/auth/me")
async def api_me(user: dict = Depends(get_current_user)):
    """현재 로그인 사용자 정보."""
    from db_manager import get_db
    with get_db() as conn:
        clinic = conn.execute(
            "SELECT api_key_configured, onboarding_started_at FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    api_key_configured = bool(clinic["api_key_configured"]) if clinic else False
    is_admin = is_admin_clinic(user) and user["role"] == "chief_director"
    return JSONResponse({
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "clinic_id": user["clinic_id"],
        "must_change_pw": bool(user["must_change_pw"]),
        "api_key_configured": api_key_configured,
        "can_invite": is_admin_clinic(user),
        "is_admin": is_admin,
    })


@router.get("/api/auth/login-history")
async def api_my_login_history(user: dict = Depends(get_current_user)):
    """본인 로그인 이력 (PIPA, 90일 50건)."""
    from db_manager import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, ip, user_agent, success, failure_reason, created_at "
            "FROM login_history "
            "WHERE user_id = ? "
            "  AND datetime(created_at) >= datetime('now', '-90 days') "
            "ORDER BY created_at DESC LIMIT 50",
            (user["id"],),
        ).fetchall()
    return JSONResponse({"rows": [dict(r) for r in rows]})


@router.post("/api/auth/change-password")
async def api_change_password(request: Request, user: dict = Depends(get_current_user)):
    """비밀번호 변경."""
    body = await request.json()
    new_pw = body.get("new_password", "")
    if len(new_pw) < 8:
        return JSONResponse({"detail": "비밀번호는 8자 이상이어야 합니다."}, status_code=400)
    change_password(user["id"], new_pw)
    return JSONResponse({"ok": True})


@router.post("/api/auth/invite")
async def api_create_invite(request: Request, user: dict = Depends(get_current_user)):
    """직원 초대 토큰 생성 — director 이상 + 베타 정책상 admin 클리닉만."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "초대 권한이 없습니다."}, status_code=403)

    if not is_admin_clinic(user):
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


@router.get("/api/auth/invite/verify")
async def api_verify_invite(token: str):
    """초대 토큰 유효성 확인 (온보딩 페이지 초기 로드용)."""
    invite = verify_invite(token)
    if not invite:
        return JSONResponse({"valid": False, "detail": "유효하지 않거나 만료된 초대 링크입니다."}, status_code=400)
    return JSONResponse({"valid": True, "email": invite["email"], "role": invite["role"]})


@router.post("/api/auth/onboard")
async def api_onboard(request: Request):
    """온보딩 완료 — 비밀번호 설정 + JWT 발급."""
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


@router.post("/api/auth/forgot-password")
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
        return JSONResponse({"ok": True}, status_code=200)

    now = _time_now()
    last = _forgot_pw_last_request.get(email)
    if last and (now - last) < _FORGOT_PW_RATE_LIMIT_SEC:
        wait = int(_FORGOT_PW_RATE_LIMIT_SEC - (now - last)) or 1
        return JSONResponse(
            {"detail": f"잠시 후 다시 시도해주세요 ({wait}초)."},
            status_code=429,
        )
    _forgot_pw_last_request[email] = now

    from db_manager import get_db
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, role, clinic_id FROM users WHERE email = ? AND is_active = 1",
            (email,),
        ).fetchone()

    if not user:
        return JSONResponse({"ok": True}, status_code=200)

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


# ─────────────────────────────────────────────────────────────────
# 베타 신청 (공개)
# ─────────────────────────────────────────────────────────────────

_EMAIL_RE = _re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@router.post("/api/beta/apply")
async def beta_apply(request: Request):
    """
    베타/일반 신청 접수 (공개 엔드포인트).

    IP당 5분 창 3회 제한. 30일 미가입 시 자동 만료.
    """
    ip = get_real_ip(request) or "unknown"
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
    tos_consent = bool(body.get("tos_consent"))            # 이용약관 동의 (필수)
    privacy_consent = bool(body.get("privacy_consent"))    # 개인정보 수집·이용 동의 (필수)
    # 하위 호환: 구버전 클라이언트가 terms_consent 단일 필드만 보낼 경우 둘 다로 간주
    if not tos_consent and not privacy_consent and bool(body.get("terms_consent")):
        tos_consent = True
        privacy_consent = True
    marketing_consent = 1 if bool(body.get("marketing_consent")) else 0
    application_type = (body.get("application_type") or "beta").strip().lower()
    if application_type not in ("beta", "general"):
        application_type = "beta"

    if not name or not clinic_name:
        return JSONResponse({"detail": "이름과 한의원명을 입력해 주세요."}, status_code=400)
    if not _EMAIL_RE.match(email):
        return JSONResponse({"detail": "유효한 이메일 주소를 입력해 주세요."}, status_code=400)
    if not tos_consent:
        return JSONResponse(
            {"detail": "이용약관에 동의해 주세요."}, status_code=400,
        )
    if not privacy_consent:
        return JSONResponse(
            {"detail": "개인정보 수집·이용에 동의해 주세요."}, status_code=400,
        )

    user_agent = (request.headers.get("user-agent") or "")[:500]

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    expires_iso = (now_dt + timedelta(days=APPLICANT_EXPIRY_DAYS)).isoformat()

    from db_manager import get_db
    with get_db() as conn:
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
        logging.getLogger(__name__).warning("beta E1 이메일 실패: %s", exc)

    # E2: 관리자 알림 (fail-soft)
    try:
        from plan_notify import send_beta_admin_notify
        await asyncio.to_thread(
            send_beta_admin_notify, name, clinic_name, email, note, applicant_id,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("beta E2 관리자 알림 실패: %s", exc)

    return JSONResponse({"ok": True})
