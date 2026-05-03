"""
blog_chat_flow 단위 테스트 (Step 1 Phase 1D-1)

검증:
  - match_option: 번호 / 정확 라벨 / 부분 일치 / 동그라미숫자 / 한국어 서수
  - process_turn: TOPIC → SEO → EMPHASIS → LENGTH → (QUESTIONS) → CONFIRM_IMAGE → DONE 흐름 (2026-05-03 신 흐름)
    · 정상 흐름 (옵션 칩 클릭)
    · 직접 글자 수 입력 (custom)
    · 모호한 입력 → ambiguous 메시지
    · SEO [넘김] / 키워드 쉼표 분리
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
    db_file = tmp_path / "blog_chat_flow_test.db"
    import db_manager
    monkeypatch.setattr(db_manager, "DB_PATH", db_file)
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
    import blog_chat_state
    blog_chat_state._cache_clear()
    yield
    blog_chat_state._cache_clear()


@pytest.fixture(autouse=True)
def _disable_questions_default(monkeypatch):
    """기본은 BLOG_OPTION_STAGES 비활성 — LENGTH → CONFIRM_IMAGE 직진 흐름 보존 (2026-05-03 신 흐름).

    QUESTIONS 흐름 검증은 TestProcessTurnQuestions 클래스에서
    명시적으로 _enable_questions fixture로 활성.
    """
    import blog_chat_options
    monkeypatch.setattr(blog_chat_options, "BLOG_OPTION_STAGES", [])


@pytest.fixture
def _enable_questions(monkeypatch):
    """BLOG_OPTION_STAGES 5개 stage 복원 (2026-05-04 tone 추가).

    LENGTH 다음 첫 question = tone (5문항 ① blog_tone 매핑용).
    """
    import blog_chat_options
    original_stages = [
        {
            "key": "tone",
            "prompt": "어떤 말투로 쓸까요?",
            "options": [
                {"id": "공감형", "label": "공감형"},
                {"id": "전문가형", "label": "전문가형"},
                {"id": "친근형", "label": "친근형"},
                {"id": "절제형", "label": "절제형"},
                {"id": "위트형", "label": "위트형"},
            ],
        },
        {
            "key": "mode",
            "prompt": "어떤 목적의 글인가요?",
            "options": [
                {"id": "정보", "label": "정보 제공"},
                {"id": "내원", "label": "내원 유도"},
            ],
        },
        {
            "key": "reader_level",
            "prompt": "독자는 누구인가요?",
            "options": [
                {"id": "일반인", "label": "일반인"},
                {"id": "건강 관심층", "label": "건강 관심층"},
                {"id": "한의학 관심층", "label": "한의학 관심층"},
            ],
        },
        {
            "key": "explanation_type",
            "prompt": "어떤 관점으로 설명할까요?",
            "options": [
                {"id": "변증시치", "label": "변증시치"},
                {"id": "체질의학", "label": "체질의학(사상체질)"},
                {"id": "skip", "label": "건너뛰기"},
            ],
            "skip_id": "skip",
        },
        {
            "key": "format_id",
            "prompt": "글 형식을 골라주세요.",
            "options": [
                {"id": "auto", "label": "자동"},
                {"id": "information", "label": "정보형"},
                {"id": "qna", "label": "Q&A"},
            ],
            "skip_id": "auto",
        },
    ]
    monkeypatch.setattr(blog_chat_options, "BLOG_OPTION_STAGES", original_stages)


# ── match_option ──────────────────────────────────────────


class TestMatchOption:
    def test_number_match(self):
        from blog_chat_flow import LENGTH_OPTIONS, match_option

        assert match_option(LENGTH_OPTIONS, "1")["id"] == "1500"
        assert match_option(LENGTH_OPTIONS, "2번")["id"] == "2000"
        assert match_option(LENGTH_OPTIONS, "(3)")["id"] == "2800"
        assert match_option(LENGTH_OPTIONS, "②")["id"] == "2000"

    def test_korean_ordinal(self):
        from blog_chat_flow import LENGTH_OPTIONS, match_option

        assert match_option(LENGTH_OPTIONS, "첫번째")["id"] == "1500"
        assert match_option(LENGTH_OPTIONS, "두 번째")["id"] == "2000"

    def test_label_partial(self):
        from blog_chat_flow import LENGTH_OPTIONS, match_option

        # "표준" 부분 일치 → 2000
        opt = match_option(LENGTH_OPTIONS, "표준")
        assert opt is not None
        assert opt["id"] == "2000"

    def test_no_match(self):
        from blog_chat_flow import LENGTH_OPTIONS, match_option

        assert match_option(LENGTH_OPTIONS, "이상한입력") is None
        assert match_option(LENGTH_OPTIONS, "") is None
        assert match_option(LENGTH_OPTIONS, "9") is None  # 옵션 4개 초과

    def test_out_of_range(self):
        from blog_chat_flow import SEO_OPTIONS, match_option

        # SEO_OPTIONS는 1개 — "2"는 범위 밖
        assert match_option(SEO_OPTIONS, "2") is None


# ── process_turn 흐름 ─────────────────────────────────────


class TestProcessTurnHappyPath:
    def test_full_flow_with_chips(self):
        """신 흐름 (2026-05-03): TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → DONE."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)

        # TOPIC: 주제 입력 → SEO 진입
        r1 = process_turn(state, "허리디스크")
        assert state.topic == "허리디스크"
        assert state.stage == Stage.SEO
        assert any(m["role"] == "assistant" for m in r1["messages"])

        # SEO: 키워드 입력 → EMPHASIS
        r2 = process_turn(state, "추나치료, 디스크")
        assert state.seo_keywords == ["추나치료", "디스크"]
        assert state.stage == Stage.EMPHASIS

        # EMPHASIS: 강조 사항 입력 → LENGTH
        r3 = process_turn(state, "추나 + 침 병행 치료가 핵심")
        assert state.emphasis == "추나 + 침 병행 치료가 핵심"
        assert state.stage == Stage.LENGTH
        # 응답에 LENGTH 옵션 4개
        last_assist = [m for m in r3["messages"] if m["role"] == "assistant"][-1]
        assert len(last_assist["options"]) == 4

        # LENGTH: "2" → 표준 2000자 → CONFIRM_IMAGE (BLOG_OPTION_STAGES 비활성)
        process_turn(state, "2")
        assert state.length_chars == 2000
        assert state.stage == Stage.CONFIRM_IMAGE

        # CONFIRM_IMAGE에 "아니오" 응답 → fallback에서 DONE까지 진행
        process_turn(state, "아니오")
        assert state.stage == Stage.DONE
        assert state.auto_image is False


class TestProcessTurnLengthCustom:
    def test_custom_label_then_direct_number(self):
        """[직접 입력] 클릭 → 안내 → 숫자 입력 → CONFIRM_IMAGE (2026-05-03 신 흐름)."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        # 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")
        assert state.stage == Stage.LENGTH
        # 직접 입력 클릭
        process_turn(state, "직접 입력")
        # 여전히 LENGTH (custom 분기 → 안내 메시지만)
        assert state.stage == Stage.LENGTH
        # 숫자 입력 → CONFIRM_IMAGE (questions 비활성)
        process_turn(state, "1800")
        assert state.length_chars == 1800
        assert state.stage == Stage.CONFIRM_IMAGE

    def test_direct_number_first(self):
        """LENGTH에서 옵션 매칭 실패 + 숫자 직접 입력 fallback (2026-05-03 신 흐름)."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")
        process_turn(state, "1800")
        assert state.length_chars == 1800
        assert state.stage == Stage.CONFIRM_IMAGE


class TestProcessTurnAmbiguous:
    def test_unparseable_length_input(self):
        """LENGTH에서 모호한 입력 → ambiguous 메시지 + stage 유지 (2026-05-03 신 흐름)."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        # 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")
        assert state.stage == Stage.LENGTH
        r = process_turn(state, "음... 잘 모르겠어요")
        assert state.stage == Stage.LENGTH  # 전이 안 함
        # ambiguous 메시지
        last_assist = [m for m in r["messages"] if m["role"] == "assistant"][-1]
        assert last_assist["meta"].get("ambiguous") is True
        # 옵션 칩 다시 노출
        assert len(last_assist["options"]) == 4


class TestProcessTurnSeo:
    """2026-05-03 신 흐름: TOPIC → SEO 직진. SEO 다음은 EMPHASIS."""

    def test_skip_keyword(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        process_turn(state, "허리디스크")
        # SEO에서 "넘김" → 키워드 비움 + EMPHASIS
        process_turn(state, "넘김")
        assert state.seo_keywords == []
        assert state.stage == Stage.EMPHASIS
        process_turn(state, "건너뛰기")
        assert state.stage == Stage.LENGTH
        process_turn(state, "2")
        assert state.stage == Stage.CONFIRM_IMAGE
        process_turn(state, "아니오")
        assert state.stage == Stage.DONE

    def test_empty_input_treated_as_skip(self):
        """SEO에서 빈 입력 → skip."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        process_turn(state, "허리디스크")
        # SEO stage에서 빈 입력
        process_turn(state, "")
        assert state.seo_keywords == []
        assert state.stage == Stage.EMPHASIS
        process_turn(state, "")  # EMPHASIS 빈 입력 → LENGTH
        assert state.stage == Stage.LENGTH
        process_turn(state, "2")
        assert state.stage == Stage.CONFIRM_IMAGE
        process_turn(state, "아니오")
        assert state.stage == Stage.DONE

    def test_max_5_keywords(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import create_session

        state = create_session(clinic_id=1, user_id=10)
        process_turn(state, "허리디스크")
        # SEO에서 6개 이상 입력 → 5개로 절단
        process_turn(state, "a, b, c, d, e, f, g")
        assert state.seo_keywords == ["a", "b", "c", "d", "e"]


class TestLLMFallback:
    """1D-2: 결정론 매칭 None일 때 Haiku fallback 호출 + 옵션 매칭 성공 시 정상 진행 (2026-05-03 신 흐름).

    LENGTH stage는 EMPHASIS 이후 진입.
    """

    def _advance_to_length(self, state):
        from blog_chat_flow import process_turn
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")

    def test_llm_fallback_invoked_when_ambiguous(self, monkeypatch):
        import blog_chat_flow as flow
        from blog_chat_state import Stage, create_session

        called = {"n": 0}

        def fake_llm(options, user_input):
            called["n"] += 1
            # "추천으로" → 표준 옵션 매핑 (id="2000")
            return {"id": "2000", "label": "표준 (2,000자, 추천)"}

        monkeypatch.setattr(flow, "llm_match_option", fake_llm)

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_length(state)
        # LENGTH에서 결정론 매칭이 안 되는 자연어
        flow.process_turn(state, "추천으로 갈게")
        assert called["n"] == 1
        assert state.length_chars == 2000
        assert state.stage == Stage.CONFIRM_IMAGE

    def test_llm_fallback_returns_none_keeps_ambiguous(self, monkeypatch):
        import blog_chat_flow as flow
        from blog_chat_state import Stage, create_session

        monkeypatch.setattr(flow, "llm_match_option", lambda o, u: None)

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_length(state)
        r = flow.process_turn(state, "음... 잘 모르겠어요")
        assert state.stage == Stage.LENGTH
        last_assist = [m for m in r["messages"] if m["role"] == "assistant"][-1]
        assert last_assist["meta"].get("ambiguous") is True

    def test_llm_not_called_when_deterministic_matches(self, monkeypatch):
        """결정론으로 매칭되면 LLM 호출 안 함 (비용 절감)."""
        import blog_chat_flow as flow
        from blog_chat_state import create_session

        called = {"n": 0}

        def fake_llm(options, user_input):
            called["n"] += 1
            return None

        monkeypatch.setattr(flow, "llm_match_option", fake_llm)

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_length(state)
        flow.process_turn(state, "2")  # 번호 매칭 → LLM 미호출
        assert called["n"] == 0
        assert state.length_chars == 2000


class TestProcessTurnDone:
    def test_input_after_done_returns_guidance(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        # 신 흐름 (2026-05-03): TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → DONE
        process_turn(state, "허리디스크")
        process_turn(state, "넘김")          # SEO skip → EMPHASIS
        process_turn(state, "건너뛰기")       # EMPHASIS skip → LENGTH
        process_turn(state, "2")              # LENGTH → CONFIRM_IMAGE
        process_turn(state, "아니오")         # CONFIRM_IMAGE → DONE
        assert state.stage == Stage.DONE
        # DONE 상태에서 추가 입력
        r = process_turn(state, "또 쓸게요")
        last_assist = [m for m in r["messages"] if m["role"] == "assistant"][-1]
        assert "완성" in last_assist["text"] or "새 글" in last_assist["text"]


# ── 옵션 카탈로그 통합 (QUESTIONS 4 stage) ─────────────────────


class TestProcessTurnQuestions:
    """LENGTH 완료 후 BLOG_OPTION_STAGES 4 stage를 chip으로 순차 노출 (2026-05-03 신 흐름).

    각 stage 답변이 state.questions_answered에 누적되고
    마지막 stage 후 CONFIRM_IMAGE로 진입.
    """

    def _advance_to_length(self, state):
        from blog_chat_flow import process_turn
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")

    def test_full_flow_topic_length_questions_seo(self, _enable_questions):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_length(state)
        # LENGTH "2" → QUESTIONS 진입 (직진 CONFIRM_IMAGE 아님)
        process_turn(state, "2")
        assert state.stage == Stage.QUESTIONS
        assert state.length_chars == 2000

        # Q1 tone: "1" → 공감형
        process_turn(state, "1")
        assert state.stage == Stage.QUESTIONS
        assert state.questions_answered[-1] == {
            "key": "tone", "id": "공감형", "label": "공감형",
        }

        # Q2 mode: "1" → 정보
        process_turn(state, "1")
        assert state.stage == Stage.QUESTIONS
        assert state.questions_answered[-1] == {
            "key": "mode", "id": "정보", "label": "정보 제공",
        }

        # Q3 reader_level: "1" → 일반인
        process_turn(state, "1")
        assert state.questions_answered[-1]["key"] == "reader_level"
        assert state.questions_answered[-1]["id"] == "일반인"

        # Q4 explanation_type: "3" → skip
        process_turn(state, "3")
        assert state.questions_answered[-1]["key"] == "explanation_type"
        assert state.questions_answered[-1]["id"] == "skip"

        # Q5 format_id: "1" → auto. 마지막 → CONFIRM_IMAGE 전이
        process_turn(state, "1")
        assert state.questions_answered[-1]["key"] == "format_id"
        assert state.stage == Stage.CONFIRM_IMAGE

    def test_ambiguous_input_keeps_same_question_stage(self, _enable_questions):
        """질문 stage에서 모호한 입력 → 같은 질문 재노출 + answered 미증가."""
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_length(state)
        process_turn(state, "2")  # → QUESTIONS Q1
        before_count = len(state.questions_answered)

        r = process_turn(state, "잘 모르겠어요")
        assert state.stage == Stage.QUESTIONS
        assert len(state.questions_answered) == before_count
        last_assist = [m for m in r["messages"] if m["role"] == "assistant"][-1]
        assert last_assist["meta"].get("ambiguous") is True


class TestToBlogArgsMapping:
    """blog_chat_options.to_blog_args — questions_answered → generate_blog_stream 인자."""

    def test_full_mapping(self):
        from blog_chat_options import to_blog_args

        args = to_blog_args({
            "tone": "공감형",
            "mode": "내원",
            "reader_level": "한의학 관심층",
            "explanation_type": "변증시치",
            "format_id": "qna",
        })
        assert args == {
            "tone": "공감형",
            "mode": "내원",
            "reader_level": "한의학 관심층",
            "explanation_types": ["변증시치"],
            "format_id": "qna",
        }

    def test_skip_normalization(self):
        from blog_chat_options import to_blog_args

        # explanation_type=skip → None / format_id=auto → None
        args = to_blog_args({
            "mode": "정보",
            "reader_level": "일반인",
            "explanation_type": "skip",
            "format_id": "auto",
        })
        assert args["explanation_types"] is None
        assert args["format_id"] is None

    def test_missing_keys_defaults(self):
        from blog_chat_options import to_blog_args

        args = to_blog_args({})
        assert args["tone"] is None
        assert args["mode"] == "정보"
        assert args["reader_level"] == "일반인"
        assert args["explanation_types"] is None
        assert args["format_id"] is None


# ── 1D-3: SSE streaming generator ─────────────────────────────


class TestStreamingForSeo:
    """SEO 입력 → SSE 본문 streaming → IMAGE 옵션 메시지 종료."""

    def test_generates_expected_frame_sequence(self, monkeypatch):
        import blog_chat_flow as flow
        from blog_chat_state import Stage, create_session

        # generate_blog_stream을 fake로 교체 (text 2 chunks + done)
        def fake_stream(*args, **kwargs):
            yield 'data: {"text": "본문 시작 "}\n\n'
            yield 'data: {"status": "본문 작성 중..."}\n\n'
            yield 'data: {"text": "이어지는 내용."}\n\n'
            yield 'data: {"done": true, "usage": {"cost_krw": 12}}\n\n'

        # 본문 생성 모듈을 monkeypatch
        import blog_generator
        monkeypatch.setattr(blog_generator, "generate_blog_stream", fake_stream)
        # save_blog_entry는 dummy로
        import blog_history
        monkeypatch.setattr(blog_history, "save_blog_entry", lambda *a, **k: 999)
        # API 키 set
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        state = create_session(clinic_id=1, user_id=10)
        # 신 흐름 (2026-05-03): TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → SSE
        flow.process_turn(state, "허리디스크")
        assert state.stage == Stage.SEO
        flow.process_turn(state, "추나, 디스크")
        assert state.stage == Stage.EMPHASIS
        assert state.seo_keywords == ["추나", "디스크"]
        flow.process_turn(state, "건너뛰기")
        assert state.stage == Stage.LENGTH
        flow.process_turn(state, "2")
        assert state.stage == Stage.CONFIRM_IMAGE

        # SSE generator 실행 — CONFIRM_IMAGE에 "아니오" 응답이 SSE 트리거 input
        gen = flow.process_turn_streaming(state, "아니오")
        frames = []
        import json as _json
        for raw in gen:
            assert raw.startswith("data: ")
            assert raw.endswith("\n\n")
            payload = _json.loads(raw[len("data: "):].strip())
            frames.append(payload)

        types = [f["type"] for f in frames]
        # 기대 순서: user_message → stage_change → stage_text → message_start
        # → token (≥1) → message_done → next_message → stage_change → done
        assert "user_message" in types
        assert "message_start" in types
        assert "token" in types
        assert "message_done" in types
        assert "next_message" in types
        assert types[-1] == "done"

        # 본문 누적이 message_done.text에 들어갔는지
        msg_done = next(f for f in frames if f["type"] == "message_done")
        assert "본문 시작" in msg_done["message"]["text"]
        assert "이어지는 내용" in msg_done["message"]["text"]

        # state 업데이트 확인
        assert state.stage == Stage.IMAGE
        assert state.blog_text  # 본문 저장
        assert state.blog_history_id == 999
        assert state.seo_keywords == ["추나", "디스크"]

    def test_missing_api_key_yields_error_frame(self, monkeypatch):
        import blog_chat_flow as flow
        from blog_chat_state import Stage, create_session

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        state = create_session(clinic_id=1, user_id=10)
        # 신 흐름 (2026-05-03): TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → SSE
        flow.process_turn(state, "허리디스크")
        flow.process_turn(state, "추나, 디스크")
        flow.process_turn(state, "건너뛰기")
        flow.process_turn(state, "2")
        assert state.stage == Stage.CONFIRM_IMAGE
        gen = flow.process_turn_streaming(state, "넘김")
        import json as _json
        frames = [_json.loads(r[len("data: "):].strip()) for r in gen]
        assert any(f["type"] == "error" for f in frames)
        assert frames[-1]["type"] == "done"


# ── 1D-4: IMAGE / FEEDBACK / TTL ──────────────────────────────


class TestImageStage:
    """IMAGE stage placeholder: 옵션 매칭 → FEEDBACK 전이."""

    def _force_image_stage(self, state):
        """2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE."""
        from blog_chat_state import Stage, transition
        from blog_chat_flow import process_turn
        process_turn(state, "허리디스크")    # TOPIC → SEO
        process_turn(state, "추나, 디스크")   # SEO → EMPHASIS
        process_turn(state, "건너뛰기")       # EMPHASIS → LENGTH
        process_turn(state, "2")              # LENGTH → CONFIRM_IMAGE
        # CONFIRM_IMAGE → GENERATING → IMAGE 직접 전이 (SSE 우회)
        transition(state, Stage.GENERATING)
        transition(state, Stage.IMAGE)

    def test_choosing_all_advances_to_feedback(self, monkeypatch):
        from blog_chat_flow import IMAGE_OPTIONS, process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._force_image_stage(state)
        assert state.stage == Stage.IMAGE

        r = process_turn(state, "1")
        assert state.stage == Stage.FEEDBACK
        # 마지막 어시스턴트 메시지에 FEEDBACK 옵션 [넘김]
        last_assist = [m for m in r["messages"] if m["role"] == "assistant"][-1]
        assert any(o["id"] == "skip" for o in last_assist["options"])

    def test_image_skip_advances_to_feedback(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._force_image_stage(state)
        process_turn(state, "이미지 없이 종료")
        assert state.stage == Stage.FEEDBACK


class TestFeedbackStage:
    """2026-05-03 신 흐름: TOPIC → SEO → EMPHASIS → LENGTH → CONFIRM_IMAGE → GENERATING → IMAGE → FEEDBACK."""

    def _advance_to_feedback(self, state):
        from blog_chat_state import Stage, transition
        from blog_chat_flow import process_turn
        process_turn(state, "허리디스크")
        process_turn(state, "추나, 디스크")
        process_turn(state, "건너뛰기")
        process_turn(state, "2")
        transition(state, Stage.GENERATING)
        transition(state, Stage.IMAGE)
        transition(state, Stage.FEEDBACK)

    def test_skip_to_done(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_feedback(state)
        process_turn(state, "넘김")
        assert state.stage == Stage.DONE

    def test_free_input_to_done(self):
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_feedback(state)
        r = process_turn(state, "이미지 생성이 좀 느렸어요")
        assert state.stage == Stage.DONE
        # 응답에 감사 인사 + [새 글 시작] 옵션 메시지 둘 다 있어야 함
        assist_msgs = [m for m in r["messages"] if m["role"] == "assistant"]
        assert any("감사" in m["text"] for m in assist_msgs)
        last_assist = assist_msgs[-1]
        assert last_assist["meta"].get("new_session_action") is True
        assert any(o["id"] == "new_session" for o in last_assist["options"])

    def test_free_input_persists_feedback(self, monkeypatch):
        """자유 입력 → routers.dashboard._persist_feedback 호출 + context에 source/session/주제 메타 포함."""
        from routers import dashboard as _dashboard
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session, transition

        calls = []
        monkeypatch.setattr(_dashboard, "_persist_feedback", lambda **k: calls.append(k))

        state = create_session(clinic_id=42, user_id=99)
        # 신 흐름 진행 + 추가 메타 세팅 (seo_keywords는 흐름에서 이미 채워지지만 명시적 덮어쓰기)
        from blog_chat_flow import process_turn as _pt
        _pt(state, "허리디스크")
        _pt(state, "추나치료, 디스크")  # 흐름에서 SEO에 저장됨
        _pt(state, "건너뛰기")
        _pt(state, "2")  # 표준 2000자
        state.blog_history_id = 7
        transition(state, Stage.GENERATING)
        transition(state, Stage.IMAGE)
        transition(state, Stage.FEEDBACK)

        process_turn(state, "이미지 생성이 좀 느렸어요")

        assert len(calls) == 1
        c = calls[0]
        assert c["clinic_id"] == 42
        assert c["user_id"] == 99
        assert c["page"] == "blog_chat"
        assert c["message"] == "이미지 생성이 좀 느렸어요"
        ctx = c["context"]
        assert ctx["source"] == "blog_chat"
        assert ctx["session_id"] == state.session_id
        assert ctx["stage"] == "feedback"
        assert ctx["topic"] == "허리디스크"
        assert ctx["length_chars"] == 2000
        assert ctx["seo_keywords"] == ["추나치료", "디스크"]
        assert ctx["blog_history_id"] == 7
        assert state.stage == Stage.DONE

    def test_skip_does_not_persist(self, monkeypatch):
        """'넘김' 입력은 저장하지 않음 (관리자 노이즈 방지)."""
        from routers import dashboard as _dashboard
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        calls = []
        monkeypatch.setattr(_dashboard, "_persist_feedback", lambda **k: calls.append(k))

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_feedback(state)

        process_turn(state, "넘김")
        assert calls == []
        assert state.stage == Stage.DONE

    def test_persist_failure_does_not_break_flow(self, monkeypatch):
        """저장 헬퍼가 예외를 내도 챗 흐름은 DONE까지 정상 진행 (fail-soft)."""
        from routers import dashboard as _dashboard
        from blog_chat_flow import process_turn
        from blog_chat_state import Stage, create_session

        def boom(**_kw):
            raise RuntimeError("DB unavailable")
        monkeypatch.setattr(_dashboard, "_persist_feedback", boom)

        state = create_session(clinic_id=1, user_id=10)
        self._advance_to_feedback(state)

        r = process_turn(state, "오류 났어요")
        assert state.stage == Stage.DONE
        assist_msgs = [m for m in r["messages"] if m["role"] == "assistant"]
        assert any("감사" in m["text"] for m in assist_msgs)
