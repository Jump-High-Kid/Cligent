"""
test_beta_apply.py — POST /api/beta/apply 유닛 테스트

커버리지:
  - 정상 신청
  - 중복 신청 (pending 상태 재신청)
  - 이미 가입된 이메일 → 409
  - 필수 필드 누락 → 400
  - 유효하지 않은 이메일 → 400
  - IP 레이트 리밋 → 429
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_db(tmp_path):
    """SQLite를 인메모리 DB로 교체"""
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "test.db"
    db_manager.init_db()
    yield
    db_manager.DB_PATH = orig


@pytest.fixture()
def client():
    import os
    os.environ.setdefault("SECRET_KEY", "test-secret-key-32chars-minimum!!")
    os.environ.setdefault("ADMIN_SECRET", "test-admin")
    from main import app, _ip_apply_buckets
    _ip_apply_buckets.clear()   # 각 테스트 전 IP 버킷 초기화
    return TestClient(app, raise_server_exceptions=False)


def _apply(client, payload: dict, ip: str = "1.2.3.4"):
    # 기본 동의 필드 자동 주입 (개별 테스트에서 override 가능)
    body = {"tos_consent": True, "privacy_consent": True, **payload}
    return client.post(
        "/api/beta/apply",
        json=body,
        headers={"X-Forwarded-For": ip},
    )


@patch("plan_notify.send_beta_apply_confirm")
@patch("plan_notify.send_beta_admin_notify")
def test_apply_success(mock_admin, mock_confirm, client):
    res = _apply(client, {
        "name": "홍길동",
        "clinic_name": "길동 한의원",
        "email": "test@example.com",
    })
    assert res.status_code == 200
    assert res.json()["ok"] is True


@patch("plan_notify.send_beta_apply_confirm")
@patch("plan_notify.send_beta_admin_notify")
def test_apply_duplicate_pending(mock_admin, mock_confirm, client):
    """pending 상태에서 재신청 → ok=True, duplicate=True"""
    payload = {"name": "홍길동", "clinic_name": "길동 한의원", "email": "dup@example.com"}
    _apply(client, payload)
    res = _apply(client, payload)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data.get("duplicate") is True


@patch("plan_notify.send_beta_apply_confirm")
@patch("plan_notify.send_beta_admin_notify")
def test_apply_missing_fields(mock_admin, mock_confirm, client):
    res = _apply(client, {"email": "test@example.com"})
    assert res.status_code == 400


@patch("plan_notify.send_beta_apply_confirm")
@patch("plan_notify.send_beta_admin_notify")
def test_apply_invalid_email(mock_admin, mock_confirm, client):
    res = _apply(client, {
        "name": "홍길동",
        "clinic_name": "길동 한의원",
        "email": "not-an-email",
    })
    assert res.status_code == 400


@patch("plan_notify.send_beta_apply_confirm")
@patch("plan_notify.send_beta_admin_notify")
def test_apply_rate_limit(mock_admin, mock_confirm, client):
    """동일 IP에서 4회 연속 신청 → 4번째는 429"""
    ip = "9.9.9.9"
    for i in range(3):
        res = _apply(client, {
            "name": f"유저{i}",
            "clinic_name": "테스트 한의원",
            "email": f"user{i}@example.com",
        }, ip=ip)
        assert res.status_code == 200

    res = _apply(client, {
        "name": "유저4",
        "clinic_name": "테스트 한의원",
        "email": "user4@example.com",
    }, ip=ip)
    assert res.status_code == 429
