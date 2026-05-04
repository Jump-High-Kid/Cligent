"""
test_per_row_salt.py — K-9 per-row salt 검증

crypto_utils + secret_manager 두 모듈이 random per-row salt 로
암호화/복호화하고, 다른 salt 로는 복호화 실패함을 보장.
레거시 row(salt NULL/빈값)는 b'cligent_v1' fallback 으로 복호화.
"""

import os
import sys
import sqlite3
import pytest
from unittest.mock import patch
from contextlib import contextmanager

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from cryptography.fernet import InvalidToken

import crypto_utils
import secret_manager


# ── crypto_utils 단위 ─────────────────────────────────────────────

class TestCryptoUtilsRoundtrip:
    def test_encrypt_returns_enc_and_salt(self):
        enc, salt = crypto_utils.encrypt_key("sk-ant-test-12345")
        assert isinstance(enc, str)
        assert isinstance(salt, bytes)
        assert len(salt) == 16  # os.urandom(16)

    def test_roundtrip_with_random_salt(self):
        plain = "sk-ant-secret-key-abcdef"
        enc, salt = crypto_utils.encrypt_key(plain)
        assert crypto_utils.decrypt_key(enc, salt) == plain

    def test_each_encrypt_uses_different_salt(self):
        e1, s1 = crypto_utils.encrypt_key("same")
        e2, s2 = crypto_utils.encrypt_key("same")
        # 같은 평문이라도 다른 salt → 다른 암호문
        assert s1 != s2
        assert e1 != e2

    def test_wrong_salt_fails(self):
        plain = "sk-ant-test"
        enc, salt = crypto_utils.encrypt_key(plain)
        wrong_salt = os.urandom(16)
        with pytest.raises(InvalidToken):
            crypto_utils.decrypt_key(enc, wrong_salt)

    def test_legacy_row_decrypt_with_none_salt(self):
        """salt=None 이면 레거시 b'cligent_v1' 사용 — 마이그레이션 전 row 호환."""
        plain = "legacy-secret"
        legacy_enc = crypto_utils._build_fernet(crypto_utils.LEGACY_SALT).encrypt(
            plain.encode()
        ).decode()
        assert crypto_utils.decrypt_key(legacy_enc, None) == plain

    def test_legacy_row_decrypt_with_empty_salt(self):
        plain = "legacy-secret"
        legacy_enc = crypto_utils._build_fernet(crypto_utils.LEGACY_SALT).encrypt(
            plain.encode()
        ).decode()
        assert crypto_utils.decrypt_key(legacy_enc, b"") == plain

    def test_get_fernet_alias_uses_legacy(self):
        """레거시 _get_fernet() alias 는 legacy salt 와 동일해야 함 (호환성)."""
        f1 = crypto_utils._get_fernet()
        f2 = crypto_utils._build_fernet(crypto_utils.LEGACY_SALT)
        token = f1.encrypt(b"x")
        assert f2.decrypt(token) == b"x"

    def test_secret_key_required(self, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError):
            crypto_utils._build_fernet(b"any")


# ── secret_manager 통합 (in-memory SQLite) ────────────────────────

@pytest.fixture()
def mem_db(monkeypatch):
    """server_secrets 만 갖춘 in-memory DB 로 secret_manager 통합 테스트."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE server_secrets (
            name TEXT PRIMARY KEY,
            value_enc TEXT NOT NULL,
            salt BLOB,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            updated_by_user_id INTEGER
        )
        """
    )
    # get_secret_meta 가 LEFT JOIN users 하므로 빈 테이블이라도 필요
    conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)"
    )

    @contextmanager
    def fake_get_db():
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    import db_manager
    monkeypatch.setattr(db_manager, "get_db", fake_get_db)
    secret_manager.invalidate_all_cache()
    yield conn
    conn.close()
    secret_manager.invalidate_all_cache()


class TestSecretManagerSalt:
    def test_set_then_get_roundtrip(self, mem_db):
        secret_manager.set_server_secret("openai_api_key", "sk-test-abc", user_id=1)
        assert secret_manager.get_server_secret("openai_api_key") == "sk-test-abc"

    def test_set_stores_salt_in_db(self, mem_db):
        secret_manager.set_server_secret("openai_api_key", "sk-test", user_id=1)
        row = mem_db.execute(
            "SELECT salt FROM server_secrets WHERE name = 'openai_api_key'"
        ).fetchone()
        assert row["salt"] is not None
        assert len(row["salt"]) == 16

    def test_legacy_row_decrypts_with_null_salt(self, mem_db):
        """salt 컬럼 NULL 인 레거시 row 도 정상 복호화 (마이그레이션 전 호환)."""
        # 레거시 salt 로 직접 암호화 후 INSERT
        legacy_enc = crypto_utils._build_fernet(crypto_utils.LEGACY_SALT).encrypt(
            b"legacy-key"
        ).decode()
        mem_db.execute(
            "INSERT INTO server_secrets (name, value_enc, salt) VALUES (?, ?, NULL)",
            ("legacy_secret", legacy_enc),
        )
        mem_db.commit()
        secret_manager.invalidate_all_cache()
        assert secret_manager.get_server_secret("legacy_secret") == "legacy-key"

    def test_two_secrets_have_different_salts(self, mem_db):
        secret_manager.set_server_secret("a", "valA", user_id=1)
        secret_manager.set_server_secret("b", "valB", user_id=1)
        rows = mem_db.execute("SELECT name, salt FROM server_secrets").fetchall()
        salts = {r["name"]: r["salt"] for r in rows}
        assert salts["a"] != salts["b"]

    def test_get_secret_meta_returns_masked(self, mem_db):
        secret_manager.set_server_secret("openai_api_key", "sk-1234567890abcd", user_id=1)
        meta = secret_manager.get_secret_meta("openai_api_key")
        assert meta is not None
        # 평문 노출 금지
        assert "1234567890abcd" not in meta["masked"]
        assert "****" in meta["masked"]

    def test_update_replaces_salt(self, mem_db):
        secret_manager.set_server_secret("k", "v1", user_id=1)
        old_salt = mem_db.execute(
            "SELECT salt FROM server_secrets WHERE name='k'"
        ).fetchone()["salt"]
        secret_manager.set_server_secret("k", "v2", user_id=2)
        new_salt = mem_db.execute(
            "SELECT salt FROM server_secrets WHERE name='k'"
        ).fetchone()["salt"]
        # 갱신 시 salt 도 새로 — 교차 노출 방어
        assert old_salt != new_salt
        assert secret_manager.get_server_secret("k") == "v2"
