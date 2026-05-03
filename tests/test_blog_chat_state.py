"""
blog_chat_state 단위 테스트 (Step 1 Phase 1B, v10 plan E1)

검증:
  - create_session: UUID 발급, TOPIC stage, DB INSERT
  - get_session: cache hit / miss → DB load / 미존재 LookupError / 다른 clinic PermissionError
  - save_session: last_active_at 갱신, state_json round-trip
  - transition: 화이트리스트 전이, 잘못된 전이 ValueError, 동일 stage no-op
  - append_message: messages append, ts/options/meta 정상 직렬화
  - LRU: max 초과 시 evict + DB 백업 → 다시 get 시 DB에서 복귀
  - cleanup_stale_sessions: TTL 초과 행 삭제
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
    """테스트별 임시 SQLite DB + LRU cache 초기화."""
    db_file = tmp_path / "blog_chat_test.db"

    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    # blog_chat_sessions 스키마만 (다른 테이블 의존성 없음)
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE blog_chat_sessions (
            session_id      TEXT PRIMARY KEY,
            clinic_id       INTEGER NOT NULL,
            user_id         INTEGER,
            stage           TEXT NOT NULL DEFAULT 'topic',
            state_json      TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            last_active_at  TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        )
    """)
    conn.commit()
    conn.close()

    # 모듈 LRU 비우기 (다른 테스트 잔여물 차단)
    import blog_chat_state
    blog_chat_state._cache_clear()
    yield
    blog_chat_state._cache_clear()


# ── create_session ────────────────────────────────────────


class TestCreateSession:
    def test_returns_state_with_uuid_and_topic_stage(self):
        from blog_chat_state import Stage, create_session

        s = create_session(clinic_id=1, user_id=10)
        assert isinstance(s.session_id, str)
        assert len(s.session_id) == 36  # UUID4 정형
        assert s.stage == Stage.TOPIC
        assert s.clinic_id == 1
        assert s.user_id == 10
        assert s.messages == []
        assert s.created_at and s.last_active_at

    def test_persists_to_db(self):
        from blog_chat_state import _load_from_db, create_session

        s = create_session(clinic_id=1, user_id=10)
        loaded = _load_from_db(s.session_id)
        assert loaded is not None
        assert loaded.session_id == s.session_id
        assert loaded.clinic_id == 1


# ── get_session ───────────────────────────────────────────


class TestGetSession:
    def test_cache_hit_returns_same_object(self):
        from blog_chat_state import create_session, get_session

        s = create_session(clinic_id=1, user_id=10)
        got = get_session(s.session_id, clinic_id=1)
        assert got is s  # cache hit → 동일 객체

    def test_cache_miss_loads_from_db(self):
        import blog_chat_state
        from blog_chat_state import create_session, get_session

        s = create_session(clinic_id=1, user_id=10)
        sid = s.session_id
        # cache 강제 비움 → DB에서만 복구
        blog_chat_state._cache_clear()
        got = get_session(sid, clinic_id=1)
        assert got.session_id == sid
        assert got.clinic_id == 1

    def test_missing_session_raises_lookup_error(self):
        from blog_chat_state import get_session

        with pytest.raises(LookupError):
            get_session("00000000-0000-0000-0000-000000000000", clinic_id=1)

    def test_other_clinic_raises_permission_error(self):
        from blog_chat_state import create_session, get_session

        s = create_session(clinic_id=1, user_id=10)
        with pytest.raises(PermissionError):
            get_session(s.session_id, clinic_id=999)


# ── save_session / round-trip ─────────────────────────────


class TestSaveSession:
    def test_updates_last_active_and_state_json(self):
        import blog_chat_state
        from blog_chat_state import (
            Stage,
            append_message,
            create_session,
            save_session,
            transition,
        )

        s = create_session(clinic_id=1, user_id=10)
        original_last = s.last_active_at

        s.topic = "허리디스크"
        transition(s, Stage.SEO)
        append_message(s, "user", "허리디스크")
        save_session(s)

        # cache 비우고 DB에서 읽어 round-trip
        blog_chat_state._cache_clear()
        loaded = blog_chat_state._load_from_db(s.session_id)
        assert loaded.topic == "허리디스크"
        assert loaded.stage == Stage.SEO
        assert len(loaded.messages) == 1
        assert loaded.messages[0].text == "허리디스크"
        assert loaded.last_active_at >= original_last


# ── transition ────────────────────────────────────────────


class TestTransition:
    def test_valid_transition(self):
        from blog_chat_state import Stage, create_session, transition

        s = create_session(clinic_id=1, user_id=10)
        transition(s, Stage.SEO)
        assert s.stage == Stage.SEO

    def test_same_stage_is_noop(self):
        from blog_chat_state import Stage, create_session, transition

        s = create_session(clinic_id=1, user_id=10)
        transition(s, Stage.TOPIC)
        assert s.stage == Stage.TOPIC

    def test_invalid_transition_raises(self):
        from blog_chat_state import Stage, create_session, transition

        s = create_session(clinic_id=1, user_id=10)
        # TOPIC → DONE 직행 금지
        with pytest.raises(ValueError):
            transition(s, Stage.DONE)

    def test_questions_can_repeat(self):
        from blog_chat_state import Stage, create_session, transition

        s = create_session(clinic_id=1, user_id=10)
        # 신 흐름 (2026-05-03): TOPIC → SEO → EMPHASIS → LENGTH → QUESTIONS
        transition(s, Stage.SEO)
        transition(s, Stage.EMPHASIS)
        transition(s, Stage.LENGTH)
        transition(s, Stage.QUESTIONS)
        # questions → questions 반복 OK (n번 질문)
        transition(s, Stage.QUESTIONS)
        assert s.stage == Stage.QUESTIONS


# ── append_message ────────────────────────────────────────


class TestAppendMessage:
    def test_appends_with_options_and_meta(self):
        from blog_chat_state import append_message, create_session

        s = create_session(clinic_id=1, user_id=10)
        msg = append_message(
            s, "assistant", "글자 수 골라주세요",
            options=[{"id": "1", "label": "1,500자"}, {"id": "2", "label": "2,000자"}],
            meta={"stage_text": "글자 수 정하는 중"},
        )
        assert len(s.messages) == 1
        assert msg.role == "assistant"
        assert len(msg.options) == 2
        assert msg.meta["stage_text"] == "글자 수 정하는 중"
        assert msg.ts  # ISO8601 채워짐


# ── state_json 직렬화 ─────────────────────────────────────────


class TestToStateJson:
    def test_image_gallery_b64_stripped_for_storage(self):
        """image_gallery 메시지의 b64 이미지는 DB JSON에 저장하지 않음 (DB 비대 방지)."""
        import json as _json
        from blog_chat_state import append_message, create_session

        s = create_session(clinic_id=1, user_id=10)
        big_b64 = ["x" * 1024 for _ in range(5)]  # 5장 더미
        append_message(
            s, "assistant", "5장 준비됐어요.",
            options=[],
            meta={"kind": "image_gallery", "images": big_b64,
                  "image_session_id": "sid", "filename_base": "허리디스크"},
        )

        serialized = _json.loads(s.to_state_json())
        gallery_msgs = [m for m in serialized["messages"]
                        if (m.get("meta") or {}).get("kind") == "image_gallery"]
        assert len(gallery_msgs) == 1
        meta = gallery_msgs[0]["meta"]
        # b64 비어 있어야 함 + count는 보존
        assert meta["images"] == []
        assert meta["images_count"] == 5
        assert meta["images_redacted_for_storage"] is True
        # 다른 메타는 그대로
        assert meta["image_session_id"] == "sid"
        assert meta["filename_base"] == "허리디스크"

        # in-memory state는 그대로 — 같은 conversation 안에서 갤러리 표시 유지
        assert len(s.messages[0].meta["images"]) == 5
        assert s.messages[0].meta["images"][0] == big_b64[0]


# ── LRU ───────────────────────────────────────────────────


class TestLRU:
    def test_evicted_session_recovered_from_db(self, monkeypatch):
        """LRU 한도 초과 시 가장 오래된 항목이 evict되어도 DB에서 다시 복구."""
        import blog_chat_state
        from blog_chat_state import create_session, get_session

        # LRU 크기를 2로 줄여 빠른 검증
        monkeypatch.setattr(blog_chat_state, "LRU_MAX_SIZE", 2)

        s1 = create_session(clinic_id=1, user_id=10)
        s2 = create_session(clinic_id=1, user_id=10)
        s3 = create_session(clinic_id=1, user_id=10)
        # s1이 evict 되었어야 함 (가장 오래됨)
        assert s1.session_id not in blog_chat_state._cache
        assert s2.session_id in blog_chat_state._cache
        assert s3.session_id in blog_chat_state._cache

        # 다시 get → DB에서 복구
        recovered = get_session(s1.session_id, clinic_id=1)
        assert recovered.session_id == s1.session_id


# ── cleanup_stale_sessions ────────────────────────────────


class TestCleanupStale:
    def test_deletes_old_sessions(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        import blog_chat_state
        from blog_chat_state import cleanup_stale_sessions, create_session

        s_old = create_session(clinic_id=1, user_id=10)
        s_new = create_session(clinic_id=1, user_id=10)

        # s_old의 last_active_at을 25시간 전으로 강제 (TTL 24h 초과)
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        from db_manager import get_db
        with get_db() as conn:
            conn.execute(
                "UPDATE blog_chat_sessions SET last_active_at = ? WHERE session_id = ?",
                (old_iso, s_old.session_id),
            )
        # in-memory 객체에도 반영 (cache 측 정리 검증용)
        s_old.last_active_at = old_iso

        deleted = cleanup_stale_sessions(ttl_hours=24)
        assert deleted == 1

        # DB에서 s_old만 사라짐
        assert blog_chat_state._load_from_db(s_old.session_id) is None
        assert blog_chat_state._load_from_db(s_new.session_id) is not None
