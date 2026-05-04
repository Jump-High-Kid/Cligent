"""
test_secret_manager.py — secret_manager.py 단위 테스트

검증 항목:
  1. SECRET_KEY 미설정 → RuntimeError
  2. set/get round-trip — 평문 그대로 복원
  3. mask_secret — 짧은 키 / 긴 키 마스킹
  4. invalidate_cache — 갱신 후 즉시 새 값 반영
  5. delete_server_secret — 삭제 후 None 반환
  6. get_server_secret 미존재 → None
  7. get_secret_meta — 평문 미노출 + 마스킹 + 갱신일
  8. 빈 값 / 빈 name → ValueError
"""

import os
import sys
import sqlite3
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """각 테스트마다 임시 SQLite + SECRET_KEY 환경변수 설정."""
    db_file = tmp_path / "secret_test.db"
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-please-change")

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    # 테스트용 테이블 생성 (init_db()는 너무 많은 테이블 만드므로 server_secrets만)
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS server_secrets (
            name              TEXT PRIMARY KEY,
            value_enc         TEXT NOT NULL,
            salt              BLOB,
            updated_at        TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            updated_by_user_id INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT
        )
    """)
    conn.commit()
    conn.close()

    # 캐시 비우기
    import secret_manager
    secret_manager.invalidate_all_cache()
    yield
    secret_manager.invalidate_all_cache()


def test_secret_key_missing_raises(monkeypatch):
    """SECRET_KEY 미설정 시 RuntimeError 발생."""
    monkeypatch.delenv("SECRET_KEY", raising=False)
    from secret_manager import _get_fernet
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _get_fernet()


def test_set_and_get_roundtrip():
    """평문 → 암호화 저장 → 복호화 후 동일."""
    from secret_manager import set_server_secret, get_server_secret
    plain = "sk-test-1234567890abcdef"
    set_server_secret("openai_api_key", plain)
    assert get_server_secret("openai_api_key") == plain


def test_get_unknown_returns_none():
    """미등록 키 조회 시 None."""
    from secret_manager import get_server_secret
    assert get_server_secret("nonexistent") is None


def test_mask_secret_long():
    from secret_manager import mask_secret
    plain = "sk-abc123def456ghi789jkl012"
    masked = mask_secret(plain)
    assert masked.startswith("sk-abc1")
    assert masked.endswith("jkl012"[-4:])
    assert "****" in masked


def test_mask_secret_short():
    from secret_manager import mask_secret
    assert mask_secret("") == "****"
    assert mask_secret("abc") == "****"
    assert mask_secret("12345678") == "****"


def test_invalidate_cache_after_update():
    """키 갱신 후 캐시가 즉시 invalidate되어 새 값 반환."""
    from secret_manager import set_server_secret, get_server_secret
    set_server_secret("openai_api_key", "sk-old-value")
    assert get_server_secret("openai_api_key") == "sk-old-value"  # 캐시 적재

    set_server_secret("openai_api_key", "sk-new-value")
    assert get_server_secret("openai_api_key") == "sk-new-value"  # 캐시 갱신 확인


def test_delete_secret():
    """삭제 후 None 반환."""
    from secret_manager import set_server_secret, delete_server_secret, get_server_secret
    set_server_secret("openai_api_key", "sk-temp")
    assert delete_server_secret("openai_api_key") is True
    assert get_server_secret("openai_api_key") is None
    assert delete_server_secret("openai_api_key") is False  # 이미 삭제됨


def test_empty_name_or_value_rejected():
    from secret_manager import set_server_secret
    with pytest.raises(ValueError):
        set_server_secret("", "value")
    with pytest.raises(ValueError):
        set_server_secret("name", "")


def test_get_secret_meta_no_plaintext_leak():
    """meta 응답에 평문이 포함되지 않음 (마스킹만)."""
    from secret_manager import set_server_secret, get_secret_meta
    plain = "sk-secret-must-not-leak-9876"
    set_server_secret("openai_api_key", plain)
    meta = get_secret_meta("openai_api_key")
    assert meta is not None
    assert "masked" in meta
    assert plain not in str(meta)  # 평문 미포함 확인
    assert meta["masked"].startswith("sk-secr")


def test_get_secret_meta_unknown_returns_none():
    from secret_manager import get_secret_meta
    assert get_secret_meta("nonexistent") is None
