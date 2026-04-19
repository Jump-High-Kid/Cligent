"""
test_auth.py — auth_manager.py 유닛 테스트

실제 SQLite 파일 대신 인메모리 DB를 사용 (각 테스트 격리).
"""

import os
import sqlite3
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")

import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

from auth_manager import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token,
    COOKIE_NAME,
)
from fastapi import HTTPException


# ── 비밀번호 ─────────────────────────────────────────────────────

class TestPassword:
    def test_hash_and_verify_success(self):
        hashed = hash_password("mypassword123")
        assert verify_password("mypassword123", hashed)

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("secret")
        assert hashed != "secret"
        assert hashed.startswith("$2b$")

    def test_same_password_different_hashes(self):
        # bcrypt는 매번 다른 salt 사용
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)


# ── JWT ──────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode(self):
        token = create_access_token(user_id=1, clinic_id=10, role="director")
        payload = decode_token(token)
        assert payload["sub"] == "1"
        assert payload["clinic_id"] == 10
        assert payload["role"] == "director"

    def test_expired_token_raises(self):
        from jose import jwt as _jwt
        expired_payload = {
            "sub": "1",
            "clinic_id": 1,
            "role": "director",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        token = _jwt.encode(expired_payload, "test-secret-key-for-unit-tests", algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401

    def test_tampered_token_raises(self):
        token = create_access_token(1, 1, "director")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc:
            decode_token(tampered)
        assert exc.value.status_code == 401

    def test_invalid_token_raises(self):
        with pytest.raises(HTTPException) as exc:
            decode_token("not.a.jwt")
        assert exc.value.status_code == 401


# ── invite (DB 필요) ────────────────────────────────────────────

@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    """
    테스트용 임시 SQLite DB.
    db_manager의 DB_PATH를 tmp_path 안으로 교체.
    """
    import db_manager
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    db_manager.init_db()

    # 기본 clinic + user 시드
    with db_manager.get_db() as conn:
        conn.execute("INSERT INTO clinics (id, name, max_slots) VALUES (1, '테스트한의원', 5)")
        from auth_manager import hash_password
        conn.execute(
            "INSERT INTO users (id, clinic_id, email, hashed_password, role) "
            "VALUES (1, 1, 'owner@test.com', ?, 'chief_director')",
            (hash_password("pass1234"),),
        )
    return db_manager


class TestInvite:
    def test_create_invite_returns_token(self, mem_db):
        from auth_manager import create_invite
        token = create_invite(clinic_id=1, email="new@test.com", role="team_member", created_by=1)
        assert len(token) > 10

    def test_verify_valid_invite(self, mem_db):
        from auth_manager import create_invite, verify_invite
        token = create_invite(1, "staff@test.com", "team_leader", 1)
        invite = verify_invite(token)
        assert invite is not None
        assert invite["email"] == "staff@test.com"
        assert invite["role"] == "team_leader"

    def test_verify_nonexistent_token_returns_none(self, mem_db):
        from auth_manager import verify_invite
        assert verify_invite("totally-fake-token") is None

    def test_resend_returns_same_token(self, mem_db):
        from auth_manager import create_invite
        t1 = create_invite(1, "same@test.com", "team_member", 1)
        t2 = create_invite(1, "same@test.com", "team_member", 1)
        assert t1 == t2  # 미사용 토큰 재사용

    def test_duplicate_user_raises(self, mem_db):
        from auth_manager import create_invite
        with pytest.raises(ValueError, match="이미 등록된"):
            create_invite(1, "owner@test.com", "team_member", 1)

    def test_slot_full_raises(self, mem_db):
        from auth_manager import create_invite, hash_password
        # max_slots = 5, 이미 1명 있으므로 4명 더 추가
        with mem_db.get_db() as conn:
            for i in range(2, 6):
                conn.execute(
                    "INSERT INTO users (clinic_id, email, hashed_password, role) "
                    "VALUES (1, ?, ?, 'team_member')",
                    (f"user{i}@test.com", hash_password("pw")),
                )
        with pytest.raises(ValueError, match="슬롯"):
            create_invite(1, "overflow@test.com", "team_member", 1)

    def test_complete_onboarding_activates_user(self, mem_db):
        from auth_manager import create_invite, complete_onboarding, verify_invite
        token = create_invite(1, "newstaff@test.com", "team_member", 1)
        user = complete_onboarding(token, "newpass123")
        assert user["email"] == "newstaff@test.com"
        assert user["is_active"] == 1
        # 토큰이 소모되었는지 확인
        assert verify_invite(token) is None

    def test_complete_onboarding_invalid_token_raises(self, mem_db):
        from auth_manager import complete_onboarding
        with pytest.raises(ValueError, match="유효하지 않"):
            complete_onboarding("bad-token", "pw123")


# ── authenticate_user ────────────────────────────────────────────

class TestAuthenticateUser:
    def test_correct_credentials(self, mem_db):
        from auth_manager import authenticate_user
        user = authenticate_user("owner@test.com", "pass1234")
        assert user is not None
        assert user["role"] == "chief_director"

    def test_wrong_password(self, mem_db):
        from auth_manager import authenticate_user
        assert authenticate_user("owner@test.com", "wrongpass") is None

    def test_unknown_email(self, mem_db):
        from auth_manager import authenticate_user
        assert authenticate_user("nobody@test.com", "anything") is None

    def test_case_insensitive_email(self, mem_db):
        from auth_manager import authenticate_user
        user = authenticate_user("OWNER@TEST.COM", "pass1234")
        assert user is not None
