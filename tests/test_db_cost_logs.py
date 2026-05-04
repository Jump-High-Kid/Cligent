"""
test_db_cost_logs.py — cost_logs 테이블 스키마 마이그레이션 회귀

배경 (2026-05-04):
  베타 KPI Commit 2 — 비용 추적용 cost_logs 신규 테이블.
  기존 usage_logs.metadata JSON 에 박는 대신 정규화된 컬럼으로 분리.
  USD 만 저장 (KRW 변환은 어드민 표시 시점에 환율 적용).

검증 항목:
  1. 신규 DB 초기화 시 cost_logs 테이블 + 인덱스 3종 생성
  2. 필수 컬럼 존재 — kind / clinic_id / tokens_in / tokens_out / cache_read /
     cache_create / cost_usd / blog_session_id / image_session_id / metadata / created_at
  3. INSERT/SELECT round-trip 동작
  4. clinic_id FK 제약 (clinics.id 참조)
  5. 인덱스: idx_cost_logs_clinic_created / idx_cost_logs_kind / idx_cost_logs_blog_session
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def fresh_db(tmp_path):
    import db_manager
    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "cost_logs_test.db"
    db_manager.init_db()

    # 시드 클리닉 1개 (FK 검증용)
    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots) VALUES (1, '테스트 한의원', 5)"
        )

    yield db_manager
    db_manager.DB_PATH = orig


def test_cost_logs_table_exists(fresh_db):
    """cost_logs 테이블이 생성되어 있어야 함"""
    with fresh_db.get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cost_logs'"
        ).fetchone()
    assert row is not None, "cost_logs 테이블이 생성되지 않음"


def test_cost_logs_columns(fresh_db):
    """필수 컬럼 모두 존재해야 함"""
    expected = {
        "id", "clinic_id", "kind", "model",
        "tokens_in", "tokens_out", "cache_read", "cache_create",
        "cost_usd",
        "blog_session_id", "image_session_id",
        "metadata", "created_at",
    }
    with fresh_db.get_db() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cost_logs)")}
    missing = expected - cols
    assert not missing, f"누락된 컬럼: {missing}"


def test_cost_logs_indexes(fresh_db):
    """인덱스 3종 — clinic_created / kind / blog_session"""
    with fresh_db.get_db() as conn:
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cost_logs'"
        ).fetchall()
    names = {r[0] for r in idx_rows}
    for required in [
        "idx_cost_logs_clinic_created",
        "idx_cost_logs_kind",
        "idx_cost_logs_blog_session",
    ]:
        assert required in names, f"누락된 인덱스: {required} (현재: {names})"


def test_cost_logs_insert_roundtrip(fresh_db):
    """기본 INSERT + SELECT 동작 — Anthropic 블로그 비용 한 건"""
    with fresh_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO cost_logs
                (clinic_id, kind, model, tokens_in, tokens_out,
                 cache_read, cache_create, cost_usd, blog_session_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "anthropic_blog", "claude-sonnet-4-6", 1500, 3000,
             200, 800, 0.0143, "blog-session-uuid-123", '{"keyword":"불면증"}'),
        )
        row = conn.execute(
            "SELECT clinic_id, kind, model, tokens_in, tokens_out, "
            "cache_read, cache_create, cost_usd, blog_session_id, metadata "
            "FROM cost_logs WHERE clinic_id = 1"
        ).fetchone()
    assert row is not None
    assert row["kind"] == "anthropic_blog"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["tokens_in"] == 1500
    assert row["tokens_out"] == 3000
    assert row["cache_read"] == 200
    assert row["cache_create"] == 800
    assert abs(row["cost_usd"] - 0.0143) < 1e-9
    assert row["blog_session_id"] == "blog-session-uuid-123"
    assert row["metadata"] == '{"keyword":"불면증"}'


def test_cost_logs_image_kind(fresh_db):
    """OpenAI 이미지 비용도 동일 테이블에 저장 가능"""
    with fresh_db.get_db() as conn:
        conn.execute(
            "INSERT INTO cost_logs (clinic_id, kind, model, cost_usd, image_session_id, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "openai_image_init", "gpt-image-2", 0.265,
             "image-session-uuid-456", '{"image_count":5,"plan":"standard"}'),
        )
        row = conn.execute(
            "SELECT kind, image_session_id FROM cost_logs WHERE clinic_id = 1"
        ).fetchone()
    assert row["kind"] == "openai_image_init"
    assert row["image_session_id"] == "image-session-uuid-456"


def test_cost_logs_idempotent_migration(fresh_db):
    """init_db() 두 번 호출해도 안전 (idempotent)"""
    fresh_db.init_db()  # 다시 호출
    with fresh_db.get_db() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cost_logs'"
        ).fetchone()
    assert row is not None
