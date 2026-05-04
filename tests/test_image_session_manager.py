"""
image_session_manager 단위 테스트 (Phase 4, 2026-04-30)

검증:
  - create_session: UUID 생성, DB INSERT, 초기 0/0 카운트
  - get_session: 존재/부재 분기
  - increment_regen / increment_edit: 카운터 증가, last_active_at 갱신
  - 다른 clinic_id 접근 시 PermissionError
  - get_clinic_image_stats: 합계·평균 계산
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """테스트별 임시 SQLite DB."""
    db_file = tmp_path / "image_sess_test.db"

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    # image_sessions 스키마만 생성 (다른 테이블 의존성 없음)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE image_sessions (
            session_id        TEXT PRIMARY KEY,
            clinic_id         INTEGER NOT NULL,
            user_id           INTEGER,
            blog_keyword      TEXT,
            plan_id_at_start  TEXT,
            regen_count       INTEGER NOT NULL DEFAULT 0,
            edit_count        INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            last_active_at    TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            modules_json      TEXT
        )
    """)
    conn.commit()
    conn.close()
    yield


# ── 테스트 ──────────────────────────────────────────────


class TestCreateSession:
    def test_returns_uuid(self):
        from image_session_manager import create_session

        sid = create_session(
            clinic_id=1, user_id=10, blog_keyword="허리디스크", plan_id_at_start="standard"
        )
        assert len(sid) == 36  # UUID4 형식
        assert sid.count("-") == 4

    def test_initial_counts_zero(self):
        from image_session_manager import create_session, get_session

        sid = create_session(
            clinic_id=1, user_id=10, blog_keyword="x", plan_id_at_start="standard"
        )
        sess = get_session(sid)
        assert sess["regen_count"] == 0
        assert sess["edit_count"] == 0
        assert sess["clinic_id"] == 1
        assert sess["plan_id_at_start"] == "standard"
        assert sess["blog_keyword"] == "x"

    def test_get_unknown_returns_none(self):
        from image_session_manager import get_session

        assert get_session("nonexistent-uuid") is None


class TestCreateSessionModulesJson:
    """Commit 6b — create_session(modules_json=...) 인자 + DB 저장 검증"""

    def test_modules_json_stored(self):
        from image_session_manager import create_session, get_session

        sid = create_session(
            clinic_id=1, user_id=10,
            blog_keyword="허리디스크",
            plan_id_at_start="standard",
            modules_json='[1,4,8,2,11]',
        )
        sess = get_session(sid)
        assert sess["modules_json"] == '[1,4,8,2,11]'

    def test_modules_json_default_none(self):
        """modules_json 미지정 시 NULL (레거시 호환)"""
        from image_session_manager import create_session, get_session

        sid = create_session(
            clinic_id=1, user_id=10, plan_id_at_start="standard"
        )
        sess = get_session(sid)
        assert sess["modules_json"] is None


class TestIncrementRegen:
    def test_increments_count(self):
        from image_session_manager import create_session, increment_regen, get_session

        sid = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        assert increment_regen(sid, clinic_id=1) == 1
        assert increment_regen(sid, clinic_id=1) == 2
        assert get_session(sid)["regen_count"] == 2

    def test_other_clinic_blocked(self):
        from image_session_manager import create_session, increment_regen

        sid = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        with pytest.raises(PermissionError):
            increment_regen(sid, clinic_id=99)

    def test_unknown_session_raises(self):
        from image_session_manager import increment_regen

        with pytest.raises(LookupError):
            increment_regen("missing-sid", clinic_id=1)

    def test_independent_from_edit(self):
        from image_session_manager import create_session, increment_regen, get_session

        sid = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        increment_regen(sid, clinic_id=1)
        sess = get_session(sid)
        assert sess["regen_count"] == 1
        assert sess["edit_count"] == 0


class TestIncrementEdit:
    def test_increments_count(self):
        from image_session_manager import create_session, increment_edit

        sid = create_session(clinic_id=1, user_id=1, plan_id_at_start="pro")
        for expected in (1, 2, 3, 4):
            assert increment_edit(sid, clinic_id=1) == expected

    def test_other_clinic_blocked(self):
        from image_session_manager import create_session, increment_edit

        sid = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        with pytest.raises(PermissionError):
            increment_edit(sid, clinic_id=99)


class TestListAndStats:
    def test_list_recent_sorted(self):
        import time

        from image_session_manager import create_session, list_recent_sessions

        sid1 = create_session(
            clinic_id=1, user_id=1, blog_keyword="첫번째", plan_id_at_start="standard"
        )
        time.sleep(0.01)
        sid2 = create_session(
            clinic_id=1, user_id=1, blog_keyword="두번째", plan_id_at_start="standard"
        )
        # 다른 클리닉 세션은 제외돼야 함
        create_session(clinic_id=2, user_id=2, plan_id_at_start="standard")

        results = list_recent_sessions(clinic_id=1, limit=10)
        assert len(results) == 2
        # 최신순 (sid2 먼저)
        assert results[0]["session_id"] == sid2
        assert results[1]["session_id"] == sid1

    def test_clinic_stats_empty(self):
        from image_session_manager import get_clinic_image_stats

        stats = get_clinic_image_stats(clinic_id=999)
        assert stats["total_sessions"] == 0
        assert stats["avg_regen_per_session"] == 0.0

    def test_clinic_stats_with_activity(self):
        from image_session_manager import (
            create_session,
            get_clinic_image_stats,
            increment_edit,
            increment_regen,
        )

        s1 = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        s2 = create_session(clinic_id=1, user_id=1, plan_id_at_start="standard")
        increment_regen(s1, clinic_id=1)
        increment_edit(s1, clinic_id=1)
        increment_edit(s1, clinic_id=1)
        increment_regen(s2, clinic_id=1)

        stats = get_clinic_image_stats(clinic_id=1)
        assert stats["total_sessions"] == 2
        assert stats["total_regens"] == 2
        assert stats["total_edits"] == 2
        assert stats["avg_regen_per_session"] == 1.0
        assert stats["avg_edit_per_session"] == 1.0
