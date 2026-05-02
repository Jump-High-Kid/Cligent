"""
blog_chat_state.py — 블로그 챗 세션 state (서버 보유, v10 plan E1)

세션 단위:
  1편 블로그당 1세션. UUID4 발급, 클라는 session_id만 보유, 서버가 state 100% 보유.
  in-memory LRU(1,000) + DB 백업(blog_chat_sessions). TTL 24h 미활성 세션 정리.

Stage 전이:
  topic → length → questions(반복) → seo → generating → image → feedback → done
  feedback는 옵션 (스킵 가능). image도 스킵 가능 (본문만 받고 종료).

장애 정책:
  - DB 에러는 RuntimeError (호출자가 503 변환)
  - clinic_id 불일치는 PermissionError (다른 한의원 차단)
  - LRU eviction 시 DB 자동 백업, 다시 접근 시 DB에서 자동 복구
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any, Optional

logger = logging.getLogger(__name__)

LRU_MAX_SIZE = 1000
SESSION_TTL_HOURS = 24


# ── Stage ──────────────────────────────────────────────────────────


class Stage(str, Enum):
    """블로그 챗 진행 단계. str enum이라 SQLite 문자열 그대로 저장."""

    TOPIC = "topic"
    LENGTH = "length"
    QUESTIONS = "questions"
    SEO = "seo"
    EMPHASIS = "emphasis"  # 2026-05-02: 강조 사항 입력 (SEO 다음, CONFIRM_IMAGE 전)
    CONFIRM_IMAGE = "confirm_image"
    GENERATING = "generating"
    IMAGE = "image"
    FEEDBACK = "feedback"
    DONE = "done"


# stage 전이 화이트리스트 — 잘못된 전이는 ValueError
# 2026-05-02 (현재 흐름): TOPIC → LENGTH → QUESTIONS → SEO → EMPHASIS → CONFIRM_IMAGE
# TODO(다음 세션): "타이핑 먼저 → 번호 나중" 흐름으로 재배치 + 테스트 31개 일괄 갱신
VALID_TRANSITIONS: dict[Stage, set[Stage]] = {
    Stage.TOPIC: {Stage.LENGTH},
    # 질문 비활성 시 length → seo 직진
    Stage.LENGTH: {Stage.QUESTIONS, Stage.SEO},
    # 질문 N회 반복 → 끝나면 seo
    Stage.QUESTIONS: {Stage.QUESTIONS, Stage.SEO},
    # SEO → EMPHASIS → CONFIRM_IMAGE
    Stage.SEO: {Stage.EMPHASIS, Stage.CONFIRM_IMAGE, Stage.GENERATING, Stage.DONE},
    Stage.EMPHASIS: {Stage.CONFIRM_IMAGE, Stage.GENERATING, Stage.DONE},
    # 이미지 자동 생성 여부 확인 → 본문 생성 (DONE은 fallback)
    Stage.CONFIRM_IMAGE: {Stage.GENERATING, Stage.CONFIRM_IMAGE, Stage.DONE},
    # 본문 완료 후 이미지 / 피드백 / 종료
    Stage.GENERATING: {Stage.IMAGE, Stage.FEEDBACK, Stage.DONE},
    Stage.IMAGE: {Stage.IMAGE, Stage.FEEDBACK, Stage.DONE},
    Stage.FEEDBACK: {Stage.DONE},
    Stage.DONE: set(),
}


_STAGE_TEXTS = {
    Stage.TOPIC: "주제 입력 중",
    Stage.LENGTH: "글자 수 정하는 중",
    Stage.QUESTIONS: "질문 답변 중",
    Stage.SEO: "주요 키워드 입력 중",
    Stage.EMPHASIS: "강조 사항 입력 중",
    Stage.CONFIRM_IMAGE: "이미지 자동 생성 여부 확인 중",
    Stage.GENERATING: "본문 작성 중",
    Stage.IMAGE: "이미지 작업 중 (평균 6분 소요)",
    Stage.FEEDBACK: "피드백 입력 중",
    Stage.DONE: "완성",
}


def stage_text(stage: Stage) -> str:
    """헤더 중앙에 표시되는 단계 한국어 라벨."""
    return _STAGE_TEXTS.get(stage, "")


# ── Data classes ───────────────────────────────────────────────────


@dataclass
class ChatMessage:
    """채팅 메시지 1건. (role, text, ts) + 옵션 칩."""

    role: str  # 'assistant' / 'user' / 'system'
    text: str
    ts: str  # ISO8601 UTC
    options: list[dict] = field(default_factory=list)  # [{id, label, hint?}]
    meta: dict = field(default_factory=dict)  # stage_text, error 등


@dataclass
class BlogChatState:
    """세션 1건 = 블로그 1편 작성 흐름의 모든 상태.

    state_json으로 직렬화하여 blog_chat_sessions.state_json에 저장.
    환자 식별 정보는 저장하지 않음 (도메인 규칙).
    """

    session_id: str
    clinic_id: int
    user_id: Optional[int]
    stage: Stage
    messages: list[ChatMessage] = field(default_factory=list)

    # 단계별 누적 입력
    topic: str = ""
    length_chars: Optional[int] = None
    questions_answered: list[dict] = field(default_factory=list)  # [{q, a}]
    seo_keywords: list[str] = field(default_factory=list)
    emphasis: str = ""  # 2026-05-02: 원장 강조 사항 (치료법·사례·증상). 본문 생성 시 강하게 인젝션.

    # 결과물 메타
    blog_text: str = ""
    blog_history_id: Optional[int] = None
    image_session_id: Optional[str] = None  # image_sessions.session_id

    # CONFIRM_IMAGE 단계 결과 — True면 본문 완료 후 IMAGE 자동 트리거
    auto_image: bool = False

    # 헤더 우측 한도 표시
    quota: dict = field(default_factory=dict)
    # {regen_used, regen_limit, edit_used, edit_limit}

    created_at: str = ""
    last_active_at: str = ""

    def to_state_json(self) -> str:
        """DB state_json 컬럼용 JSON. session_id/clinic_id/stage 등 별도 컬럼은 제외.

        image_gallery 메시지의 b64 이미지는 DB에 저장하지 않음 (크기·DB 비대 방지).
        대신 images_count 메타만 남김. 새로고침 후 복원 시 갤러리 자체는 사라지지만
        in-memory state를 유지하는 동일 세션 안에서는 갤러리가 그대로 보임.
        """
        d: dict[str, Any] = asdict(self)
        for k in ("session_id", "clinic_id", "user_id", "stage", "created_at", "last_active_at"):
            d.pop(k, None)
        for m in d.get("messages", []) or []:
            meta = m.get("meta") or {}
            if meta.get("kind") == "image_gallery" and meta.get("images"):
                meta["images_count"] = len(meta["images"])
                meta["images"] = []
                meta["images_redacted_for_storage"] = True
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_db_row(cls, row: dict) -> "BlogChatState":
        """DB row → BlogChatState 복원."""
        data = json.loads(row["state_json"])
        msgs = [ChatMessage(**m) for m in data.get("messages", [])]
        return cls(
            session_id=row["session_id"],
            clinic_id=row["clinic_id"],
            user_id=row["user_id"],
            stage=Stage(row["stage"]),
            messages=msgs,
            topic=data.get("topic", ""),
            length_chars=data.get("length_chars"),
            questions_answered=data.get("questions_answered", []),
            seo_keywords=data.get("seo_keywords", []),
            blog_text=data.get("blog_text", ""),
            blog_history_id=data.get("blog_history_id"),
            image_session_id=data.get("image_session_id"),
            auto_image=bool(data.get("auto_image", False)),
            quota=data.get("quota", {}),
            created_at=row["created_at"],
            last_active_at=row["last_active_at"],
        )


# ── Time helpers ───────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── LRU cache (in-memory) ──────────────────────────────────────────

_cache: "OrderedDict[str, BlogChatState]" = OrderedDict()
_cache_lock = RLock()


def _cache_get(session_id: str) -> Optional[BlogChatState]:
    with _cache_lock:
        if session_id not in _cache:
            return None
        _cache.move_to_end(session_id)
        return _cache[session_id]


def _cache_put(state: BlogChatState) -> None:
    with _cache_lock:
        _cache[state.session_id] = state
        _cache.move_to_end(state.session_id)
        # 한도 초과 시 가장 오래된 항목 evict → DB 백업
        while len(_cache) > LRU_MAX_SIZE:
            _, evicted = _cache.popitem(last=False)
            try:
                _save_to_db(evicted)
            except Exception:
                logger.exception("LRU eviction DB save failed: %s", evicted.session_id)


def _cache_clear() -> None:
    """테스트용 — LRU 비우기."""
    with _cache_lock:
        _cache.clear()


# ── DB layer ───────────────────────────────────────────────────────


def _save_to_db(state: BlogChatState) -> None:
    from db_manager import get_db

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO blog_chat_sessions
              (session_id, clinic_id, user_id, stage, state_json, created_at, last_active_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              stage = excluded.stage,
              state_json = excluded.state_json,
              last_active_at = excluded.last_active_at
            """,
            (
                state.session_id,
                state.clinic_id,
                state.user_id,
                state.stage.value,
                state.to_state_json(),
                state.created_at,
                state.last_active_at,
            ),
        )


def _load_from_db(session_id: str) -> Optional[BlogChatState]:
    from db_manager import get_db

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM blog_chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
    if row is None:
        return None
    return BlogChatState.from_db_row(dict(row))


# ── Public API ─────────────────────────────────────────────────────


def create_session(clinic_id: int, user_id: Optional[int]) -> BlogChatState:
    """새 챗 세션. UUID4 발급, TOPIC stage로 시작. LRU + DB 동시 저장."""
    sid = str(uuid.uuid4())
    now = _now_iso()
    state = BlogChatState(
        session_id=sid,
        clinic_id=clinic_id,
        user_id=user_id,
        stage=Stage.TOPIC,
        created_at=now,
        last_active_at=now,
    )
    _save_to_db(state)
    _cache_put(state)
    return state


def get_session(session_id: str, clinic_id: int) -> BlogChatState:
    """세션 조회 + clinic_id 검증.

    LRU 미스 시 DB에서 복구. 없으면 LookupError.
    다른 한의원 세션은 PermissionError.
    """
    state = _cache_get(session_id)
    if state is None:
        state = _load_from_db(session_id)
        if state is None:
            raise LookupError(f"챗 세션을 찾을 수 없습니다: {session_id}")
        _cache_put(state)
    if state.clinic_id != clinic_id:
        raise PermissionError("다른 한의원의 챗 세션은 접근할 수 없습니다.")
    return state


def save_session(state: BlogChatState) -> None:
    """state 변경 후 호출. last_active_at 자동 갱신 + LRU·DB 동기화."""
    state.last_active_at = _now_iso()
    _save_to_db(state)
    _cache_put(state)


def transition(state: BlogChatState, next_stage: Stage) -> None:
    """stage 전이. 동일 stage는 no-op. 화이트리스트 위반은 ValueError. 저장은 호출자."""
    if next_stage == state.stage:
        return
    allowed = VALID_TRANSITIONS.get(state.stage, set())
    if next_stage not in allowed:
        raise ValueError(
            f"잘못된 stage 전이: {state.stage.value} → {next_stage.value}"
        )
    state.stage = next_stage


def append_message(
    state: BlogChatState,
    role: str,
    text: str,
    options: Optional[list[dict]] = None,
    meta: Optional[dict] = None,
) -> ChatMessage:
    """메시지 1건 추가. 저장은 호출자가 save_session()으로 별도."""
    msg = ChatMessage(
        role=role,
        text=text,
        ts=_now_iso(),
        options=options or [],
        meta=meta or {},
    )
    state.messages.append(msg)
    return msg


def cleanup_stale_sessions(ttl_hours: int = SESSION_TTL_HOURS) -> int:
    """TTL 초과 세션 DB 삭제. lifespan 24h 스케줄러용. 반환: 삭제 행 수."""
    from db_manager import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM blog_chat_sessions WHERE last_active_at < ?", (cutoff,)
        )
        deleted = cur.rowcount
    # in-memory도 정리
    with _cache_lock:
        stale = [sid for sid, s in _cache.items() if s.last_active_at < cutoff]
        for sid in stale:
            _cache.pop(sid, None)
    return deleted


def serialize_message(msg: ChatMessage) -> dict:
    """라우트 응답용 — JSON 직렬화 가능 dict."""
    return {
        "role": msg.role,
        "text": msg.text,
        "ts": msg.ts,
        "options": msg.options,
        "meta": msg.meta,
    }
