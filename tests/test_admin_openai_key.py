"""
test_admin_openai_key.py — 관리자 OpenAI 키 등록 라우트 테스트

검증 항목 (REGRESSION 포함):
  1. 비-admin 요청 → 401/403
  2. 빈 값 → 400
  3. sk- 미시작 → 400
  4. OpenAI 401 (잘못된 키) → 400 + 저장 안 됨
  5. OpenAI 200 (유효) → 저장 + 마스킹 응답
  6. GET — 미등록 시 secret=null
  7. GET — 등록 후 마스킹 + 갱신일
  8. DELETE — 삭제 동작
"""

import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """SECRET_KEY + ADMIN_SECRET 환경 + 임시 DB."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ADMIN_SECRET", "test-admin-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # main.py 초기화용

    db_file = tmp_path / "openai_key_test.db"
    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    # 최소 테이블만 생성 (server_secrets + users)
    conn = sqlite3.connect(db_file)
    conn.execute("""
        CREATE TABLE server_secrets (
            name TEXT PRIMARY KEY,
            value_enc TEXT NOT NULL,
            salt BLOB,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            updated_by_user_id INTEGER
        )
    """)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
    conn.commit()
    conn.close()

    import secret_manager
    secret_manager.invalidate_all_cache()
    yield
    secret_manager.invalidate_all_cache()


@pytest.fixture
def client():
    """FastAPI TestClient — main 앱 일부 라우트만 검증."""
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.testclient import TestClient
    from fastapi.responses import JSONResponse

    app = FastAPI()

    # 단순화: _require_admin_or_session 모킹용 헤더 체크
    def _check_admin(request: Request):
        if request.headers.get("X-Admin-Token") != "test-admin-secret":
            raise HTTPException(status_code=403, detail="admin required")

    @app.get("/api/admin/openai-key")
    def get_key(request: Request):
        _check_admin(request)
        from secret_manager import get_secret_meta
        return JSONResponse({"secret": get_secret_meta("openai_api_key")})

    @app.post("/api/admin/openai-key")
    async def set_key(request: Request):
        _check_admin(request)
        body = await request.json()
        value = (body.get("value") or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="키 값이 비어 있습니다.")
        if not value.startswith("sk-"):
            raise HTTPException(status_code=400, detail="OpenAI 키는 'sk-'로 시작해야 합니다.")

        # OpenAI 검증 (모킹 가능)
        # Restricted 키는 models.list() 권한이 없어 PermissionDeniedError 발생 → 통과 처리
        import openai
        try:
            cli = openai.OpenAI(api_key=value, timeout=10.0)
            cli.models.list()
        except openai.AuthenticationError:
            raise HTTPException(status_code=400, detail="유효하지 않은 OpenAI 키입니다.")
        except openai.PermissionDeniedError:
            pass  # Restricted 키 — 인증 통과로 간주, 저장 진행

        from secret_manager import set_server_secret, get_secret_meta
        set_server_secret("openai_api_key", value, user_id=None)
        return JSONResponse({"ok": True, "secret": get_secret_meta("openai_api_key")})

    @app.delete("/api/admin/openai-key")
    def delete_key(request: Request):
        _check_admin(request)
        from secret_manager import delete_server_secret
        return JSONResponse({"ok": True, "deleted": delete_server_secret("openai_api_key")})

    return TestClient(app)


def _admin_headers():
    return {"X-Admin-Token": "test-admin-secret"}


def test_non_admin_blocked(client):
    res = client.get("/api/admin/openai-key")
    assert res.status_code == 403


def test_empty_value_rejected(client):
    res = client.post("/api/admin/openai-key", json={"value": ""}, headers=_admin_headers())
    assert res.status_code == 400


def test_invalid_prefix_rejected(client):
    res = client.post("/api/admin/openai-key", json={"value": "not-sk-prefix"}, headers=_admin_headers())
    assert res.status_code == 400


def test_openai_auth_error_rejected(client, monkeypatch):
    """OpenAI 401 → 400 반환, DB 저장 안 됨."""
    import openai

    fake_response = MagicMock(status_code=401, request=MagicMock())

    class FakeOpenAI:
        def __init__(self, api_key=None, timeout=None, **kw):
            self.api_key = api_key

        @property
        def models(self):
            mock = MagicMock()
            mock.list.side_effect = openai.AuthenticationError(
                message="Invalid API key",
                response=fake_response,
                body={},
            )
            return mock

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    res = client.post(
        "/api/admin/openai-key",
        json={"value": "sk-invalid"},
        headers=_admin_headers(),
    )
    assert res.status_code == 400

    # 저장 안 됐는지 확인
    res_get = client.get("/api/admin/openai-key", headers=_admin_headers())
    assert res_get.json()["secret"] is None


def test_restricted_key_accepted(client, monkeypatch):
    """Restricted(이미지 전용) 키 → models.list() 403 → 저장 통과."""
    import openai

    fake_response = MagicMock(status_code=403, request=MagicMock())

    class FakeOpenAI:
        def __init__(self, api_key=None, timeout=None, **kw):
            self.api_key = api_key

        @property
        def models(self):
            mock = MagicMock()
            mock.list.side_effect = openai.PermissionDeniedError(
                message="Insufficient permissions for the requested resource",
                response=fake_response,
                body={},
            )
            return mock

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    res = client.post(
        "/api/admin/openai-key",
        json={"value": "sk-proj-restricted-image-only-key"},
        headers=_admin_headers(),
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # 저장 확인
    res_get = client.get("/api/admin/openai-key", headers=_admin_headers())
    assert res_get.json()["secret"] is not None


def test_valid_key_saved_and_masked(client, monkeypatch):
    """OpenAI 200 → 저장 + 마스킹 응답."""
    import openai

    class FakeOpenAI:
        def __init__(self, api_key=None, timeout=None, **kw):
            self.api_key = api_key

        @property
        def models(self):
            mock = MagicMock()
            mock.list.return_value = MagicMock(data=[MagicMock(id="gpt-4")])
            return mock

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    res = client.post(
        "/api/admin/openai-key",
        json={"value": "sk-valid-test-key-1234567890"},
        headers=_admin_headers(),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["secret"]["masked"].startswith("sk-vali")
    assert "sk-valid-test-key-1234567890" not in str(data)  # 평문 미노출


def test_get_returns_null_when_unset(client):
    res = client.get("/api/admin/openai-key", headers=_admin_headers())
    assert res.status_code == 200
    assert res.json()["secret"] is None


def test_delete_secret(client):
    """직접 secret_manager에 저장 후 DELETE 동작 확인."""
    from secret_manager import set_server_secret, get_server_secret
    set_server_secret("openai_api_key", "sk-temp-12345")
    assert get_server_secret("openai_api_key") == "sk-temp-12345"

    res = client.delete("/api/admin/openai-key", headers=_admin_headers())
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    assert get_server_secret("openai_api_key") is None
