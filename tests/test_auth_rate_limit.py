"""
test_auth_rate_limit.py — K-4 로그인 무차별 대입 차단 테스트

대상:
  - auth_manager.count_failed_logins_by_ip / count_failed_logins_by_email 헬퍼
  - routers/auth.py /api/auth/login 라우트의 429 차단 로직

테스트 격리: tmp_path SQLite DB 사용 (test_auth.py 의 mem_db 패턴 차용).
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    """tmp_path SQLite + 기본 clinic·user 시드."""
    import db_manager
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_manager, "DB_PATH", db_path)
    db_manager.init_db()

    from auth_manager import hash_password
    with db_manager.get_db() as conn:
        conn.execute("INSERT INTO clinics (id, name, max_slots) VALUES (1, '테스트한의원', 5)")
        conn.execute(
            "INSERT INTO users (id, clinic_id, email, hashed_password, role) "
            "VALUES (1, 1, 'owner@test.com', ?, 'chief_director')",
            (hash_password("pass1234"),),
        )
    return db_manager


def _record(success: bool, ip: str = "1.2.3.4", email: str = "x@test.com"):
    from auth_manager import record_login_attempt
    record_login_attempt(
        user_id=None, email=email, clinic_id=None,
        ip=ip, user_agent="test", success=success,
        failure_reason=None if success else "invalid_credentials",
    )


# ── 헬퍼 단위 테스트 ─────────────────────────────────────────────

class TestCountFailedLoginsByIp:
    def test_no_attempts_returns_zero(self, mem_db):
        from auth_manager import count_failed_logins_by_ip
        assert count_failed_logins_by_ip("9.9.9.9") == 0

    def test_counts_only_failures(self, mem_db):
        from auth_manager import count_failed_logins_by_ip
        _record(success=False, ip="1.1.1.1")
        _record(success=False, ip="1.1.1.1")
        _record(success=True, ip="1.1.1.1")  # 성공은 카운트 안 됨
        assert count_failed_logins_by_ip("1.1.1.1") == 2

    def test_isolated_per_ip(self, mem_db):
        from auth_manager import count_failed_logins_by_ip
        _record(success=False, ip="1.1.1.1")
        _record(success=False, ip="2.2.2.2")
        assert count_failed_logins_by_ip("1.1.1.1") == 1
        assert count_failed_logins_by_ip("2.2.2.2") == 1

    def test_empty_ip_returns_zero(self, mem_db):
        from auth_manager import count_failed_logins_by_ip
        assert count_failed_logins_by_ip("") == 0
        assert count_failed_logins_by_ip(None) == 0  # type: ignore


class TestCountFailedLoginsByEmail:
    def test_counts_only_failures(self, mem_db):
        from auth_manager import count_failed_logins_by_email
        _record(success=False, email="a@test.com")
        _record(success=False, email="a@test.com")
        _record(success=True, email="a@test.com")
        assert count_failed_logins_by_email("a@test.com") == 2

    def test_isolated_per_email(self, mem_db):
        from auth_manager import count_failed_logins_by_email
        _record(success=False, email="a@test.com")
        _record(success=False, email="b@test.com")
        assert count_failed_logins_by_email("a@test.com") == 1
        assert count_failed_logins_by_email("b@test.com") == 1


# ── 라우트 통합 테스트 ───────────────────────────────────────────

class TestLoginRateLimit:
    def _client(self, mem_db):
        """TestClient with main app + mem_db monkeypatched."""
        from fastapi.testclient import TestClient
        # main 모듈은 우리가 monkeypatch 한 db_manager 를 같은 import 로 본다
        import main
        return TestClient(main.app)

    def test_under_limit_returns_401_normally(self, mem_db):
        client = self._client(mem_db)
        # IP 5회 실패 — 임계 10 미만
        for _ in range(5):
            r = client.post("/api/auth/login",
                            json={"email": "owner@test.com", "password": "wrong"})
            assert r.status_code == 401

    def test_ip_limit_blocks_at_threshold(self, mem_db):
        client = self._client(mem_db)
        # 이메일 분산 (이메일 임계는 안 건드리고 IP 만 채움)
        for i in range(10):
            r = client.post("/api/auth/login",
                            json={"email": f"u{i}@test.com", "password": "wrong"})
            assert r.status_code == 401, f"iter {i}"
        # 11번째 차단
        r = client.post("/api/auth/login",
                        json={"email": "u99@test.com", "password": "wrong"})
        assert r.status_code == 429
        assert "잠시" in r.json()["detail"]

    def test_email_limit_blocks_at_threshold(self, mem_db):
        client = self._client(mem_db)
        # 같은 이메일 5회 실패 → 6번째 차단
        for i in range(5):
            r = client.post("/api/auth/login",
                            json={"email": "owner@test.com", "password": "wrong"})
            assert r.status_code == 401
        r = client.post("/api/auth/login",
                        json={"email": "owner@test.com", "password": "wrong"})
        assert r.status_code == 429

    def test_different_email_not_blocked(self, mem_db):
        client = self._client(mem_db)
        # 이메일 A 5회 실패 → 차단
        for _ in range(5):
            client.post("/api/auth/login",
                        json={"email": "a@test.com", "password": "wrong"})
        # 이메일 B 는 영향 없음 (단, IP 카운트는 누적)
        r = client.post("/api/auth/login",
                        json={"email": "b@test.com", "password": "wrong"})
        assert r.status_code == 401  # 401 (정상 실패), 429 아님
