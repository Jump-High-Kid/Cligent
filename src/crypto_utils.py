"""
crypto_utils.py — 클리닉별 Anthropic API 키 암호화/복호화.

PBKDF2HMAC(SHA256, salt='cligent_v1', 100k iterations) + Fernet.
SECRET_KEY 환경 변수에서 키 파생. SECRET_KEY 미설정 시 RuntimeError.

main.py 분할(v0.9.0)로 routers/clinic.py 와 main.py(어드민 OpenAI 키 등)가
공용 사용. 이전에는 main.py 내부 함수 _encrypt_key/_decrypt_key/_mask_key 였음.
"""
from __future__ import annotations

import base64
import os


def _get_fernet():
    """SECRET_KEY 기반 Fernet 인스턴스. 호출 시점에 env 조회."""
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    secret = os.getenv("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY 환경 변수가 설정되지 않았습니다.")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"cligent_v1",
        iterations=100_000,
    )
    raw = kdf.derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_key(plain: str) -> str:
    """평문 → Fernet 암호문 (base64 str)."""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_key(enc: str) -> str:
    """Fernet 암호문 → 평문. 키 변경·손상 시 InvalidToken."""
    return _get_fernet().decrypt(enc.encode()).decode()


def mask_key(plain: str) -> str:
    """API 키 마스킹: 앞 10자 + **** + 뒤 4자. 8자 이하는 ****만."""
    if len(plain) <= 8:
        return "****"
    return plain[:10] + "****" + plain[-4:]
