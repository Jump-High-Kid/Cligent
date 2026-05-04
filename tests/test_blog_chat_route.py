"""
test_blog_chat_route.py — Step 1 Phase 1F smoke test

검증 (2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → ...):
  - /api/blog-chat/turn 첫 호출 (신규 세션 + 첫 사용자 입력 → 인사 + SEO)
  - CONFIRM_IMAGE 진입 시 SSE 응답 (Content-Type + 본문 token 프레임)
  - 한도 초과 → 429 + kind=quota_exceeded
  - /blog/chat 베타 게이트 (chat_beta_enabled=0 → /blog 리다이렉트)
  - IMAGE 'all' 옵션 → SSE 5단계 stage_text 발송
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")
# 이미지 단계 텍스트 sleep 차단 (테스트 속도)
os.environ["BLOG_CHAT_IMAGE_DELAYS"] = "0,0,0,0,0"

from main import app  # noqa: E402
from auth_manager import get_current_user  # noqa: E402


FAKE_USER = {
    "id": 1, "clinic_id": 1, "role": "chief_director",
    "email": "test@test.com", "is_active": True,
}


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    """blog_chat_sessions + clinics 최소 스키마 + 시드."""
    db_file = tmp_path / "blog_chat_route.db"
    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            chat_beta_enabled INTEGER DEFAULT 0,
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
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            last_active_at TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            used_at TEXT NOT NULL DEFAULT (datetime('now', 'utc')),
            metadata TEXT
        );
    """)
    # 시드 — clinic_id=1
    conn.execute(
        "INSERT INTO clinics (id, name, chat_beta_enabled) VALUES (1, '테스트한의원', 0)"
    )
    conn.commit()
    conn.close()

    import blog_chat_state
    blog_chat_state._cache_clear()

    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield
    app.dependency_overrides.clear()
    blog_chat_state._cache_clear()


@pytest.fixture(autouse=True)
def _disable_questions_default(monkeypatch):
    """기본은 BLOG_OPTION_STAGES 비활성 — LENGTH → CONFIRM_IMAGE 직진 흐름 (2026-05-03 신 흐름).

    QUESTIONS 흐름 검증은 test_blog_chat_flow.py::TestProcessTurnQuestions 참조.
    """
    import blog_chat_options
    monkeypatch.setattr(blog_chat_options, "BLOG_OPTION_STAGES", [])


client = TestClient(app)


# ── 1) 첫 turn — 신규 세션 + SEO 진입 (2026-05-03 신 흐름) ─────────────────────


def test_first_turn_creates_session_and_advances_to_length():
    """session_id=null + user_input='허리디스크' → 인사 + 사용자 + SEO 옵션 (신 흐름)."""
    res = client.post("/api/blog-chat/turn",
                      json={"user_input": "허리디스크"})
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"]  # UUID 발급
    assert data["stage"] == "seo"
    assert data["stage_text"] == "주요 키워드 입력 중"
    msgs = data["messages"]
    # 인사 + 사용자 + SEO 옵션 메시지 (3건)
    assert len(msgs) == 3
    assert msgs[0]["role"] == "assistant"
    assert "원장님" in msgs[0]["text"]
    assert msgs[1]["role"] == "user"
    assert msgs[1]["text"] == "허리디스크"
    assert msgs[2]["role"] == "assistant"
    # SEO_OPTIONS는 [자동 생성] 1개
    assert len(msgs[2]["options"]) == 1
    assert msgs[2]["options"][0]["id"] == "skip"


# ── 2) SEO 진입 → SSE streaming ───────────────────────────────


def test_seo_input_returns_sse_with_token_frames():
    """TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → 본문 SSE (2026-05-03 신 흐름)."""
    # 1) topic 입력 → SEO
    r1 = client.post("/api/blog-chat/turn", json={"user_input": "허리디스크"})
    sid = r1.json()["session_id"]
    # 2) SEO 키워드 입력 → EMPHASIS
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "추나, 디스크"})
    # 3) EMPHASIS 건너뛰기 → LENGTH
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "건너뛰기"})
    # 4) LENGTH 선택 → CONFIRM_IMAGE
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "2"})
    # 5) CONFIRM_IMAGE 응답 → 본문 SSE
    def fake_gen(*args, **kwargs):
        yield 'data: {"text": "본문 시작"}\n\n'
        yield 'data: {"done": true, "usage": {"cost_krw": 5}}\n\n'

    with patch("blog_generator.generate_blog_stream", fake_gen), \
         patch("blog_history.save_blog_entry", lambda *a, **k: 999), \
         patch("routers.blog.check_blog_limit"), \
         patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
        res = client.post("/api/blog-chat/turn",
                          json={"session_id": sid, "user_input": "아니오"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    body = res.content.decode("utf-8")
    types = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                types.append(json.loads(line[6:]).get("type"))
            except Exception:
                pass
    assert "user_message" in types
    assert "token" in types
    assert "message_done" in types
    assert types[-1] == "done"


def test_confirm_image_no_skips_image_stage(monkeypatch):
    """CONFIRM_IMAGE 부정 응답 → 본문 SSE 정상 + IMAGE 단계 스킵 → FEEDBACK 직접 전이.

    버그 #23 회귀 방지. "아니오" 응답 시 OpenAI 이미지 호출이 절대 발생하지 않아야 한다.
    SSE 종료 후 state.stage == FEEDBACK + IMAGE 옵션 메시지 미발송 + auto_image=False 검증.
    """
    r1 = client.post("/api/blog-chat/turn", json={"user_input": "허리디스크"})
    sid = r1.json()["session_id"]
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "추나"})
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "건너뛰기"})
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "2"})

    def fake_gen(*args, **kwargs):
        yield 'data: {"text": "본문 시작"}\n\n'
        yield 'data: {"done": true, "usage": {"cost_krw": 5}}\n\n'

    with patch("blog_generator.generate_blog_stream", fake_gen), \
         patch("blog_history.save_blog_entry", lambda *a, **k: 999), \
         patch("routers.blog.check_blog_limit"), \
         patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}):
        res = client.post("/api/blog-chat/turn",
                          json={"session_id": sid, "user_input": "아니오"})
    assert res.status_code == 200
    body = res.content.decode("utf-8")

    # 1) IMAGE stage_change 프레임이 발송되지 않아야 함
    stage_changes = []
    next_msg_options = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            obj = json.loads(line[6:])
        except Exception:
            continue
        if obj.get("type") == "stage_change":
            stage_changes.append(obj.get("stage"))
        if obj.get("type") == "next_message":
            msg = obj.get("message") or {}
            for o in msg.get("options") or []:
                next_msg_options.append(o.get("id"))
    assert "image" not in stage_changes, f"IMAGE 단계로 진입하면 안 됨: {stage_changes}"
    # 2) IMAGE 옵션(전체 만들기/이미지 없이 종료) 메시지 미발송
    assert "all" not in next_msg_options
    # 3) FEEDBACK 단계로 전이
    assert "feedback" in stage_changes

    # 4) state 검증 — auto_image=False, stage=FEEDBACK
    from blog_chat_state import get_session, Stage
    state = get_session(sid, clinic_id=1)
    assert state.auto_image is False
    assert state.stage == Stage.FEEDBACK


# ── 3) 한도 초과 → 429 ────────────────────────────────────────


def test_quota_exceeded_returns_429():
    """CONFIRM_IMAGE stage 진입 시 한도 초과 → JSON 429 응답 (2026-05-03 신 흐름).

    TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → SSE 트리거 시 quota 검사.
    """
    from fastapi import HTTPException

    r1 = client.post("/api/blog-chat/turn", json={"user_input": "허리디스크"})
    sid = r1.json()["session_id"]
    # SEO skip → EMPHASIS
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "넘김"})
    # EMPHASIS skip → LENGTH
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "건너뛰기"})
    # LENGTH 선택 → CONFIRM_IMAGE
    client.post("/api/blog-chat/turn",
                json={"session_id": sid, "user_input": "2"})

    def raise_quota(_):
        raise HTTPException(status_code=429, detail="이번 달 한도 초과.")

    with patch("routers.blog.check_blog_limit", side_effect=raise_quota):
        # CONFIRM_IMAGE 응답이 quota check 트리거
        res = client.post("/api/blog-chat/turn",
                          json={"session_id": sid, "user_input": "아니오"})
    assert res.status_code == 429
    data = res.json()
    assert data.get("kind") == "quota_exceeded"


# ── 4) /blog/chat 베타 게이트 ─────────────────────────────────


def test_blog_chat_page_redirects_when_beta_disabled():
    """chat_beta_enabled=0 (기본) → /blog 리다이렉트."""
    # 인증 토큰 없이도 redirect 됨 (먼저 /login으로). 이 케이스는 베타 플래그
    # 검사 자체를 우회하므로 별도 검증.
    # 실제 베타 게이트는 토큰 보유 + clinic_id=1이지만 chat_beta_enabled=0 이어야 함.
    # TestClient는 cookie 없이 호출 → 첫 redirect는 /login.
    res = client.get("/blog/chat", follow_redirects=False)
    assert res.status_code in (302, 307)
    # 토큰 없이는 /login으로 (정상)
    assert res.headers.get("location") in ("/login", "/blog")


def test_blog_chat_page_serves_html_when_beta_enabled(monkeypatch, tmp_path):
    """chat_beta_enabled=1 + 유효한 토큰 → blog_chat.html 응답."""
    from auth_manager import create_access_token, COOKIE_NAME
    from db_manager import get_db

    with get_db() as conn:
        conn.execute("UPDATE clinics SET chat_beta_enabled = 1 WHERE id = 1")

    token = create_access_token(user_id=1, clinic_id=1, role="chief_director")
    res = client.get("/blog/chat", cookies={COOKIE_NAME: token},
                     follow_redirects=False)
    assert res.status_code == 200
    assert b"chat-app" in res.content  # blog_chat.html 본문 검증


# ── 5) IMAGE 'all' → SSE stage_text 5단계 ─────────────────────


def test_image_all_option_yields_gallery_and_advances_to_feedback(monkeypatch):
    """IMAGE 'all' → image2 호출(mock) → 갤러리 메시지(meta.images) → FEEDBACK 전이."""
    # 직접 IMAGE stage로 진입한 세션 시드
    from blog_chat_state import (
        Stage, append_message, create_session, save_session, transition,
    )
    state = create_session(clinic_id=1, user_id=1)
    state.topic = "허리디스크"
    state.length_chars = 2000
    state.seo_keywords = ["추나"]
    state.blog_text = "본문 더미"
    # 2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE
    transition(state, Stage.SEO)
    transition(state, Stage.EMPHASIS)
    transition(state, Stage.LENGTH)
    transition(state, Stage.CONFIRM_IMAGE)
    transition(state, Stage.GENERATING)
    transition(state, Stage.IMAGE)
    append_message(state, "assistant", "이미지 5장을 만들까요?",
                   options=[{"id": "all", "label": "전체 만들기"},
                            {"id": "none", "label": "이미지 없이 종료"}])
    save_session(state)

    # 외부 의존성 mock — Anthropic 프롬프트 추출 / OpenAI 이미지 / DB session
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    import image_prompt_generator
    def fake_prompt_stream(*a, **kw):
        yield 'data: {"status":"analyzing","message":"분석"}\n\n'
        yield 'data: {"status":"generating","message":"생성"}\n\n'
        yield 'data: {"done":true,"prompts":["P1","P2","P3","P4","P5"]}\n\n'
    monkeypatch.setattr(
        image_prompt_generator, "generate_image_prompts_stream", fake_prompt_stream,
    )

    import plan_guard
    monkeypatch.setattr(
        plan_guard, "get_effective_plan", lambda cid: {"plan_id": "standard"},
    )

    import image_session_manager
    monkeypatch.setattr(
        image_session_manager, "create_session", lambda **kw: "fake-img-sid",
    )

    import image_generator
    # blog_chat_flow는 ai_client.call_openai_image_generate를 직접 5번 호출 (2026-05-01)
    import ai_client
    from ai_client import AIResponse

    def fake_image_call(prompt, size, quality, n):
        return [AIResponse(content=f"b64-{prompt[:2]}", usage={"mode": "test"})]

    monkeypatch.setattr(ai_client, "call_openai_image_generate", fake_image_call)
    # blog_chat_flow가 import할 때 모듈 attribute 직접 사용하므로 동일 함수 패치
    import blog_chat_flow as _bcf
    monkeypatch.setattr(_bcf, "_AIClientError", ai_client.AIClientError, raising=False)
    monkeypatch.setattr(
        image_generator, "get_quota_status",
        lambda *a, **kw: {"regen": {"used": 0, "limit": 1},
                          "edit":  {"used": 0, "limit": 2}},
    )

    res = client.post("/api/blog-chat/turn",
                      json={"session_id": state.session_id,
                            "user_input": "전체 만들기"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")
    body = res.content.decode("utf-8")

    types: list = []
    stage_texts: list = []
    gallery_msgs: list = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            obj = json.loads(line[6:])
        except Exception:
            continue
        types.append(obj.get("type"))
        if obj.get("type") == "stage_text":
            stage_texts.append(obj.get("text", ""))
        if obj.get("type") == "next_message":
            msg = obj.get("message") or {}
            if (msg.get("meta") or {}).get("kind") == "image_gallery":
                gallery_msgs.append(msg)

    # 단계 텍스트 일부 + 갤러리 메시지 1개 + 마지막은 done
    assert any("분석" in t or "컨셉" in t for t in stage_texts)
    # 이미지 5장 진행 안내 — 각 호출 직전 "이미지 N/5 ... (약 N분 남음)" stage_text
    progress_texts = [t for t in stage_texts if "이미지" in t and "/5" in t]
    assert len(progress_texts) == 5
    # negative_prompt가 있으면 본문에 통합되었는지 (gpt-image-2 호환)
    # 이 테스트의 fake prompts는 string이라 분기 안 타지만 단위테스트는 별도 추가됨
    # 첫 호출은 5장 모두 남았으므로 5분, 마지막은 1분
    assert "약 5분 남음" in progress_texts[0]
    assert "약 1분 남음" in progress_texts[4]
    assert "이미지 1/5" in progress_texts[0]
    assert "이미지 5/5" in progress_texts[4]
    assert "next_message" in types
    assert len(gallery_msgs) == 1
    g_meta = gallery_msgs[0]["meta"]
    assert len(g_meta["images"]) == 5
    assert g_meta["image_session_id"] == "fake-img-sid"
    assert g_meta["plan_id"] == "standard"
    assert g_meta["quota"]["regen"]["limit"] == 1
    assert g_meta["quota"]["edit"]["limit"] == 2
    assert g_meta["filename_base"] == "허리디스크"
    assert types[-1] == "done"


def test_negatives_injected_into_prompt_body(monkeypatch):
    """Stage 2 출력의 negative_prompt 필드가 prompt 본문 끝에 'Negative aspects to avoid:'로 통합 (2026-05-01).

    gpt-image-2는 별도 negative_prompt 인자가 없어 본문에 박지 않으면 무시됨.
    """
    from blog_chat_state import (
        Stage, append_message, create_session, save_session, transition,
    )
    state = create_session(clinic_id=1, user_id=1)
    state.topic = "허리디스크"
    state.blog_text = "본문 더미"
    # 2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE
    transition(state, Stage.SEO)
    transition(state, Stage.EMPHASIS)
    transition(state, Stage.LENGTH)
    transition(state, Stage.CONFIRM_IMAGE)
    transition(state, Stage.GENERATING)
    transition(state, Stage.IMAGE)
    append_message(state, "assistant", "이미지 5장을 만들까요?",
                   options=[{"id": "all", "label": "전체 만들기"}])
    save_session(state)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("IMAGE_INJECT_NEGATIVES", "1")

    # Stage 2 결과를 dict 형태(prompt + negative_prompt)로 반환
    import image_prompt_generator
    fake_prompts_payload = [
        {"number": i + 1,
         "title_ko": f"장면{i+1}",
         "module": (i % 11) + 1,
         "prompt": f"Scene {i+1} body",
         "negative_prompt": f"Chinese tunic suit, red lanterns {i+1}"}
        for i in range(5)
    ]

    def fake_prompt_stream(*a, **kw):
        yield 'data: {"status":"analyzing","message":"분석"}\n\n'
        yield 'data: {"status":"generating","message":"생성"}\n\n'
        yield ('data: '
               + json.dumps({"done": True, "prompts": fake_prompts_payload})
               + '\n\n')
    monkeypatch.setattr(
        image_prompt_generator, "generate_image_prompts_stream", fake_prompt_stream,
    )

    import plan_guard
    monkeypatch.setattr(
        plan_guard, "get_effective_plan", lambda cid: {"plan_id": "standard"},
    )
    import image_session_manager
    monkeypatch.setattr(
        image_session_manager, "create_session", lambda **kw: "fake-img-sid",
    )

    captured_prompts = []

    import ai_client
    from ai_client import AIResponse

    def fake_call(prompt, size, quality, n):
        captured_prompts.append(prompt)
        return [AIResponse(content="b64", usage={"mode": "test"})]

    monkeypatch.setattr(ai_client, "call_openai_image_generate", fake_call)
    import image_generator
    monkeypatch.setattr(
        image_generator, "get_quota_status",
        lambda *a, **kw: {"regen": {"used": 0, "limit": 1},
                          "edit":  {"used": 0, "limit": 2}},
    )

    res = client.post("/api/blog-chat/turn",
                      json={"session_id": state.session_id,
                            "user_input": "전체 만들기"})
    assert res.status_code == 200
    # 5번 호출 모두 negative가 본문 끝에 통합되었는지
    assert len(captured_prompts) == 5
    for i, p in enumerate(captured_prompts):
        assert "Scene" in p and "body" in p
        assert "Negative aspects to avoid:" in p, (
            f"Scene {i+1} negative not injected: {p!r}"
        )
        assert "Chinese tunic suit" in p


def test_negatives_can_be_disabled_by_env(monkeypatch):
    """IMAGE_INJECT_NEGATIVES=0 → negative 합치지 않음 (역효과 발생 시 즉시 fallback)."""
    from blog_chat_state import (
        Stage, append_message, create_session, save_session, transition,
    )
    state = create_session(clinic_id=1, user_id=1)
    state.topic = "허리디스크"
    state.blog_text = "본문 더미"
    # 2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE
    transition(state, Stage.SEO)
    transition(state, Stage.EMPHASIS)
    transition(state, Stage.LENGTH)
    transition(state, Stage.CONFIRM_IMAGE)
    transition(state, Stage.GENERATING)
    transition(state, Stage.IMAGE)
    append_message(state, "assistant", "이미지 5장을 만들까요?",
                   options=[{"id": "all", "label": "전체 만들기"}])
    save_session(state)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("IMAGE_INJECT_NEGATIVES", "0")

    import image_prompt_generator
    fake_prompts_payload = [
        {"number": i + 1,
         "title_ko": f"장면{i+1}",
         "module": (i % 11) + 1,
         "prompt": f"Scene {i+1} body",
         "negative_prompt": "Chinese tunic suit"}
        for i in range(5)
    ]

    def fake_prompt_stream(*a, **kw):
        yield ('data: '
               + json.dumps({"done": True, "prompts": fake_prompts_payload})
               + '\n\n')
    monkeypatch.setattr(
        image_prompt_generator, "generate_image_prompts_stream", fake_prompt_stream,
    )

    import plan_guard
    monkeypatch.setattr(
        plan_guard, "get_effective_plan", lambda cid: {"plan_id": "standard"},
    )
    import image_session_manager
    monkeypatch.setattr(
        image_session_manager, "create_session", lambda **kw: "fake-img-sid",
    )

    captured_prompts = []
    import ai_client
    from ai_client import AIResponse

    def fake_call(prompt, size, quality, n):
        captured_prompts.append(prompt)
        return [AIResponse(content="b64", usage={"mode": "test"})]

    monkeypatch.setattr(ai_client, "call_openai_image_generate", fake_call)
    import image_generator
    monkeypatch.setattr(
        image_generator, "get_quota_status",
        lambda *a, **kw: {"regen": {"used": 0, "limit": 1},
                          "edit":  {"used": 0, "limit": 2}},
    )

    client.post("/api/blog-chat/turn",
                json={"session_id": state.session_id,
                      "user_input": "전체 만들기"})
    assert len(captured_prompts) == 5
    for p in captured_prompts:
        assert "Negative aspects to avoid:" not in p


def test_image_all_option_without_api_key_yields_error(monkeypatch):
    """ANTHROPIC_API_KEY 없으면 첫 stage_change 후 error → done."""
    from blog_chat_state import (
        Stage, append_message, create_session, save_session, transition,
    )
    state = create_session(clinic_id=1, user_id=1)
    state.topic = "허리디스크"
    state.blog_text = "본문 더미"
    # 2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE
    transition(state, Stage.SEO)
    transition(state, Stage.EMPHASIS)
    transition(state, Stage.LENGTH)
    transition(state, Stage.CONFIRM_IMAGE)
    transition(state, Stage.GENERATING)
    transition(state, Stage.IMAGE)
    append_message(state, "assistant", "이미지 5장을 만들까요?",
                   options=[{"id": "all", "label": "전체 만들기"}])
    save_session(state)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post("/api/blog-chat/turn",
                      json={"session_id": state.session_id,
                            "user_input": "전체 만들기"})
    assert res.status_code == 200
    body = res.content.decode("utf-8")
    types: list = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                types.append(json.loads(line[6:]).get("type"))
            except Exception:
                pass
    assert "error" in types
    assert types[-1] == "done"


# ── 6) image_partial_frames env gate ──────────────────────────


def test_image_partial_frames_default_zero(monkeypatch):
    """기본 (env unset) — partial_frames=0 (M0 게이트)."""
    from blog_chat_flow import image_partial_frames
    monkeypatch.delenv("BLOG_CHAT_IMAGE_PARTIAL_FRAMES", raising=False)
    assert image_partial_frames() == 0


def test_image_partial_frames_m1_active(monkeypatch):
    monkeypatch.setenv("BLOG_CHAT_IMAGE_PARTIAL_FRAMES", "3")
    from blog_chat_flow import image_partial_frames
    assert image_partial_frames() == 3
