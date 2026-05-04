"""
test_admin_kpi_route.py — /admin/kpi + /api/admin/kpi 라우트 통합 테스트 (Commit 7c)

검증:
  - /admin/kpi 200 (HTML)
  - /api/admin/kpi 200 + 정해진 키 구조
  - 미인증 401/403
"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "kpi_route_test.db"
    db_manager.init_db()
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots, is_admin_clinic) "
            "VALUES (1, 'Cligent Admin', 100, 1)"
        )
    yield
    db_manager.DB_PATH = orig


@pytest.fixture
def client(monkeypatch):
    os.environ.setdefault("SECRET_KEY", "test-secret-key-32chars-minimum!!")
    os.environ.setdefault("ADMIN_CLINIC_ID", "1")
    os.environ.setdefault("ADMIN_USER_ID", "1")
    from main import app
    monkeypatch.setenv("ADMIN_SECRET", "test-admin")
    return TestClient(app, raise_server_exceptions=False)


def _auth():
    return {"Authorization": "Bearer test-admin"}


def test_kpi_html_page_returns_200(client):
    res = client.get("/admin/kpi", headers=_auth())
    assert res.status_code == 200
    body = res.text
    assert "베타 KPI" in body
    # 차트 컨테이너 존재 여부 (UI 회귀 가드 가벼운 버전)
    assert "chart-cost-by-day" in body
    assert "chart-turn-hist" in body
    assert "chart-module-sat" in body


def test_kpi_json_returns_expected_structure(client):
    res = client.get("/api/admin/kpi", headers=_auth())
    assert res.status_code == 200
    d = res.json()
    assert d["days"] == 14
    # cost
    assert "total_usd" in d["cost"]
    assert "by_kind" in d["cost"]
    assert "by_day" in d["cost"]
    # modules
    assert isinstance(d["modules"], list)
    # turns
    assert "total" in d["turns"]
    assert "completion_rate" in d["turns"]
    assert "avg_turns" in d["turns"]
    assert "histogram" in d["turns"]
    assert len(d["turns"]["histogram"]) == 6
    # clinics
    assert isinstance(d["clinics"], list)
    # 시드된 admin clinic 1건은 활동 0 행으로 포함
    cids = [c["clinic_id"] for c in d["clinics"]]
    assert 1 in cids


def test_kpi_html_unauthenticated_rejected(client):
    res = client.get("/admin/kpi")
    # 인증 없으면 401 또는 redirect (실제 구현 따라). 200 은 안 됨.
    assert res.status_code != 200


def test_kpi_json_unauthenticated_rejected(client):
    res = client.get("/api/admin/kpi")
    assert res.status_code != 200
