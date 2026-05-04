"""
secret_manager.py — 서버 전역 비밀 관리 (관리자 전용, Fernet 암호화)

베타 단계: BYOAI 비활성, 모든 사용자가 server_secrets 테이블에 저장된
관리자 OpenAI 키를 공유 사용.

저장: server_secrets 테이블 (name TEXT PK, value_enc TEXT, updated_at, updated_by_user_id)
암호화: SECRET_KEY 환경변수에서 PBKDF2HMAC 파생 → Fernet (main.py와 호환)
캐시: 60초 TTL 메모리 캐시, 갱신 시 invalidate

미래 (M3+) BYOAI Lite 도입 시 clinic-scope 키와 별개로 운영.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 메모리 캐시: name → (plain_value, expires_at_monotonic)
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 60  # 초


def _build_fernet(salt: bytes):
    """SECRET_KEY + 주어진 salt 로 Fernet 인스턴스 파생."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY 환경변수가 설정되지 않았습니다.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    raw = kdf.derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))


# K-9: 마이그레이션 호환용 레거시 salt. 신규 row는 절대 사용하지 않음.
_LEGACY_SALT = b"cligent_v1"


def _get_fernet():
    """레거시 alias — 레거시 salt 기반. K-9 이전 코드 호환용."""
    return _build_fernet(_LEGACY_SALT)


def _encrypt_with_salt(plain: str) -> tuple[str, bytes]:
    """평문 → (암호문, 16바이트 random salt). K-9 신규 저장 경로."""
    salt = os.urandom(16)
    return _build_fernet(salt).encrypt(plain.encode()).decode(), salt


def _decrypt_with_salt(enc: str, salt: Optional[bytes]) -> str:
    """암호문 + 저장된 salt → 평문. salt None/빈값 시 레거시 salt 사용."""
    actual = salt if salt else _LEGACY_SALT
    return _build_fernet(actual).decrypt(enc.encode()).decode()


def mask_secret(plain: str) -> str:
    """sk-...abc1 형태로 마스킹 (UI 표시용)."""
    if not plain or len(plain) <= 8:
        return "****"
    return plain[:7] + "****" + plain[-4:]


def set_server_secret(name: str, value: str, user_id: Optional[int] = None) -> None:
    """
    서버 비밀 저장 (UPSERT) + 캐시 invalidate.
    호출자가 user_id를 알면 감사 로그용으로 같이 기록.
    """
    if not name or not value:
        raise ValueError("name과 value는 비어 있을 수 없습니다.")
    enc, salt = _encrypt_with_salt(value)
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO server_secrets (name, value_enc, salt, updated_at, updated_by_user_id)
            VALUES (?, ?, ?, datetime('now', 'utc'), ?)
            ON CONFLICT(name) DO UPDATE SET
                value_enc = excluded.value_enc,
                salt = excluded.salt,
                updated_at = excluded.updated_at,
                updated_by_user_id = excluded.updated_by_user_id
            """,
            (name, enc, salt, user_id),
        )
    invalidate_cache(name)
    logger.info("server_secret 갱신: name=%s by user_id=%s", name, user_id)


def get_server_secret(name: str) -> Optional[str]:
    """
    서버 비밀 평문 반환. 미존재·DB 장애 시 None.
    호출자가 None 처리 책임 (예: OpenAI 호출 전 None 체크).
    """
    cached = _cache.get(name)
    if cached is not None:
        plain, exp = cached
        if time.monotonic() < exp:
            return plain
        del _cache[name]

    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT value_enc, salt FROM server_secrets WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        plain = _decrypt_with_salt(row["value_enc"], row["salt"])
        _cache[name] = (plain, time.monotonic() + _CACHE_TTL)
        return plain
    except Exception as exc:
        logger.warning("get_server_secret 실패 (name=%s): %s", name, exc)
        return None


def get_secret_meta(name: str) -> Optional[dict]:
    """
    UI 표시용 메타데이터 (마스킹된 값 + updated_at + updated_by). 미존재 시 None.
    평문 키를 반환하지 않음 — 보안.
    """
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT s.value_enc, s.salt, s.updated_at, s.updated_by_user_id, u.email
                FROM server_secrets s
                LEFT JOIN users u ON u.id = s.updated_by_user_id
                WHERE s.name = ?
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        plain = _decrypt_with_salt(row["value_enc"], row["salt"])
        return {
            "name": name,
            "masked": mask_secret(plain),
            "updated_at": row["updated_at"],
            "updated_by_email": row["email"],
        }
    except Exception as exc:
        logger.warning("get_secret_meta 실패 (name=%s): %s", name, exc)
        return None


def delete_server_secret(name: str) -> bool:
    """비밀 삭제 + 캐시 invalidate. 삭제된 행이 있으면 True."""
    try:
        from db_manager import get_db
        with get_db() as conn:
            cur = conn.execute("DELETE FROM server_secrets WHERE name = ?", (name,))
            deleted = cur.rowcount > 0
        invalidate_cache(name)
        if deleted:
            logger.info("server_secret 삭제: name=%s", name)
        return deleted
    except Exception as exc:
        logger.warning("delete_server_secret 실패 (name=%s): %s", name, exc)
        return False


def invalidate_cache(name: str) -> None:
    """비밀 갱신·삭제 후 캐시 무효화."""
    _cache.pop(name, None)


def invalidate_all_cache() -> None:
    """전체 캐시 비움 (테스트 또는 SECRET_KEY 회전 시)."""
    _cache.clear()
