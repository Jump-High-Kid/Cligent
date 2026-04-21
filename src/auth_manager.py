"""
auth_manager.py — JWT 인증, bcrypt 해시, 초대 토큰 관리

주요 기능:
  - JWT 생성/검증 (httpOnly 쿠키, HS256)
  - 비밀번호 bcrypt 해시/검증
  - 72시간 유효 1회용 초대 토큰 생성/검증
  - FastAPI 의존성 주입용 get_current_user()
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext

from db_manager import get_db

# ── 설정 ──────────────────────────────────────────────────────────

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8
INVITE_EXPIRE_HOURS = 72
COOKIE_NAME = "cligent_token"

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_secret_key() -> str:
    """SECRET_KEY 로드 — 없으면 서버 시작 시 이미 검증 실패했어야 함"""
    key = os.getenv("SECRET_KEY", "")
    if not key:
        raise RuntimeError("SECRET_KEY가 설정되지 않았습니다.")
    return key


# ── 비밀번호 ─────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


# ── JWT ──────────────────────────────────────────────────────────

def create_access_token(user_id: int, clinic_id: int, role: str) -> str:
    """8시간 유효 JWT 생성"""
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "clinic_id": clinic_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """
    JWT 검증 후 페이로드 반환.
    만료 또는 서명 오류 시 HTTPException(401) 발생.
    """
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="인증 토큰이 유효하지 않습니다.",
        )


# ── FastAPI 의존성 ─────────────────────────────────────────────

def get_current_user(
    cligent_token: Optional[str] = Cookie(default=None),
) -> dict:
    """
    FastAPI 의존성 주입용.
    쿠키에서 JWT를 읽어 현재 사용자 정보 반환.

    사용법:
        @app.get("/protected")
        async def handler(user = Depends(get_current_user)):
            ...
    """
    if not cligent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다.",
        )
    payload = decode_token(cligent_token)

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, clinic_id, email, role, is_active, must_change_pw "
            "FROM users WHERE id = ?",
            (int(payload["sub"]),),
        ).fetchone()

    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="비활성화된 계정입니다.",
        )

    return dict(user)


def require_role(*roles: str):
    """
    역할 제한 의존성 팩토리.

    사용법:
        @app.post("/admin")
        async def handler(user = Depends(require_role("chief_director", "director"))):
            ...
    """
    def _check(user: dict = None):
        from fastapi import Depends
        from module_manager import role_has_access
        if user is None:
            raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
        if not role_has_access(user["role"], list(roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="접근 권한이 없습니다.",
            )
        return user
    return _check


# ── 로그인 ───────────────────────────────────────────────────────

def authenticate_user(email: str, password: str) -> Optional[dict]:
    """
    이메일 + 비밀번호 검증.
    성공 시 user dict, 실패 시 None.
    """
    with get_db() as conn:
        user = conn.execute(
            "SELECT id, clinic_id, email, hashed_password, role, is_active, must_change_pw "
            "FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()

    if not user:
        return None
    if not user["hashed_password"]:
        return None
    if not verify_password(password, user["hashed_password"]):
        return None
    if not user["is_active"]:
        return None

    return dict(user)


# ── 초대 토큰 ─────────────────────────────────────────────────────

def create_invite(clinic_id: int, email: str, role: str, created_by: int) -> str:
    """
    72시간 유효 초대 토큰 생성 후 DB 저장.
    이미 활성 초대가 있으면 기존 토큰 재사용 (재전송 지원).
    반환: 초대 토큰 문자열
    """
    email = email.lower().strip()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRE_HOURS)
    ).isoformat()

    with get_db() as conn:
        # 미사용 + 미만료 초대가 있으면 재사용
        existing = conn.execute(
            "SELECT token FROM invites "
            "WHERE clinic_id = ? AND email = ? AND used_at IS NULL "
            "AND datetime(expires_at) > datetime('now')",
            (clinic_id, email),
        ).fetchone()
        if existing:
            return existing["token"]

        # 해당 이메일의 사용자가 이미 있는지 확인
        user_exists = conn.execute(
            "SELECT id FROM users WHERE email = ? AND clinic_id = ?",
            (email, clinic_id),
        ).fetchone()
        if user_exists:
            raise ValueError(f"{email}은 이미 등록된 사용자입니다.")

        # 슬롯 확인
        clinic = conn.execute(
            "SELECT max_slots FROM clinics WHERE id = ?", (clinic_id,)
        ).fetchone()
        active_users = conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE clinic_id = ? AND is_active = 1",
            (clinic_id,),
        ).fetchone()
        if clinic and active_users["cnt"] >= clinic["max_slots"]:
            raise ValueError("사용자 슬롯이 가득 찼습니다. 관리자에게 슬롯 추가를 요청하세요.")

        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO invites (clinic_id, email, role, token, expires_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clinic_id, email, role, token, expires_at, created_by),
        )

    return token


def create_reinvite(clinic_id: int, email: str, role: str, created_by: int) -> str:
    """
    기존 사용자용 비밀번호 재설정 토큰 생성.
    create_invite()와 달리 user_exists, max_slots 체크 없이 토큰만 생성.
    이미 활성 토큰이 있으면 새로 생성(강제 갱신).
    """
    email = email.lower().strip()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRE_HOURS)
    ).isoformat()

    with get_db() as conn:
        # 기존 미사용 토큰은 만료 처리 (새 링크 발급)
        conn.execute(
            "UPDATE invites SET expires_at = datetime('now') "
            "WHERE clinic_id = ? AND email = ? AND used_at IS NULL",
            (clinic_id, email),
        )
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO invites (clinic_id, email, role, token, expires_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clinic_id, email, role, token, expires_at, created_by),
        )
    return token


def verify_invite(token: str) -> Optional[dict]:
    """
    초대 토큰 검증.
    유효하면 invite dict (clinic_id, email, role 포함), 아니면 None.
    """
    with get_db() as conn:
        invite = conn.execute(
            "SELECT id, clinic_id, email, role, expires_at, used_at "
            "FROM invites WHERE token = ?",
            (token,),
        ).fetchone()

    if not invite:
        return None
    if invite["used_at"]:
        return None  # 이미 사용됨

    expires = datetime.fromisoformat(invite["expires_at"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return None  # 만료됨

    return dict(invite)


def complete_onboarding(token: str, password: str) -> dict:
    """
    온보딩 완료: 비밀번호 설정 + 사용자 활성화 + 초대 토큰 소모.
    반환: 생성된 user dict
    """
    invite = verify_invite(token)
    if not invite:
        raise ValueError("유효하지 않거나 만료된 초대 링크입니다.")

    hashed = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    with get_db() as conn:
        # 기존 사용자 확인 (재초대 = 비밀번호 재설정)
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? AND clinic_id = ?",
            (invite["email"], invite["clinic_id"]),
        ).fetchone()

        if existing:
            # 비밀번호 재설정: 기존 사용자 업데이트
            conn.execute(
                "UPDATE users SET hashed_password = ?, must_change_pw = 0, is_active = 1 WHERE id = ?",
                (hashed, existing["id"]),
            )
            user_id = existing["id"]
        else:
            # 신규 사용자 생성
            cur = conn.execute(
                "INSERT INTO users (clinic_id, email, hashed_password, role, is_active, must_change_pw) "
                "VALUES (?, ?, ?, ?, 1, 0)",
                (invite["clinic_id"], invite["email"], hashed, invite["role"]),
            )
            user_id = cur.lastrowid

        # 토큰 소모
        conn.execute(
            "UPDATE invites SET used_at = ? WHERE id = ?",
            (now, invite["id"]),
        )

        user = conn.execute(
            "SELECT id, clinic_id, email, role, is_active, must_change_pw "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    return dict(user)


def change_password(user_id: int, new_password: str) -> None:
    """비밀번호 변경 + must_change_pw 초기화"""
    hashed = hash_password(new_password)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET hashed_password = ?, must_change_pw = 0 WHERE id = ?",
            (hashed, user_id),
        )
