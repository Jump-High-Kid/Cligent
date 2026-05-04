"""
crypto_utils.py — 클리닉별 Anthropic API 키 암호화/복호화.

K-9 (per-row salt, 2026-05-04):
- 신규 row: 16바이트 random salt 생성 → Fernet 암호화
- 레거시 row (salt 없음): salt=b'cligent_v1' fallback (마이그레이션 호환)
- DB+SECRET_KEY 둘 노출 시에도 row별로 키 파생이 달라 일괄 복호화 차단

PBKDF2HMAC(SHA256, salt=per-row, 100k iterations) + Fernet.
SECRET_KEY 환경 변수에서 키 파생. SECRET_KEY 미설정 시 RuntimeError.
"""
from __future__ import annotations

import base64
import os
from typing import Optional, Tuple


# K-9: 마이그레이션 호환용 레거시 salt. 신규 row는 절대 사용하지 않음.
LEGACY_SALT = b"cligent_v1"


def _build_fernet(salt: bytes):
    """SECRET_KEY + 주어진 salt 로 Fernet 인스턴스 파생."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY 환경 변수가 설정되지 않았습니다.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    raw = kdf.derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))


def _get_fernet():
    """
    레거시 alias — 레거시 salt(b'cligent_v1') 기반 Fernet.
    K-9 이전 코드/테스트(monkeypatch) 호환용. 신규 코드는 _build_fernet(salt) 직접 사용.
    """
    return _build_fernet(LEGACY_SALT)


def encrypt_key(plain: str) -> Tuple[str, bytes]:
    """
    평문 → (Fernet 암호문 base64 str, 16바이트 random salt).
    K-9: 호출자는 반드시 salt 도 함께 DB에 저장해야 함.
    """
    salt = os.urandom(16)
    enc = _build_fernet(salt).encrypt(plain.encode()).decode()
    return enc, salt


def decrypt_key(enc: str, salt: Optional[bytes] = None) -> str:
    """
    Fernet 암호문 → 평문.
    salt None/빈값 시 레거시 salt(b'cligent_v1') 사용 — K-9 마이그레이션 전 row 호환.
    키 변경·손상 시 InvalidToken.
    """
    actual_salt = salt if salt else LEGACY_SALT
    return _build_fernet(actual_salt).decrypt(enc.encode()).decode()


def mask_key(plain: str) -> str:
    """API 키 마스킹: 앞 10자 + **** + 뒤 4자. 8자 이하는 ****만."""
    if len(plain) <= 8:
        return "****"
    return plain[:10] + "****" + plain[-4:]
