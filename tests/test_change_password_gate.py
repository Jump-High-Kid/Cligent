"""
test_change_password_gate.py — K-5 게이트 검증

POST /api/auth/change-password 는 must_change_pw=1 일 때만 허용.
일반 세션(must_change_pw=0)은 403 반환 — 세션 탈취로 PW 영구 교체 차단.
"""

import os
import sys
import pytest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

import auth_manager
import main as _main
from fastapi.testclient import TestClient


def _make_user(must_change_pw: int) -> dict:
    return {
        "id": 1,
        "clinic_id": 1,
        "email": "user@test.com",
        "role": "team_member",
        "must_change_pw": must_change_pw,
        "is_active": 1,
    }


@pytest.fixture()
def client():
    tc = TestClient(_main.app, raise_server_exceptions=True)
    yield tc
    _main.app.dependency_overrides.pop(auth_manager.get_current_user, None)


def test_change_password_allowed_when_must_change(client):
    """must_change_pw=1 → 변경 허용 (200)."""
    _main.app.dependency_overrides[auth_manager.get_current_user] = lambda: _make_user(1)
    with patch("routers.auth.change_password") as mock_cp:
        res = client.post(
            "/api/auth/change-password",
            json={"new_password": "newpassword123"},
        )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    mock_cp.assert_called_once_with(1, "newpassword123")


def test_change_password_blocked_when_normal_session(client):
    """must_change_pw=0 → 403 (K-5 핵심: 세션 탈취 방어)."""
    _main.app.dependency_overrides[auth_manager.get_current_user] = lambda: _make_user(0)
    with patch("routers.auth.change_password") as mock_cp:
        res = client.post(
            "/api/auth/change-password",
            json={"new_password": "newpassword123"},
        )
    assert res.status_code == 403
    assert "임시" in res.json()["detail"]
    mock_cp.assert_not_called()


def test_change_password_short_pw_still_400_in_must_change(client):
    """must_change_pw=1 인 사용자도 8자 미만은 400 (기존 검증 유지)."""
    _main.app.dependency_overrides[auth_manager.get_current_user] = lambda: _make_user(1)
    with patch("routers.auth.change_password") as mock_cp:
        res = client.post(
            "/api/auth/change-password",
            json={"new_password": "short"},
        )
    assert res.status_code == 400
    mock_cp.assert_not_called()


def test_change_password_short_pw_blocked_first_in_normal_session(client):
    """must_change_pw=0 + 짧은 PW → 게이트가 먼저 (403). 길이 검증 도달 안 함."""
    _main.app.dependency_overrides[auth_manager.get_current_user] = lambda: _make_user(0)
    with patch("routers.auth.change_password") as mock_cp:
        res = client.post(
            "/api/auth/change-password",
            json={"new_password": "short"},
        )
    assert res.status_code == 403
    mock_cp.assert_not_called()
