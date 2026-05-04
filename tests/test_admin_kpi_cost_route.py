"""
test_admin_kpi_cost_route.py — /api/admin/kpi/cost + billing-recon 통합 테스트 (Commit 8b)

검증:
  - GET  /api/admin/kpi/cost                   200 + 키 구조
  - POST /api/admin/kpi/cost/billing-recon     UPSERT + diff 계산
  - 미인증 401/403
  - 입력 형식 오류 400
  - admin_billing_recon 테이블 idempotent ALTER 확인
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
    db_manager.DB_PATH = tmp_path / "kpi_cost_route_test.db"
    db_manager.init_db()
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots, is_admin_clinic) "
            "VALUES (1, 'Cligent Admin', 100, 1)"
        )
    # plan_guard 캐시 비움
    import plan_guard
    plan_guard._plan_cache.clear()
    yield
    db_manager.DB_PATH = orig


@pytest.fixture
def client(monkeypatch):
    os.environ.setdefault("SECRET_KEY", "test-secret-key-32chars-minimum!!")
    os.environ.setdefault("ADMIN_CLINIC_ID", "1")
    os.environ.setdefault("ADMIN_USER_ID", "1")
    # main 임포트가 load_dotenv(override=True) 로 .env 값을 덮어쓰므로
    # 그 다음에 monkeypatch.setenv 호출해야 효과 있음 (test_admin_kpi_route 패턴 동일).
    from main import app
    monkeypatch.setenv("ADMIN_SECRET", "test-admin")
    monkeypatch.setenv("KOREAEXIM_API_KEY", "")
    return TestClient(app, raise_server_exceptions=False)


def _auth():
    return {"Authorization": "Bearer test-admin"}


# ── 스키마 ────────────────────────────────────────────────


def test_admin_billing_recon_table_exists():
    """init_db 가 admin_billing_recon 테이블을 생성했는지 (idempotent ALTER)."""
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='admin_billing_recon'"
        ).fetchone()
        assert row is not None


# ── GET /api/admin/kpi/cost ──────────────────────────────


def test_kpi_cost_json_returns_expected_structure(client):
    res = client.get("/api/admin/kpi/cost", headers=_auth())
    assert res.status_code == 200
    d = res.json()
    # top-level keys
    for key in (
        "days", "rate", "cost_per_blog", "margin", "billing_recon",
        "plan_distribution", "avg_usage", "image_calls", "loss_risk",
        "estimate_30users", "alerts",
    ):
        assert key in d, f"missing key: {key}"

    # rate 환율 — KOREAEXIM_API_KEY 비었으나 디스크 캐시 있을 수 있음.
    # source 는 원본 보존 (koreaexim 또는 fallback). 캐시 hit 일 땐 cached=True.
    assert d["rate"]["source"] in ("fallback", "koreaexim")
    assert d["rate"]["rate"] > 0
    assert "cached" in d["rate"]

    # margin: standard / pro 두 plan
    assert "standard" in d["margin"]
    assert "pro" in d["margin"]
    for plan in ("standard", "pro"):
        m = d["margin"][plan]
        for k in ("revenue_krw", "cost_krw", "margin_krw",
                  "margin_pct", "status"):
            assert k in m

    # plan_distribution: total/standard/pro/trial/free
    pd = d["plan_distribution"]
    assert pd["total"] >= 1  # 시드된 admin clinic 1건


def test_kpi_cost_days_query_parameter(client):
    res = client.get("/api/admin/kpi/cost?days=7", headers=_auth())
    assert res.status_code == 200
    assert res.json()["days"] == 7


def test_kpi_cost_days_clamped_to_safe_range(client):
    # days=0 → default 30 (의미 없는 값은 fallback), days=999 → 365 (최대 클램프)
    res = client.get("/api/admin/kpi/cost?days=0", headers=_auth())
    assert res.json()["days"] == 30
    res = client.get("/api/admin/kpi/cost?days=999", headers=_auth())
    assert res.json()["days"] == 365


def test_kpi_cost_unauthenticated_rejected(client):
    res = client.get("/api/admin/kpi/cost")
    assert res.status_code != 200


# ── POST /api/admin/kpi/cost/billing-recon ──────────────


def test_billing_recon_upsert_and_retrieve(client):
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        headers=_auth(),
        json={"year_month": "2026-04", "openai_invoice_usd": 12.34},
    )
    assert res.status_code == 200
    d = res.json()
    assert d["ok"] is True
    assert isinstance(d["recon"], list)
    assert len(d["recon"]) == 1
    row = d["recon"][0]
    assert row["year_month"] == "2026-04"
    assert row["openai_invoice_usd"] == pytest.approx(12.34)

    # UPSERT — 같은 year_month 재호출 시 갱신
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        headers=_auth(),
        json={"year_month": "2026-04", "openai_invoice_usd": 20.00},
    )
    assert res.status_code == 200
    assert res.json()["recon"][0]["openai_invoice_usd"] == pytest.approx(20.00)


def test_billing_recon_invalid_year_month_400(client):
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        headers=_auth(),
        json={"year_month": "2026/04", "openai_invoice_usd": 10.0},
    )
    assert res.status_code == 400


def test_billing_recon_missing_fields_400(client):
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        headers=_auth(),
        json={"year_month": "2026-04"},
    )
    assert res.status_code == 400


def test_billing_recon_negative_invoice_400(client):
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        headers=_auth(),
        json={"year_month": "2026-04", "openai_invoice_usd": -1.0},
    )
    assert res.status_code == 400


def test_billing_recon_unauthenticated_rejected(client):
    res = client.post(
        "/api/admin/kpi/cost/billing-recon",
        json={"year_month": "2026-04", "openai_invoice_usd": 10.0},
    )
    assert res.status_code != 200
