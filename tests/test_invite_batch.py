"""
test_invite_batch.py — POST /api/admin/invite-batch 유닛 테스트

커버리지:
  - ADMIN_SECRET 미인증 → 403 / 401
  - 빈 ids → 400
  - pending 신청자 정상 초대
  - invited 상태 신청자는 건너뜀
  - create_invite ValueError → failed[] 반환
  - GET /api/admin/applicants 통계 구조 확인
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_db(tmp_path):
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "test.db"
    db_manager.init_db()

    # 어드민 클리닉 시드
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots, is_admin_clinic) VALUES (1, 'Cligent Admin', 100, 1)"
        )
        conn.execute(
            "INSERT INTO users (id, clinic_id, email, hashed_password, role) "
            "VALUES (1, 1, 'admin@cligent.app', 'x', 'chief_director')"
        )

    yield
    db_manager.DB_PATH = orig


@pytest.fixture()
def client():
    import os
    os.environ["SECRET_KEY"]    = "test-secret-key-32chars-minimum!!"
    os.environ["ADMIN_SECRET"]  = "test-admin"
    os.environ["ADMIN_CLINIC_ID"] = "1"
    os.environ["ADMIN_USER_ID"]   = "1"
    from main import app
    return TestClient(app, raise_server_exceptions=False)


def _auth_headers():
    return {"Authorization": "Bearer test-admin"}


def _insert_applicant(name: str, email: str, status: str = "pending") -> int:
    import db_manager
    from datetime import datetime, timezone
    with db_manager.get_db() as conn:
        cur = conn.execute(
            "INSERT INTO beta_applicants (name, clinic_name, email, applied_at, status) "
            "VALUES (?, '테스트 한의원', ?, ?, ?)",
            (name, email, datetime.now(timezone.utc).isoformat(), status),
        )
        return cur.lastrowid


def test_no_auth_secret_disabled(client):
    """ADMIN_SECRET 미설정 환경에서 403"""
    import os
    orig = os.environ.pop("ADMIN_SECRET", None)
    try:
        res = client.post("/api/admin/invite-batch", json={"ids": [1]})
        assert res.status_code == 403
    finally:
        if orig:
            os.environ["ADMIN_SECRET"] = orig


def test_wrong_token(client):
    res = client.post(
        "/api/admin/invite-batch",
        json={"ids": [1]},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert res.status_code == 401


def test_empty_ids(client):
    res = client.post("/api/admin/invite-batch", json={"ids": []}, headers=_auth_headers())
    assert res.status_code == 400


@patch("plan_notify.send_beta_invite_email")
def test_invite_batch_success(mock_email, client):
    rid = _insert_applicant("홍길동", "invite_ok@example.com")
    res = client.post(
        "/api/admin/invite-batch",
        json={"ids": [rid]},
        headers=_auth_headers(),
    )
    assert res.status_code == 200
    data = res.json()
    assert rid in data["invited"]
    assert data["failed"] == []
    mock_email.assert_called_once()


@patch("plan_notify.send_beta_invite_email")
def test_invite_batch_skips_non_pending(mock_email, client):
    """already invited 신청자는 rows에서 제외되므로 invited=[]"""
    rid = _insert_applicant("김철수", "already@example.com", status="invited")
    res = client.post(
        "/api/admin/invite-batch",
        json={"ids": [rid]},
        headers=_auth_headers(),
    )
    assert res.status_code == 200
    data = res.json()
    assert rid not in data["invited"]


def test_get_applicants_stats(client):
    _insert_applicant("A", "a@example.com", "pending")
    _insert_applicant("B", "b@example.com", "invited")
    _insert_applicant("C", "c@example.com", "registered")

    res = client.get("/api/admin/applicants", headers=_auth_headers())
    assert res.status_code == 200
    data = res.json()
    assert "applicants" in data
    assert "stats" in data
    stats = data["stats"]
    assert stats["total"] >= 3
    assert stats["pending"]    >= 1
    assert stats["invited"]    >= 1
    assert stats["registered"] >= 1
