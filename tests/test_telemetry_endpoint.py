"""
test_telemetry_endpoint.py — POST /api/telemetry/event 회귀 (Commit 4)

배경 (2026-05-04):
  클라이언트 (chat_state.js) 가 stuck/cancel 감지 시 fire-and-forget 으로
  이 엔드포인트에 POST. 서버는 인증된 사용자의 clinic_id 만 사용하고
  payload 의 clinic_id 는 무시 (보안).

검증 항목:
  1. 정상 요청 — 200 + JSONL 1줄
  2. 미인증 → 401
  3. 알 수 없는 kind → 400
  4. payload 의 clinic_id 무시, 인증된 user.clinic_id 사용
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from main import app  # noqa: E402
from auth_manager import get_current_user  # noqa: E402

FAKE_USER = {
    "id": 1, "clinic_id": 7, "role": "chief_director",
    "email": "test@test.com", "is_active": True,
}


@pytest.fixture
def client_authed(monkeypatch, tmp_path):
    """인증된 client + tmp telemetry 로그 경로"""
    import telemetry
    log = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(telemetry, "DEFAULT_LOG_PATH", log)
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield TestClient(app, raise_server_exceptions=False), log
    app.dependency_overrides.clear()


@pytest.fixture
def client_anon():
    """미인증 client"""
    app.dependency_overrides.clear()
    return TestClient(app, raise_server_exceptions=False)


def test_endpoint_writes_event(client_authed):
    client, log = client_authed
    res = client.post(
        "/api/telemetry/event",
        json={
            "kind": "stuck",
            "session_id": "abc-123",
            "stage": "generating",
            "context": {"reason": "test"},
        },
    )
    assert res.status_code == 200, res.text
    assert log.exists()
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["kind"] == "stuck"
    assert row["clinic_id"] == 7  # FAKE_USER.clinic_id
    assert row["session_id"] == "abc-123"
    assert row["stage"] == "generating"


def test_endpoint_requires_auth(client_anon):
    res = client_anon.post(
        "/api/telemetry/event",
        json={"kind": "stuck"},
    )
    assert res.status_code == 401


def test_endpoint_invalid_kind_returns_400(client_authed):
    client, log = client_authed
    res = client.post("/api/telemetry/event", json={"kind": "bogus"})
    assert res.status_code == 400
    # 잘못된 kind 는 JSONL 에 쓰면 안 됨
    assert not log.exists() or log.read_text(encoding="utf-8") == ""


def test_endpoint_ignores_payload_clinic_id(client_authed):
    """보안 — payload 에 다른 clinic_id 보내도 인증된 user.clinic_id 가 사용됨"""
    client, log = client_authed
    res = client.post(
        "/api/telemetry/event",
        json={"kind": "cancel", "clinic_id": 999},  # 위조 시도
    )
    assert res.status_code == 200
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["clinic_id"] == 7  # FAKE_USER.clinic_id, NOT 999
