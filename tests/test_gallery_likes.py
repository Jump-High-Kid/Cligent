"""
test_gallery_likes.py — Commit 6c gallery_likes 모듈 단위 테스트

API:
  set_like(session_id, image_index, clinic_id, user_id, liked) -> dict
    UPSERT 멱등. liked=False 시 row 유지(liked=0). module 컬럼은
    image_sessions.modules_json[image_index] 에서 denormalize.
  get_session_likes(session_id, clinic_id) -> list[dict]
    image_index 0~4 순서 보장 (없는 index 는 liked=False, module=None).

검증 항목:
  - set_like 새 row 생성 + module denormalize
  - UPSERT 멱등 (같은 인자 두 번 호출)
  - toggle off (liked=False) row 보존, liked=0
  - toggle on after off → liked=1 다시
  - modules_json NULL 인 레거시 세션 → module=None
  - 잘못된 session_id → LookupError
  - 다른 clinic_id → PermissionError
  - image_index 범위 밖(0~4) → ValueError
  - get_session_likes 5개 dict (없는 index 는 unliked default)
  - get_session_likes 다른 clinic → PermissionError
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
    """테스트별 임시 SQLite DB. image_sessions + gallery_likes 스키마만."""
    db_file = tmp_path / "gallery_likes_test.db"

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

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
    conn.execute("""
        CREATE TABLE gallery_likes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL,
            image_index INTEGER NOT NULL,
            clinic_id   INTEGER NOT NULL,
            user_id     INTEGER,
            module      INTEGER,
            liked       INTEGER NOT NULL DEFAULT 1,
            liked_at    TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
            updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
            UNIQUE(session_id, image_index)
        )
    """)
    # 시드 image_session — modules_json = [1, 4, 8, 2, 11]
    conn.execute(
        "INSERT INTO image_sessions "
        "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start, modules_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("img-session-uuid-1", 1, 100, "허리디스크", "standard",
         '[1, 4, 8, 2, 11]'),
    )
    # 시드 image_session 2 — modules_json NULL (레거시)
    conn.execute(
        "INSERT INTO image_sessions "
        "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start) "
        "VALUES (?, ?, ?, ?, ?)",
        ("img-session-legacy", 1, 100, "두통", "standard"),
    )
    # 시드 image_session 3 — 다른 clinic
    conn.execute(
        "INSERT INTO image_sessions "
        "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start, modules_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("img-session-other-clinic", 99, 999, "x", "standard",
         '[1, 4, 8, 2, 11]'),
    )
    conn.commit()
    conn.close()
    yield


# ── set_like ────────────────────────────────────────────────


class TestSetLike:
    def test_creates_new_row_with_module_denormalized(self):
        from gallery_likes import set_like

        result = set_like(
            session_id="img-session-uuid-1",
            image_index=0,
            clinic_id=1,
            user_id=100,
            liked=True,
        )
        assert result["liked"] is True
        assert result["image_index"] == 0
        assert result["module"] == 1  # modules_json[0]

    def test_module_denormalized_from_correct_index(self):
        from gallery_likes import set_like

        # image_index=2 → modules_json[2] = 8
        result = set_like(
            session_id="img-session-uuid-1",
            image_index=2,
            clinic_id=1,
            user_id=100,
            liked=True,
        )
        assert result["module"] == 8

    def test_idempotent_upsert_same_args(self):
        """같은 인자 두 번 호출해도 row 1개"""
        from gallery_likes import set_like
        import db_manager

        for _ in range(2):
            set_like(
                session_id="img-session-uuid-1",
                image_index=0, clinic_id=1, user_id=100, liked=True,
            )
        with db_manager.get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM gallery_likes WHERE session_id = ? AND image_index = ?",
                ("img-session-uuid-1", 0),
            ).fetchall()
        assert len(rows) == 1

    def test_toggle_off_preserves_row(self):
        """liked=False → row 유지, liked=0"""
        from gallery_likes import set_like
        import db_manager

        set_like("img-session-uuid-1", 0, 1, 100, True)
        result = set_like("img-session-uuid-1", 0, 1, 100, False)
        assert result["liked"] is False

        with db_manager.get_db() as conn:
            row = conn.execute(
                "SELECT liked FROM gallery_likes WHERE session_id = ? AND image_index = ?",
                ("img-session-uuid-1", 0),
            ).fetchone()
        assert row is not None
        assert row["liked"] == 0

    def test_toggle_on_after_off(self):
        """false → true 시 다시 liked=1"""
        from gallery_likes import set_like

        set_like("img-session-uuid-1", 0, 1, 100, True)
        set_like("img-session-uuid-1", 0, 1, 100, False)
        result = set_like("img-session-uuid-1", 0, 1, 100, True)
        assert result["liked"] is True

    def test_modules_json_null_falls_back_to_none(self):
        """레거시 세션 (modules_json NULL) → module=None"""
        from gallery_likes import set_like

        result = set_like(
            session_id="img-session-legacy",
            image_index=0,
            clinic_id=1,
            user_id=100,
            liked=True,
        )
        assert result["module"] is None

    def test_unknown_session_raises_lookup_error(self):
        from gallery_likes import set_like

        with pytest.raises(LookupError):
            set_like("nonexistent-session", 0, 1, 100, True)

    def test_other_clinic_blocked(self):
        from gallery_likes import set_like

        with pytest.raises(PermissionError):
            # session 은 clinic=99 소유. clinic=1 이 like 시도
            set_like("img-session-other-clinic", 0, 1, 100, True)

    @pytest.mark.parametrize("bad_index", [-1, 5, 100, 999])
    def test_image_index_out_of_range_raises_value_error(self, bad_index):
        from gallery_likes import set_like

        with pytest.raises(ValueError):
            set_like("img-session-uuid-1", bad_index, 1, 100, True)


# ── get_session_likes ────────────────────────────────────


class TestGetSessionLikes:
    def test_returns_5_default_unliked_when_empty(self):
        """좋아요 row 0건이면 5개 default(liked=False, module=modules_json[i])"""
        from gallery_likes import get_session_likes

        likes = get_session_likes("img-session-uuid-1", clinic_id=1)
        assert len(likes) == 5
        for i, like in enumerate(likes):
            assert like["image_index"] == i
            assert like["liked"] is False
        # module 은 modules_json 에서 매핑
        assert likes[0]["module"] == 1
        assert likes[1]["module"] == 4
        assert likes[2]["module"] == 8
        assert likes[3]["module"] == 2
        assert likes[4]["module"] == 11

    def test_returns_5_legacy_session_module_none(self):
        """modules_json NULL 인 세션 → 5개 모두 module=None"""
        from gallery_likes import get_session_likes

        likes = get_session_likes("img-session-legacy", clinic_id=1)
        assert len(likes) == 5
        for like in likes:
            assert like["module"] is None
            assert like["liked"] is False

    def test_reflects_existing_likes(self):
        from gallery_likes import set_like, get_session_likes

        set_like("img-session-uuid-1", 0, 1, 100, True)
        set_like("img-session-uuid-1", 2, 1, 100, True)
        set_like("img-session-uuid-1", 3, 1, 100, True)
        set_like("img-session-uuid-1", 3, 1, 100, False)  # toggle off

        likes = get_session_likes("img-session-uuid-1", clinic_id=1)
        assert likes[0]["liked"] is True
        assert likes[1]["liked"] is False
        assert likes[2]["liked"] is True
        assert likes[3]["liked"] is False  # toggle off
        assert likes[4]["liked"] is False

    def test_unknown_session_raises_lookup_error(self):
        from gallery_likes import get_session_likes

        with pytest.raises(LookupError):
            get_session_likes("nonexistent-session", clinic_id=1)

    def test_other_clinic_blocked(self):
        from gallery_likes import get_session_likes

        with pytest.raises(PermissionError):
            get_session_likes("img-session-other-clinic", clinic_id=1)
