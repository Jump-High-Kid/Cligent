"""
test_cost_logger.py — record_cost() 헬퍼 회귀 (Commit 5a, 2026-05-04)

검증 항목:
  1. valid kind + 기본 필드 → True 반환 + cost_logs 1행 INSERT
  2. invalid kind → False 반환, INSERT 미발생, raise 없음
  3. metadata dict → JSON 문자열 직렬화 후 저장
  4. blog_session_id / image_session_id 둘 다 nullable, 둘 다 set 가능
  5. fail-soft — DB 미초기화 / FK 위반 / 잘못된 path → False, raise 없음
  6. VALID_COST_KINDS 화이트리스트 (Commit 5에서 합의된 6종)
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def fresh_db(tmp_path):
    import db_manager

    orig = db_manager.DB_PATH
    db_manager.DB_PATH = tmp_path / "cost_logger_test.db"
    db_manager.init_db()

    with db_manager.get_db() as conn:
        conn.execute(
            "INSERT INTO clinics (id, name, max_slots) VALUES (1, '테스트 한의원', 5)"
        )

    yield db_manager
    db_manager.DB_PATH = orig


# ── 화이트리스트 ────────────────────────────────────────────


def test_valid_cost_kinds_set():
    """Commit 5 합의된 6종 — 빠지면 wire-up 실패 위험."""
    from cost_logger import VALID_COST_KINDS

    expected = {
        "anthropic_blog",
        "anthropic_meta",
        "openai_image_init",
        "openai_image_regen",
        "openai_image_edit",
        "openai_image_admin",
    }
    assert set(VALID_COST_KINDS) == expected


# ── 정상 INSERT ────────────────────────────────────────────


def test_record_cost_anthropic_blog(fresh_db):
    from cost_logger import record_cost

    ok = record_cost(
        kind="anthropic_blog",
        clinic_id=1,
        cost_usd=0.0143,
        model="claude-sonnet-4-6",
        tokens_in=1500,
        tokens_out=3000,
        cache_read=200,
        cache_create=800,
        blog_session_id="blog-uuid-abc",
        metadata={"keyword": "불면증"},
    )
    assert ok is True

    with fresh_db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM cost_logs WHERE clinic_id=1"
        ).fetchone()
    assert row is not None
    assert row["kind"] == "anthropic_blog"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["tokens_in"] == 1500
    assert row["tokens_out"] == 3000
    assert row["cache_read"] == 200
    assert row["cache_create"] == 800
    assert abs(row["cost_usd"] - 0.0143) < 1e-9
    assert row["blog_session_id"] == "blog-uuid-abc"
    assert row["image_session_id"] is None
    assert json.loads(row["metadata"]) == {"keyword": "불면증"}


def test_record_cost_image_kind(fresh_db):
    from cost_logger import record_cost

    ok = record_cost(
        kind="openai_image_init",
        clinic_id=1,
        cost_usd=0.265,
        model="gpt-image-2",
        image_session_id="img-uuid-xyz",
        metadata={"size": "1024x1024", "quality": "medium", "count": 5},
    )
    assert ok is True

    with fresh_db.get_db() as conn:
        row = conn.execute(
            "SELECT kind, model, image_session_id, blog_session_id, metadata "
            "FROM cost_logs WHERE clinic_id=1"
        ).fetchone()
    assert row["kind"] == "openai_image_init"
    assert row["image_session_id"] == "img-uuid-xyz"
    assert row["blog_session_id"] is None
    md = json.loads(row["metadata"])
    assert md["count"] == 5


def test_record_cost_minimal(fresh_db):
    """필수 인자만 전달 — 옵션 모두 기본값."""
    from cost_logger import record_cost

    ok = record_cost(kind="anthropic_meta", clinic_id=1, cost_usd=0.0008)
    assert ok is True

    with fresh_db.get_db() as conn:
        row = conn.execute("SELECT * FROM cost_logs WHERE clinic_id=1").fetchone()
    assert row["kind"] == "anthropic_meta"
    assert row["tokens_in"] == 0
    assert row["tokens_out"] == 0
    assert row["model"] is None
    assert row["metadata"] is None


def test_record_cost_metadata_none_omits_field(fresh_db):
    from cost_logger import record_cost

    ok = record_cost(
        kind="openai_image_edit",
        clinic_id=1,
        cost_usd=0.053,
        metadata=None,
    )
    assert ok is True
    with fresh_db.get_db() as conn:
        row = conn.execute("SELECT metadata FROM cost_logs WHERE clinic_id=1").fetchone()
    assert row["metadata"] is None


# ── invalid kind ───────────────────────────────────────────


def test_record_cost_invalid_kind_returns_false(fresh_db):
    from cost_logger import record_cost

    ok = record_cost(kind="bogus_kind", clinic_id=1, cost_usd=1.0)
    assert ok is False

    with fresh_db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM cost_logs").fetchone()[0]
    assert count == 0


# ── fail-soft ──────────────────────────────────────────────


def test_record_cost_fk_violation_fail_soft(fresh_db):
    """존재하지 않는 clinic_id — FK 위반 → False, raise 없음."""
    from cost_logger import record_cost

    ok = record_cost(
        kind="anthropic_blog",
        clinic_id=99999,
        cost_usd=0.001,
    )
    assert ok is False

    with fresh_db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM cost_logs").fetchone()[0]
    assert count == 0


def test_record_cost_db_unavailable_fail_soft(tmp_path, monkeypatch):
    """DB 경로 자체가 망가져도 raise 없음."""
    import db_manager
    from cost_logger import record_cost

    bad = tmp_path / "nonexistent" / "cligent.db"
    monkeypatch.setattr(db_manager, "DB_PATH", bad)

    # 절대 raise 안 해야 함 — 본 흐름 차단 금지 정책
    ok = record_cost(kind="anthropic_blog", clinic_id=1, cost_usd=0.001)
    assert ok is False


def test_record_cost_unserializable_metadata_fail_soft(fresh_db):
    """JSON 직렬화 불가 metadata → False, raise 없음."""
    from cost_logger import record_cost

    class NotSerializable:
        pass

    ok = record_cost(
        kind="anthropic_blog",
        clinic_id=1,
        cost_usd=0.001,
        metadata={"obj": NotSerializable()},
    )
    assert ok is False
