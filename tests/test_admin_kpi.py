"""
test_admin_kpi.py — admin_kpi.py SQL 집계 함수 단위 테스트 (Commit 7b)

격리 SQLite — clinics + blog_chat_sessions + image_sessions + cost_logs + gallery_likes
최소 스키마 만들어 시드 후 집계 결과 검증.
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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """admin_kpi 가 의존하는 5개 테이블 최소 스키마."""
    db_file = tmp_path / "admin_kpi_test.db"

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
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
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            modules_json TEXT
        );
        CREATE TABLE cost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE gallery_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            image_index INTEGER NOT NULL,
            clinic_id INTEGER NOT NULL,
            module INTEGER,
            liked INTEGER NOT NULL DEFAULT 1,
            liked_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            UNIQUE(session_id, image_index)
        );
    """)
    conn.execute("INSERT INTO clinics (id, name) VALUES (1, '강남한의원')")
    conn.execute("INSERT INTO clinics (id, name) VALUES (2, '강북한의원')")
    conn.commit()
    conn.close()
    yield


# ── helpers ────────────────────────────────────────────────


def _seed_cost(clinic_id: int, kind: str, usd: float, days_ago: int = 0):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO cost_logs (clinic_id, kind, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?)",
            (clinic_id, kind, usd, _iso_days_ago(days_ago)),
        )


def _seed_chat(clinic_id: int, turn_count: int, stage: str = "done", days_ago: int = 0,
               session_id: str = ""):
    from db_manager import get_db
    sid = session_id or f"sid-{clinic_id}-{turn_count}-{stage}-{days_ago}"
    with get_db() as conn:
        conn.execute(
            "INSERT INTO blog_chat_sessions "
            "(session_id, clinic_id, stage, state_json, turn_count, "
            " created_at, last_active_at) "
            "VALUES (?, ?, ?, '{}', ?, ?, ?)",
            (sid, clinic_id, stage, turn_count,
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


def _seed_like(session_id: str, image_index: int, clinic_id: int,
               module, liked: int = 1):
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO gallery_likes "
            "(session_id, image_index, clinic_id, module, liked) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, image_index, clinic_id, module, liked),
        )


# ── get_cost_summary ──────────────────────────────────────


class TestCostSummary:
    def test_empty_db_returns_zeroes(self):
        from admin_kpi import get_cost_summary

        out = get_cost_summary(days=14)
        assert out["total_usd"] == 0.0
        assert out["by_kind"] == {}
        assert out["by_day"] == []
        assert out["days"] == 14

    def test_aggregates_by_kind_and_day(self):
        from admin_kpi import get_cost_summary

        _seed_cost(1, "anthropic_blog", 0.10, days_ago=0)
        _seed_cost(1, "anthropic_blog", 0.20, days_ago=0)
        _seed_cost(1, "openai_image_init", 0.50, days_ago=1)
        _seed_cost(2, "anthropic_meta", 0.05, days_ago=2)

        out = get_cost_summary(days=14)
        assert round(out["total_usd"], 4) == 0.85
        assert out["by_kind"]["anthropic_blog"] == pytest.approx(0.30)
        assert out["by_kind"]["openai_image_init"] == pytest.approx(0.50)
        assert out["by_kind"]["anthropic_meta"] == pytest.approx(0.05)
        assert len(out["by_day"]) == 3

    def test_excludes_rows_outside_window(self):
        from admin_kpi import get_cost_summary

        _seed_cost(1, "anthropic_blog", 1.00, days_ago=20)  # 14일 밖
        _seed_cost(1, "anthropic_blog", 0.10, days_ago=1)

        out = get_cost_summary(days=14)
        assert round(out["total_usd"], 4) == 0.10


# ── get_module_satisfaction ───────────────────────────────


class TestModuleSatisfaction:
    def test_empty_returns_empty_list(self):
        from admin_kpi import get_module_satisfaction
        assert get_module_satisfaction() == []

    def test_groups_by_module_with_rate(self):
        from admin_kpi import get_module_satisfaction

        # module=1 → 좋아요 2 / 전체 3 → 0.6667
        _seed_like("s1", 0, 1, module=1, liked=1)
        _seed_like("s1", 1, 1, module=1, liked=1)
        _seed_like("s1", 2, 1, module=1, liked=0)  # toggle off, row 보존
        # module=2 → 좋아요 1 / 전체 1 → 1.0
        _seed_like("s2", 0, 1, module=2, liked=1)

        out = get_module_satisfaction()
        m1 = next(r for r in out if r["module"] == 1)
        m2 = next(r for r in out if r["module"] == 2)
        assert m1["total"] == 3
        assert m1["liked"] == 2
        assert m1["rate"] == 0.6667
        assert m2["total"] == 1
        assert m2["liked"] == 1
        assert m2["rate"] == 1.0

    def test_null_module_is_last(self):
        """레거시 row(module=NULL)는 정렬상 마지막."""
        from admin_kpi import get_module_satisfaction

        _seed_like("s1", 0, 1, module=None, liked=1)
        _seed_like("s2", 0, 1, module=3, liked=1)
        _seed_like("s3", 0, 1, module=1, liked=0)

        out = get_module_satisfaction()
        modules = [r["module"] for r in out]
        assert modules == [1, 3, None]


# ── get_chat_turn_distribution ────────────────────────────


class TestChatTurnDistribution:
    def test_empty_returns_zero_with_bins(self):
        from admin_kpi import get_chat_turn_distribution

        out = get_chat_turn_distribution(days=14)
        assert out["total"] == 0
        assert out["completion_rate"] == 0.0
        assert out["avg_turns"] == 0.0
        # 6개 bin 모두 0
        assert all(b["count"] == 0 for b in out["histogram"])
        assert [b["bin"] for b in out["histogram"]] == [
            "0", "1-2", "3-4", "5-7", "8-12", "13+"
        ]

    def test_histogram_buckets_correctly(self):
        from admin_kpi import get_chat_turn_distribution

        # turn_count: 0, 1, 2, 4, 6, 10, 15
        for tc in [0, 1, 2, 4, 6, 10, 15]:
            _seed_chat(1, turn_count=tc, stage="done")

        out = get_chat_turn_distribution(days=14)
        bins = {b["bin"]: b["count"] for b in out["histogram"]}
        assert bins["0"] == 1     # 0
        assert bins["1-2"] == 2   # 1, 2
        assert bins["3-4"] == 1   # 4
        assert bins["5-7"] == 1   # 6
        assert bins["8-12"] == 1  # 10
        assert bins["13+"] == 1   # 15
        assert out["total"] == 7

    def test_completion_rate(self):
        from admin_kpi import get_chat_turn_distribution

        _seed_chat(1, turn_count=5, stage="done")
        _seed_chat(1, turn_count=3, stage="done")
        _seed_chat(1, turn_count=1, stage="topic")    # 미완료
        _seed_chat(1, turn_count=2, stage="generating")  # 미완료

        out = get_chat_turn_distribution(days=14)
        assert out["total"] == 4
        assert out["completion_rate"] == 0.5
        assert out["avg_turns"] == pytest.approx(2.75)

    def test_excludes_old_sessions(self):
        from admin_kpi import get_chat_turn_distribution

        _seed_chat(1, turn_count=5, stage="done", days_ago=20)
        _seed_chat(1, turn_count=3, stage="done", days_ago=2)

        out = get_chat_turn_distribution(days=14)
        assert out["total"] == 1


# ── get_clinic_activity ───────────────────────────────────


class TestClinicActivity:
    def test_empty_returns_zero_rows_per_clinic(self):
        """활동 0 클리닉도 결과에 포함."""
        from admin_kpi import get_clinic_activity

        rows = get_clinic_activity(days=14)
        assert len(rows) == 2
        for r in rows:
            assert r["blog_sessions"] == 0
            assert r["image_sessions"] == 0
            assert r["cost_usd"] == 0.0

    def test_aggregates_per_clinic_sorted_by_cost_desc(self):
        from admin_kpi import get_clinic_activity

        _seed_chat(1, turn_count=3, stage="done")
        _seed_chat(1, turn_count=4, stage="done")
        _seed_image_session(1, "img-c1-a")
        _seed_cost(1, "anthropic_blog", 0.15, days_ago=0)
        _seed_cost(1, "openai_image_init", 0.20, days_ago=0)

        _seed_chat(2, turn_count=2, stage="topic")
        _seed_cost(2, "anthropic_blog", 0.05, days_ago=1)

        rows = get_clinic_activity(days=14)
        # 비용 ↓ 정렬: clinic 1 먼저
        assert rows[0]["clinic_id"] == 1
        assert rows[0]["blog_sessions"] == 2
        assert rows[0]["image_sessions"] == 1
        assert rows[0]["cost_usd"] == pytest.approx(0.35)
        assert rows[1]["clinic_id"] == 2
        assert rows[1]["blog_sessions"] == 1
        assert rows[1]["cost_usd"] == pytest.approx(0.05)

    def test_excludes_admin_image_test_cost(self):
        """openai_image_admin kind 는 KPI 비용 합계에서 제외."""
        from admin_kpi import get_clinic_activity

        _seed_cost(1, "openai_image_admin", 5.00, days_ago=0)  # 제외
        _seed_cost(1, "anthropic_blog", 0.10, days_ago=0)

        rows = get_clinic_activity(days=14)
        c1 = next(r for r in rows if r["clinic_id"] == 1)
        assert c1["cost_usd"] == pytest.approx(0.10)

    def test_excludes_old_data(self):
        from admin_kpi import get_clinic_activity

        _seed_cost(1, "anthropic_blog", 1.00, days_ago=20)
        _seed_cost(1, "anthropic_blog", 0.10, days_ago=1)
        _seed_chat(1, turn_count=3, stage="done", days_ago=20)
        _seed_chat(1, turn_count=4, stage="done", days_ago=1)
        _seed_image_session(1, "img-old", days_ago=20)
        _seed_image_session(1, "img-new", days_ago=1)

        rows = get_clinic_activity(days=14)
        c1 = next(r for r in rows if r["clinic_id"] == 1)
        assert c1["blog_sessions"] == 1
        assert c1["image_sessions"] == 1
        assert c1["cost_usd"] == pytest.approx(0.10)
