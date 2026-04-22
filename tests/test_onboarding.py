"""
test_onboarding.py — API 키 온보딩 위자드 관련 테스트

테스트 대상:
  1. /api/auth/me 응답에 api_key_configured 포함 여부
  2. /api/settings/clinic/ai (POST) 저장 시 api_key_configured=1 설정
  3. clear_key=true 시 api_key_configured=0 으로 리셋
  4. /api/settings/clinic/ai/onboarding-start 가 onboarding_started_at 기록
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

import auth_manager
import main as _main
from fastapi.testclient import TestClient


# ── 공통 픽스처 ───────────────────────────────────────────────────

FAKE_USER = {
    "id": 1,
    "clinic_id": 1,
    "email": "owner@test.com",
    "role": "chief_director",
    "must_change_pw": 0,
    "is_active": 1,
}


@pytest.fixture()
def clinic_row():
    return {
        "id": 1,
        "name": "테스트 한의원",
        "api_key_enc": None,
        "api_key_configured": 0,
        "onboarding_started_at": None,
        "model": None,
        "monthly_budget_krw": None,
    }


@pytest.fixture()
def client(clinic_row, monkeypatch):
    """TestClient with auth bypassed and DB mocked."""
    import db_manager
    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        conn = _FakeConn(clinic_row)
        try:
            yield conn
        except Exception:
            raise

    monkeypatch.setattr(db_manager, "get_db", fake_get_db)
    # main.py 내부에서도 db_manager.get_db를 사용하므로 동일하게 패치
    monkeypatch.setattr(_main, "__import__", lambda name, *a, **kw: (
        db_manager if name == "db_manager" else __builtins__.__import__(name, *a, **kw)
    ), raising=False)

    # Fernet 실 암호화 우회
    fake_fernet = MagicMock()
    fake_fernet.encrypt = lambda b: b"ENC:" + (b if isinstance(b, bytes) else b.encode())
    fake_fernet.decrypt = lambda b: b[4:]
    monkeypatch.setattr(_main, "_get_fernet", lambda: fake_fernet)

    # auth 의존성 override: auth_manager.get_current_user 를 키로 등록
    _main.app.dependency_overrides[auth_manager.get_current_user] = lambda: FAKE_USER

    tc = TestClient(_main.app, raise_server_exceptions=True)
    yield tc

    _main.app.dependency_overrides.clear()


class _FakeConn:
    """인메모리 딕셔너리 기반 가짜 DB 커넥션."""

    def __init__(self, store: dict):
        self._store = store

    def execute(self, sql: str, params=()):
        sql_u = sql.strip().upper()
        result = MagicMock()
        result.fetchone = lambda: dict(self._store)
        result.fetchall = lambda: [dict(self._store)]
        result.lastrowid = 1

        if "UPDATE CLINICS" in sql_u:
            has_api_key_enc = "API_KEY_ENC" in sql_u
            has_configured_1 = "API_KEY_CONFIGURED=1" in sql_u.replace(" ", "")
            has_configured_0 = "API_KEY_CONFIGURED=0" in sql_u.replace(" ", "")
            has_enc_null = "API_KEY_ENC=NULL" in sql_u.replace(" ", "")
            has_onboarding = "ONBOARDING_STARTED_AT" in sql_u

            if has_api_key_enc and has_configured_1:
                # save_clinic_ai: api_key_enc=?, ..., api_key_configured=1
                # params: (model, clinic_id, budget, api_key_enc, clinic_id)
                self._store["api_key_enc"] = params[3] if len(params) > 3 else params[0]
                self._store["api_key_configured"] = 1
            elif has_enc_null and has_configured_0:
                # clear_key 경로
                self._store["api_key_enc"] = None
                self._store["api_key_configured"] = 0
            elif has_onboarding and "COALESCE" in sql_u:
                # onboarding-start: COALESCE → 최초 1회만 설정
                if self._store["onboarding_started_at"] is None:
                    self._store["onboarding_started_at"] = params[0]

        return result

    def commit(self): pass
    def rollback(self): pass
    def close(self): pass


# ── 테스트 ────────────────────────────────────────────────────────

class TestApiKeyConfiguredFlag:
    def test_me_returns_false_when_no_key(self, client, clinic_row):
        """API 키 미등록 시 /api/auth/me 가 api_key_configured=false 반환."""
        clinic_row["api_key_enc"] = None
        clinic_row["api_key_configured"] = 0

        res = client.get("/api/auth/me")
        assert res.status_code == 200
        data = res.json()
        assert "api_key_configured" in data
        assert data["api_key_configured"] is False

    def test_me_returns_true_when_key_exists(self, client, clinic_row):
        """API 키 등록 후 /api/auth/me 가 api_key_configured=true 반환."""
        clinic_row["api_key_enc"] = b"ENC:sk-ant-test"
        clinic_row["api_key_configured"] = 1

        res = client.get("/api/auth/me")
        assert res.status_code == 200
        assert res.json()["api_key_configured"] is True

    def test_save_ai_sets_configured_flag(self, client, clinic_row):
        """AI 키 저장 시 DB의 api_key_configured 가 1로 변경된다."""
        clinic_row["api_key_configured"] = 0

        res = client.post("/api/settings/clinic/ai", json={"api_key": "sk-ant-fake"})
        assert res.status_code == 200
        assert clinic_row["api_key_configured"] == 1

    def test_clear_key_resets_configured_flag(self, client, clinic_row):
        """clear_key=true 로 키 삭제 시 api_key_configured 가 0으로 리셋된다."""
        clinic_row["api_key_enc"] = b"ENC:sk-ant-test"
        clinic_row["api_key_configured"] = 1

        res = client.post("/api/settings/clinic/ai", json={"clear_key": True})
        assert res.status_code == 200
        assert clinic_row["api_key_configured"] == 0

    def test_onboarding_start_records_timestamp(self, client, clinic_row):
        """/api/settings/clinic/ai/onboarding-start 호출 시 onboarding_started_at 기록."""
        clinic_row["onboarding_started_at"] = None

        res = client.post("/api/settings/clinic/ai/onboarding-start")
        assert res.status_code == 200
        assert clinic_row["onboarding_started_at"] is not None
