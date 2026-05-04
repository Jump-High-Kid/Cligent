"""
test_db_gallery_likes.py — Commit 6a 마이그레이션 회귀

배경 (2026-05-04):
  베타 KPI Commit 6 — 갤러리 좋아요(D1) + 모듈별 만족도 인프라.
  image_sessions 에 modules_json TEXT 컬럼 추가 (5장 모듈 list).
  gallery_likes 신규 테이블: like INSERT 시 modules_json[index] 읽어서
  module 컬럼 denormalize → KPI GROUP BY module 깔끔.

검증 항목:
  1. image_sessions.modules_json 컬럼 존재 (ALTER 마이그레이션)
  2. gallery_likes 테이블 + 인덱스 2종 생성
  3. 필수 컬럼 — id / session_id / image_index / clinic_id / user_id /
     module / liked / liked_at / updated_at
  4. UNIQUE(session_id, image_index) 제약
  5. INSERT/SELECT round-trip
  6. UPSERT 동작 (ON CONFLICT(session_id, image_index) DO UPDATE)
  7. init_db() 멱등 (두 번 호출 안전)
  8. 기존 image_sessions row 의 modules_json NULL 허용 (레거시 호환)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def fresh_db(tmp_path):
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "gallery_likes_test.db"
    db_manager.init_db()

    # 시드 클리닉 1개 + 시드 image_session 1개 (FK·매핑 검증용)
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots) VALUES (1, '테스트 한의원', 5)"
        )
        conn.execute(
            "INSERT INTO image_sessions "
            "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start) "
            "VALUES (?, ?, ?, ?, ?)",
            ("img-session-uuid-1", 1, 100, "불면증", "standard"),
        )

    yield db_manager
    db_manager.DB_PATH = orig


# ── image_sessions.modules_json 마이그레이션 ──────────────


def test_image_sessions_has_modules_json_column(fresh_db):
    """image_sessions 테이블에 modules_json TEXT 컬럼 추가됨"""
    with fresh_db.get_db() as conn:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(image_sessions)")}
    assert "modules_json" in cols, f"modules_json 컬럼 누락 (현재: {set(cols)})"
    assert cols["modules_json"] == "TEXT", f"modules_json 타입 불일치: {cols['modules_json']}"


def test_image_sessions_modules_json_nullable(fresh_db):
    """기존 image_sessions row 는 modules_json NULL 허용 (레거시 호환)"""
    with fresh_db.get_db() as conn:
        # 시드 row 는 modules_json 없이 INSERT 됨 → NULL 이어야 함
        row = conn.execute(
            "SELECT modules_json FROM image_sessions WHERE session_id = ?",
            ("img-session-uuid-1",),
        ).fetchone()
    assert row is not None
    assert row["modules_json"] is None, "기존 row 의 modules_json 은 NULL 이어야 함"


def test_image_sessions_modules_json_roundtrip(fresh_db):
    """modules_json INSERT/SELECT 라운드트립"""
    payload = '["anatomy","acupuncture","herbal","clinic","lifestyle"]'
    with fresh_db.get_db() as conn:
        conn.execute(
            "INSERT INTO image_sessions "
            "(session_id, clinic_id, user_id, blog_keyword, plan_id_at_start, modules_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("img-session-uuid-2", 1, 100, "두통", "pro", payload),
        )
        row = conn.execute(
            "SELECT modules_json FROM image_sessions WHERE session_id = ?",
            ("img-session-uuid-2",),
        ).fetchone()
    assert row["modules_json"] == payload


# ── gallery_likes 테이블 ───────────────────────────────


def test_gallery_likes_table_exists(fresh_db):
    """gallery_likes 테이블 생성됨"""
    with fresh_db.get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gallery_likes'"
        ).fetchone()
    assert row is not None, "gallery_likes 테이블이 생성되지 않음"


def test_gallery_likes_columns(fresh_db):
    """필수 컬럼 모두 존재해야 함"""
    expected = {
        "id", "session_id", "image_index", "clinic_id", "user_id",
        "module", "liked", "liked_at", "updated_at",
    }
    with fresh_db.get_db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(gallery_likes)")}
    missing = expected - cols
    assert not missing, f"누락된 컬럼: {missing} (현재: {cols})"


def test_gallery_likes_indexes(fresh_db):
    """인덱스 2종 — clinic / module"""
    with fresh_db.get_db() as conn:
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='gallery_likes'"
        ).fetchall()
    names = {r[0] for r in idx_rows}
    for required in [
        "idx_gallery_likes_clinic",
        "idx_gallery_likes_module",
    ]:
        assert required in names, f"누락된 인덱스: {required} (현재: {names})"


def test_gallery_likes_insert_roundtrip(fresh_db):
    """기본 INSERT + SELECT 동작"""
    with fresh_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO gallery_likes
                (session_id, image_index, clinic_id, user_id, module, liked,
                 liked_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("img-session-uuid-1", 0, 1, 100, 1, 1,
             "2026-05-04T12:00:00+00:00", "2026-05-04T12:00:00+00:00"),
        )
        row = conn.execute(
            "SELECT session_id, image_index, clinic_id, user_id, module, liked "
            "FROM gallery_likes WHERE session_id = ? AND image_index = ?",
            ("img-session-uuid-1", 0),
        ).fetchone()
    assert row is not None
    assert row["session_id"] == "img-session-uuid-1"
    assert row["image_index"] == 0
    assert row["clinic_id"] == 1
    assert row["user_id"] == 100
    assert row["module"] == 1
    assert row["liked"] == 1


def test_gallery_likes_unique_constraint(fresh_db):
    """UNIQUE(session_id, image_index) — 같은 (session, index) 중복 INSERT 차단"""
    import sqlite3
    with fresh_db.get_db() as conn:
        conn.execute(
            "INSERT INTO gallery_likes "
            "(session_id, image_index, clinic_id, user_id, module, liked, "
            " liked_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("img-session-uuid-1", 0, 1, 100, 1, 1,
             "2026-05-04T12:00:00+00:00", "2026-05-04T12:00:00+00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO gallery_likes "
                "(session_id, image_index, clinic_id, user_id, module, liked, "
                " liked_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("img-session-uuid-1", 0, 1, 100, 1, 0,
                 "2026-05-04T12:01:00+00:00", "2026-05-04T12:01:00+00:00"),
            )


def test_gallery_likes_upsert_toggle_off(fresh_db):
    """ON CONFLICT(session_id, image_index) DO UPDATE — toggle 시 row 1개 유지, liked=0 으로 갱신"""
    with fresh_db.get_db() as conn:
        # 1차: liked=1
        conn.execute(
            """
            INSERT INTO gallery_likes
                (session_id, image_index, clinic_id, user_id, module, liked,
                 liked_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, image_index) DO UPDATE SET
                liked = excluded.liked,
                updated_at = excluded.updated_at
            """,
            ("img-session-uuid-1", 0, 1, 100, 1, 1,
             "2026-05-04T12:00:00+00:00", "2026-05-04T12:00:00+00:00"),
        )
        # 2차: 같은 (session, index) toggle off → liked=0
        conn.execute(
            """
            INSERT INTO gallery_likes
                (session_id, image_index, clinic_id, user_id, module, liked,
                 liked_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, image_index) DO UPDATE SET
                liked = excluded.liked,
                updated_at = excluded.updated_at
            """,
            ("img-session-uuid-1", 0, 1, 100, 1, 0,
             "2026-05-04T12:00:00+00:00", "2026-05-04T12:01:00+00:00"),
        )
        # row 1개만 존재
        rows = conn.execute(
            "SELECT liked, liked_at, updated_at FROM gallery_likes "
            "WHERE session_id = ? AND image_index = ?",
            ("img-session-uuid-1", 0),
        ).fetchall()
    assert len(rows) == 1, f"UPSERT 실패 — row 수 {len(rows)} (1 이어야 함)"
    assert rows[0]["liked"] == 0, "toggle off 후 liked=0 이어야 함"
    # liked_at 은 최초 좋아요 시각 보존 / updated_at 은 갱신
    assert rows[0]["liked_at"] == "2026-05-04T12:00:00+00:00"
    assert rows[0]["updated_at"] == "2026-05-04T12:01:00+00:00"


def test_gallery_likes_idempotent_migration(fresh_db):
    """init_db() 두 번 호출해도 안전 (idempotent)"""
    fresh_db.init_db()  # 다시 호출
    with fresh_db.get_db() as conn:
        # gallery_likes 테이블 여전히 존재
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gallery_likes'"
        ).fetchone()
        assert row is not None
        # image_sessions.modules_json 컬럼 여전히 존재 (중복 ALTER 없음)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(image_sessions)")}
        assert "modules_json" in cols
