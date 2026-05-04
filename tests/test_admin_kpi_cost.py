"""
test_admin_kpi_cost.py — admin_kpi_cost.py 9 함수 단위 테스트 (Commit 8a)

격리 SQLite — clinics + blog_chat_sessions + image_sessions + cost_logs
+ admin_billing_recon 최소 스키마 시드 후 집계 검증.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _future_iso(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    db_file = tmp_path / "admin_kpi_cost_test.db"

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)
    # plan_guard 의 _plan_cache 도 비움 (테스트간 격리)
    import plan_guard
    plan_guard._plan_cache.clear()

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            plan_id TEXT DEFAULT 'free',
            plan_expires_at TEXT,
            trial_expires_at TEXT
        );
        CREATE TABLE blog_chat_sessions (
            session_id TEXT PRIMARY KEY,
            clinic_id INTEGER NOT NULL,
            user_id INTEGER,
            stage TEXT NOT NULL DEFAULT 'topic',
            state_json TEXT NOT NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            last_active_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE image_sessions (
            session_id TEXT PRIMARY KEY,
            clinic_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0,
            blog_session_id TEXT,
            image_session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE admin_billing_recon (
            year_month TEXT PRIMARY KEY,
            openai_invoice_usd REAL NOT NULL DEFAULT 0,
            our_logged_usd REAL NOT NULL DEFAULT 0,
            recorded_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    yield


# ── helpers ────────────────────────────────────────────────


def _seed_clinic(cid: int, name: str = "테스트한의원",
                 plan_id: str = "free",
                 plan_expires_at=None, trial_expires_at=None):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, plan_id, plan_expires_at, "
            "trial_expires_at) VALUES (?, ?, ?, ?, ?)",
            (cid, name, plan_id, plan_expires_at, trial_expires_at),
        )


def _seed_cost(clinic_id: int, kind: str, usd: float, *,
               blog_session_id=None, days_ago: int = 0):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO cost_logs (clinic_id, kind, cost_usd, "
            "blog_session_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (clinic_id, kind, usd, blog_session_id, _iso_days_ago(days_ago)),
        )


def _seed_chat(session_id: str, clinic_id: int, *,
               user_id=None, stage: str = "done", days_ago: int = 0):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO blog_chat_sessions "
            "(session_id, clinic_id, user_id, stage, state_json, "
            " turn_count, created_at, last_active_at) "
            "VALUES (?, ?, ?, ?, '{}', 0, ?, ?)",
            (session_id, clinic_id, user_id, stage,
             _iso_days_ago(days_ago), _iso_days_ago(days_ago)),
        )


def _seed_image_session(clinic_id: int, session_id: str, days_ago: int = 0):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO image_sessions (session_id, clinic_id, created_at) "
            "VALUES (?, ?, ?)",
            (session_id, clinic_id, _iso_days_ago(days_ago)),
        )


# ── A1 get_cost_per_blog ──────────────────────────────────


class TestCostPerBlog:
    def test_empty_returns_zeros(self):
        from admin_kpi_cost import get_cost_per_blog
        out = get_cost_per_blog(days=30)
        assert out["blog_count"] == 0
        assert out["avg_usd"]["total"] == 0.0
        assert out["days"] == 30

    def test_aggregates_per_blog_session(self):
        from admin_kpi_cost import get_cost_per_blog
        _seed_clinic(1)
        # blog A: text 0.10, init 0.50
        _seed_cost(1, "anthropic_blog", 0.10, blog_session_id="A")
        _seed_cost(1, "openai_image_init", 0.50, blog_session_id="A")
        # blog B: text 0.20, init 0.50, edit 0.10
        _seed_cost(1, "anthropic_blog", 0.20, blog_session_id="B")
        _seed_cost(1, "openai_image_init", 0.50, blog_session_id="B")
        _seed_cost(1, "openai_image_edit", 0.10, blog_session_id="B")

        out = get_cost_per_blog(days=30)
        assert out["blog_count"] == 2
        # text 평균 = (0.10 + 0.20) / 2 = 0.15
        assert out["avg_usd"]["text"] == pytest.approx(0.15)
        # init 평균 = (0.50 + 0.50) / 2 = 0.50
        assert out["avg_usd"]["image_init"] == pytest.approx(0.50)
        # edit 평균 = 0.10 / 2 = 0.05 (분모 전체 blog_count, regen 0 인 편 포함)
        assert out["avg_usd"]["image_edit"] == pytest.approx(0.05)
        assert out["avg_usd"]["image_regen"] == 0.0
        assert out["avg_usd"]["total"] == pytest.approx(0.70)

    def test_excludes_admin_image_test(self):
        from admin_kpi_cost import get_cost_per_blog
        _seed_clinic(1)
        _seed_cost(1, "anthropic_blog", 0.10, blog_session_id="A")
        # 어드민 테스트 — KPI 제외
        _seed_cost(1, "openai_image_admin", 5.00, blog_session_id="ADMIN")
        out = get_cost_per_blog(days=30)
        assert out["blog_count"] == 1
        assert out["avg_usd"]["total"] == pytest.approx(0.10)

    def test_excludes_null_blog_session_id(self):
        from admin_kpi_cost import get_cost_per_blog
        _seed_clinic(1)
        _seed_cost(1, "anthropic_blog", 1.00, blog_session_id=None)
        _seed_cost(1, "anthropic_blog", 0.10, blog_session_id="A")
        out = get_cost_per_blog(days=30)
        assert out["blog_count"] == 1
        assert out["avg_usd"]["text"] == pytest.approx(0.10)


# ── A2 get_margin_summary ────────────────────────────────


class TestMarginSummary:
    def test_empty_returns_full_revenue_as_margin(self):
        from admin_kpi_cost import get_margin_summary
        out = get_margin_summary(days=30, usd_to_krw_rate=1400)
        # cost 0 → 마진 = 매출
        assert out["standard"]["cost_krw"] == 0.0
        assert out["standard"]["margin_pct"] == 100.0
        assert out["pro"]["margin_pct"] == 100.0
        assert out["standard"]["status"] == "ok"
        assert out["pro"]["status"] == "ok"

    def test_pro_critical_status_when_margin_low(self):
        from admin_kpi_cost import get_margin_summary
        _seed_clinic(1)
        # 편당 USD 합 = 2.5 → KRW 환산 = 3,500 → Pro 매출 3,488 → 마진 -0.34%
        _seed_cost(1, "anthropic_blog", 0.50, blog_session_id="A")
        _seed_cost(1, "openai_image_init", 2.00, blog_session_id="A")
        out = get_margin_summary(days=30, usd_to_krw_rate=1400)
        assert out["pro"]["margin_pct"] < 0
        assert out["pro"]["status"] == "critical"
        # Standard 매출 4,967 - 변동비 3,500 → 마진 약 29.5% (still positive)
        assert out["standard"]["margin_pct"] > 0
        # Standard status 는 항상 ok (Pro 만 임계값 적용)
        assert out["standard"]["status"] == "ok"


# ── B1 get_plan_distribution ──────────────────────────────


class TestPlanDistribution:
    def test_classifies_by_resolve_effective_plan(self):
        from admin_kpi_cost import get_plan_distribution
        # standard (활성)
        _seed_clinic(1, "S", plan_id="standard",
                     plan_expires_at=_future_iso(30))
        # pro (활성)
        _seed_clinic(2, "P", plan_id="pro",
                     plan_expires_at=_future_iso(30))
        # trial (활성)
        _seed_clinic(3, "T", plan_id="free",
                     trial_expires_at=_future_iso(10))
        # free (만료된 trial → free 로 분류)
        _seed_clinic(4, "F", plan_id="free",
                     trial_expires_at=_iso_days_ago(1))

        out = get_plan_distribution()
        assert out["total"] == 4
        assert out["standard"] == 1
        assert out["pro"] == 1
        assert out["trial"] == 1
        assert out["free"] == 1
        # paid 비율: standard 50% / pro 50%
        assert out["standard_pct"] == 50.0
        assert out["pro_pct"] == 50.0


# ── B2 get_avg_usage_per_user ─────────────────────────────


class TestAvgUsagePerUser:
    def test_empty_returns_zero(self):
        from admin_kpi_cost import get_avg_usage_per_user
        out = get_avg_usage_per_user(days=30)
        assert out["user_count"] == 0
        assert out["avg_blogs_per_user_month"] == 0.0

    def test_per_user_monthly_normalization(self):
        from admin_kpi_cost import get_avg_usage_per_user
        _seed_clinic(1)
        # user 1: 2 done, user 2: 1 done — 30일 윈도우 / 30일 = ×1
        _seed_chat("s1", 1, user_id=10, stage="done")
        _seed_chat("s2", 1, user_id=10, stage="done")
        _seed_chat("s3", 1, user_id=20, stage="done")
        # 미완료 — 분자에서 제외
        _seed_chat("s4", 1, user_id=10, stage="topic")

        out = get_avg_usage_per_user(days=30)
        assert out["user_count"] == 2
        # 평균 1인당 done = 3 / 2 = 1.5
        assert out["avg_blogs_per_user_month"] == pytest.approx(1.5)
        # Standard 30 대비 사용률 = 1.5 / 30 * 100 = 5.0
        assert out["blog_usage_pct_standard"] == pytest.approx(5.0)


# ── B3 get_image_calls_per_blog ──────────────────────────


class TestImageCallsPerBlog:
    def test_aggregates_avg_calls(self):
        from admin_kpi_cost import get_image_calls_per_blog
        _seed_clinic(1)
        # blog A: init 1, regen 2, edit 1
        for _ in range(1):
            _seed_cost(1, "openai_image_init", 0.05, blog_session_id="A")
        for _ in range(2):
            _seed_cost(1, "openai_image_regen", 0.05, blog_session_id="A")
        for _ in range(1):
            _seed_cost(1, "openai_image_edit", 0.05, blog_session_id="A")
        # blog B: init 1, edit 3
        _seed_cost(1, "openai_image_init", 0.05, blog_session_id="B")
        for _ in range(3):
            _seed_cost(1, "openai_image_edit", 0.05, blog_session_id="B")

        out = get_image_calls_per_blog(days=30)
        assert out["blog_count"] == 2
        assert out["init"] == pytest.approx(1.0)   # (1+1)/2
        assert out["regen"] == pytest.approx(1.0)  # (2+0)/2
        assert out["edit"] == pytest.approx(2.0)   # (1+3)/2


# ── B4 get_pro_loss_risk_clinics ─────────────────────────


class TestProLossRiskClinics:
    def test_excludes_trial_and_break_even_clinics(self):
        from admin_kpi_cost import get_pro_loss_risk_clinics
        # Pro 적자: 1편 × 3,488 매출 vs cost 5 USD × 1400 = 7,000원
        _seed_clinic(1, "Pro적자", plan_id="pro",
                     plan_expires_at=_future_iso(30))
        _seed_chat("s1", 1, stage="done")
        _seed_cost(1, "openai_image_init", 5.0, blog_session_id="s1")

        # Standard 흑자: 5편 × 4,967 매출 vs cost 0.5 USD × 1400 = 700원
        _seed_clinic(2, "Std흑자", plan_id="standard",
                     plan_expires_at=_future_iso(30))
        for i in range(5):
            _seed_chat(f"s2-{i}", 2, stage="done")
        _seed_cost(2, "anthropic_blog", 0.5, blog_session_id="s2-0")

        # trial — 매출 0 이지만 패널 노이즈 회피 위해 제외
        _seed_clinic(3, "Trial", plan_id="free",
                     trial_expires_at=_future_iso(10))
        _seed_chat("s3", 3, stage="done")
        _seed_cost(3, "openai_image_init", 5.0, blog_session_id="s3")

        rows = get_pro_loss_risk_clinics(days=30, top=10,
                                         usd_to_krw_rate=1400)
        # Pro 적자 클리닉만
        assert len(rows) == 1
        assert rows[0]["clinic_id"] == 1
        assert rows[0]["plan"] == "pro"
        assert rows[0]["loss_krw"] > 0


# ── A3 billing_recon ─────────────────────────────────────


class TestBillingRecon:
    def test_record_and_retrieve(self):
        from admin_kpi_cost import record_billing_recon, get_billing_recon
        _seed_clinic(1)
        # 2026-04 비용 시드
        _seed_cost(1, "openai_image_init", 1.50, blog_session_id="A",
                   days_ago=20)
        # year_month 가 days_ago=20 인 row 의 created_at 월과 일치해야 함
        # 테스트 안정성을 위해 직접 계산
        target_month = (datetime.now(timezone.utc) - timedelta(days=20)) \
            .strftime("%Y-%m")

        ok = record_billing_recon(target_month, openai_invoice_usd=2.00)
        assert ok is True

        rows = get_billing_recon(months_back=3)
        assert len(rows) == 1
        assert rows[0]["year_month"] == target_month
        assert rows[0]["openai_invoice_usd"] == pytest.approx(2.00)
        assert rows[0]["our_logged_usd"] == pytest.approx(1.50)
        assert rows[0]["diff_pct"] == pytest.approx(25.0)

    def test_invalid_year_month_format(self):
        from admin_kpi_cost import record_billing_recon
        assert record_billing_recon("2026/04", 100.0) is False
        assert record_billing_recon("invalid", 100.0) is False
        assert record_billing_recon("", 100.0) is False

    def test_negative_invoice_rejected(self):
        from admin_kpi_cost import record_billing_recon
        assert record_billing_recon("2026-04", -1.0) is False


# ── C1 estimate_annual_revenue_30users ──────────────────


class TestEstimate30Users:
    def test_uses_b1_weights_when_available(self):
        from admin_kpi_cost import estimate_annual_revenue_30users
        # standard 1, pro 1 → 50/50 가중치
        _seed_clinic(1, "S", plan_id="standard",
                     plan_expires_at=_future_iso(30))
        _seed_clinic(2, "P", plan_id="pro",
                     plan_expires_at=_future_iso(30))
        out = estimate_annual_revenue_30users(days=30, usd_to_krw_rate=1400)
        # blog_count 0 → blogs_per_month 0 → 30인 매출 0
        assert out["weights"]["standard"] == pytest.approx(0.5)
        assert out["weights"]["pro"] == pytest.approx(0.5)
        assert out["annual_revenue_30users_krw"] == 0.0


# ── C2 get_policy_alerts ─────────────────────────────────


class TestPolicyAlerts:
    def test_empty_db_no_alerts(self):
        from admin_kpi_cost import get_policy_alerts
        # 빈 DB → margin 100% (cost 0) → 알림 없음
        assert get_policy_alerts(days=30, usd_to_krw_rate=1400) == []

    def test_critical_alert_when_pro_margin_low(self):
        from admin_kpi_cost import get_policy_alerts
        _seed_clinic(1)
        # Pro 매출 3,488 < 변동비 5,600 (4 USD × 1400) → 마진 -60% < -30%
        _seed_cost(1, "openai_image_init", 4.0, blog_session_id="A")
        alerts = get_policy_alerts(days=30, usd_to_krw_rate=1400)
        kinds = {a["kind"] for a in alerts}
        assert "pro_margin_low" in kinds
        critical = [a for a in alerts if a["kind"] == "pro_margin_low"][0]
        assert critical["severity"] == "critical"

    def test_edit_high_alert(self):
        from admin_kpi_cost import get_policy_alerts
        _seed_clinic(1)
        # blog A: edit 4회 (init 1)
        _seed_cost(1, "openai_image_init", 0.05, blog_session_id="A")
        for _ in range(4):
            _seed_cost(1, "openai_image_edit", 0.05, blog_session_id="A")
        alerts = get_policy_alerts(days=30, usd_to_krw_rate=1400)
        kinds = {a["kind"] for a in alerts}
        assert "edit_high" in kinds
