"""
dependencies.py — 라우터 공용 의존성

routers/* 어디서든 import 해서 사용. main.py도 backward-compat 으로 같은 함수를 wrap.

포함 항목:
  - is_admin_clinic       : 베타 정책 — ADMIN_CLINIC_ID 일치 여부
  - require_admin         : ADMIN_SECRET Bearer 토큰 검증 (CLI 전용)
  - require_admin_or_session : Bearer 또는 chief_director 세션
  - require_announce_admin : 공지 작성 권한

주의: 단순 의존성만 모음. 도메인 로직은 각 라우터 또는 *_manager.py 에 둠.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Request

from auth_manager import COOKIE_NAME, decode_token


def _admin_clinic_id() -> Optional[int]:
    """환경변수 ADMIN_CLINIC_ID 를 정수로 파싱. 미설정 시 1 (기본 시드 클리닉)."""
    raw = os.getenv("ADMIN_CLINIC_ID", "1")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_admin_clinic(user: dict) -> bool:
    """베타 정책: ADMIN_CLINIC_ID 와 일치하는 클리닉만 직원 초대·관리 기능 허용.

    정식 서비스 출시 시 본 함수를 제거하거나 항상 True 반환으로 전환.
    """
    admin_cid = _admin_clinic_id()
    if admin_cid is None:
        return False
    try:
        return int(user.get("clinic_id", 0)) == admin_cid
    except (TypeError, ValueError):
        return False


def require_admin(request: Request) -> None:
    """Bearer <ADMIN_SECRET> 검증. 실패 시 HTTPException."""
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret:
        raise HTTPException(status_code=403, detail="관리자 기능이 비활성화되어 있습니다.")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != admin_secret:
        raise HTTPException(status_code=401, detail="인증 실패")


def require_admin_or_session(request: Request) -> None:
    """세션 쿠키(chief_director + ADMIN_CLINIC_ID) 또는 ADMIN_SECRET Bearer.

    브라우저 진입에는 세션, CLI 스크립트에는 Bearer 사용.
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
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, role, clinic_id FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="세션이 만료되었습니다.")
        admin_cid = _admin_clinic_id()
        if row["role"] != "chief_director" or admin_cid is None or int(row["clinic_id"]) != admin_cid:
            raise HTTPException(status_code=403, detail="관리자 권한이 없습니다.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="세션 검증 실패")


def require_announce_admin(user: dict) -> None:
    """공지 작성·수정·삭제 권한: ADMIN_CLINIC_ID + chief_director."""
    if not (is_admin_clinic(user) and user.get("role") == "chief_director"):
        raise HTTPException(status_code=403, detail="공지 작성 권한이 없습니다.")
