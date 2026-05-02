"""
blog_chat_flow.py — 블로그 챗 stage 진행 로직 (Step 1, v10 plan)

책임:
  - stage별 옵션 정의 (length / seo / image / feedback)
  - 결정론적 옵션 매칭 (번호 / 정확 라벨) — 1D-1
  - 자연어 fallback: Haiku 4.5 짧은 호출 (~30 출력 토큰, ~₩0.5/턴) — 1D-2
  - process_turn(state, user_input) → 응답 dict
  - 본문 SSE는 1D-3에서 generating stage 진입 시 generator 분리
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from blog_chat_state import (
    BlogChatState,
    Stage,
    append_message,
    save_session,
    serialize_message,
    stage_text,
    transition,
)

logger = logging.getLogger(__name__)

# Haiku fallback 호출 시간 가드 — 옵션 매칭은 짧으니 5s 이내 응답 요구
_HAIKU_TIMEOUT_SECONDS = 5.0
_HAIKU_MODEL = "claude-haiku-4-5-20251001"


# ── stage별 옵션 정의 ──────────────────────────────────────────


LENGTH_OPTIONS = [
    {"id": "1500", "label": "가벼운 글 (1,500자)"},
    {"id": "2000", "label": "표준 (2,000자, 추천)"},
    {"id": "2800", "label": "상세한 글 (2,500~3,000자)"},
    {"id": "custom", "label": "직접 입력"},
]


SEO_OPTIONS = [
    {"id": "skip", "label": "자동 생성"},
]


CONFIRM_IMAGE_OPTIONS = [
    {"id": "yes", "label": "예"},
    {"id": "no", "label": "아니오"},
]


IMAGE_OPTIONS = [
    {"id": "all", "label": "전체 만들기"},
    {"id": "none", "label": "이미지 없이 종료"},
]


FEEDBACK_OPTIONS = [
    {"id": "skip", "label": "넘김"},
]


# DONE 단계 — 새 글 시작 액션 (클라가 sessionStorage clear + reload 처리)
NEW_SESSION_OPTIONS = [
    {"id": "new_session", "label": "새 글 시작하기"},
]


# ── 결정론 매칭 (번호 + 정확 라벨) ───────────────────────────


_NUM_PREFIX_RE = [
    re.compile(r"^\s*([1-9])\s*$"),
    re.compile(r"^\s*([1-9])\s*번\b"),
    re.compile(r"^\s*([1-9])\s*\."),
    re.compile(r"^\s*\(([1-9])\)"),
]

_CIRCLED_DIGIT = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9}

_KO_ORDINAL = {
    "첫번째": 1, "첫 번째": 1, "하나": 1, "1번째": 1,
    "두번째": 2, "두 번째": 2, "둘": 2, "2번째": 2,
    "세번째": 3, "세 번째": 3, "셋": 3, "3번째": 3,
    "네번째": 4, "네 번째": 4, "넷": 4, "4번째": 4,
    "다섯번째": 5, "다섯 번째": 5, "다섯": 5, "5번째": 5,
}


def _try_number(user_input: str) -> Optional[int]:
    """번호 표현을 정수로. 실패 시 None."""
    if not user_input:
        return None
    s = user_input.strip()
    if s in _CIRCLED_DIGIT:
        return _CIRCLED_DIGIT[s]
    if s in _KO_ORDINAL:
        return _KO_ORDINAL[s]
    for pat in _NUM_PREFIX_RE:
        m = pat.match(s)
        if m:
            return int(m.group(1))
    return None


def match_option(options: list[dict], user_input: str) -> Optional[dict]:
    """결정론 매칭: 번호 → 정확 라벨 → 부분 일치. 실패 시 None.

    LLM fallback은 process_turn 내부에서 호출 (network 의존이라 분리).
    """
    if not options or not user_input:
        return None
    n = _try_number(user_input)
    if n is not None and 1 <= n <= len(options):
        return options[n - 1]
    s = user_input.strip().lower()
    if not s:
        return None
    # 정확 일치
    for opt in options:
        label = (opt.get("label") or "").strip().lower()
        oid = (opt.get("id") or "").strip().lower()
        if s == label or s == oid:
            return opt
    # 부분 일치 — 라벨이 입력에 포함되거나 입력이 라벨에 포함
    for opt in options:
        label = (opt.get("label") or "").strip().lower()
        if not label:
            continue
        if s in label or label.split(" ")[0] == s:
            return opt
    return None


def match_options_multi(options: list[dict], user_input: str) -> list[dict]:
    """multi-select 결정론 매칭. "1,3" "1 3" "변증시치, 해부학" 등 입력 → 옵션 list.

    숫자가 하나라도 발견되면 숫자 모드로만 처리 (라벨 추가 안 함).
    숫자 없으면 콤마/공백 구분 후 각 토큰을 match_option로 매칭.
    중복 제거. 실패 시 빈 list (호출자가 ambiguous로 분기).
    """
    if not options or not user_input:
        return []
    s = user_input.strip()
    if not s:
        return []
    # 1) 숫자 토큰 추출 (1자리 단독만 — "13"을 "1,3"으로 오해석 방지: 옵션 13개 미만이면 그대로 13으로)
    nums = re.findall(r"\d+", s)
    if nums:
        selected: list[dict] = []
        seen_idx: set[int] = set()
        for n_str in nums:
            try:
                n = int(n_str)
            except ValueError:
                continue
            if 1 <= n <= len(options):
                idx = n - 1
                if idx not in seen_idx:
                    seen_idx.add(idx)
                    selected.append(options[idx])
        return selected
    # 2) 콤마·세미콜론·공백·한글 조사로 분할 후 라벨 매칭
    parts = re.split(r"[,，;、/\s]+|과\s|와\s|및\s", s)
    selected_labels: list[dict] = []
    seen_id: set[str] = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        opt = match_option(options, p)
        if opt:
            oid = opt.get("id") or ""
            if oid not in seen_id:
                seen_id.add(oid)
                selected_labels.append(opt)
    return selected_labels


# ── Haiku 자연어 fallback (1D-2) ──────────────────────────────


def llm_match_option(options: list[dict], user_input: str) -> Optional[dict]:
    """결정론 매칭이 None일 때만 호출되는 짧은 Haiku 매칭.

    실패는 모두 None 반환 (네트워크/파싱/키 누락 → 호출자에서 ambiguous로 분기).
    비용: 입력 ~150 토큰 + 출력 ~20 토큰 = 1턴당 ₩0.5 미만.
    """
    if not options or not user_input:
        return None
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        # anthropic SDK는 요청 단계에 import (테스트에서 monkeypatch 가능)
        import anthropic

        opt_lines = "\n".join(
            f"{i + 1}) id={o.get('id', '')} | label={o.get('label', '')}"
            for i, o in enumerate(options)
        )
        system = (
            "사용자의 한국어 입력이 다음 옵션 중 어느 것을 의미하는지 판별하세요. "
            "확실하지 않으면 'none'을 반환하세요. JSON만 출력하세요.\n\n"
            f"옵션:\n{opt_lines}\n\n"
            '응답 형식: {"matched_id": "<id>" 또는 "none", "confidence": "high|medium|low"}'
        )
        client = anthropic.Anthropic(api_key=api_key, timeout=_HAIKU_TIMEOUT_SECONDS)
        msg = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=80,
            system=system,
            messages=[{"role": "user", "content": user_input.strip()[:500]}],
        )
        text = (msg.content[0].text or "").strip()
        # 코드펜스 제거
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        matched_id = (data.get("matched_id") or "").strip()
        confidence = (data.get("confidence") or "").strip().lower()
        if matched_id == "none" or matched_id == "":
            return None
        # confidence=low는 ambiguous로 간주 (안전 fallback)
        if confidence == "low":
            return None
        for opt in options:
            if (opt.get("id") or "") == matched_id:
                return opt
    except Exception as exc:
        logger.info("Haiku fallback skipped (%s)", exc)
        return None
    return None


# ── 응답 dict 생성 ────────────────────────────────────────────


def _to_response(state: BlogChatState, latest_n: int = 2) -> dict:
    """라우트 응답 dict — 최근 N개 메시지만 (클라가 append할 분량)."""
    msgs = state.messages[-latest_n:] if latest_n > 0 else state.messages
    return {
        "session_id": state.session_id,
        "stage": state.stage.value,
        "stage_text": stage_text(state.stage),
        "messages": [serialize_message(m) for m in msgs],
        "quota": state.quota,
    }


# ── stage 메시지 빌더 ─────────────────────────────────────────


def _length_message() -> tuple[str, list[dict], dict]:
    text = (
        "글의 길이를 골라주세요.\n"
        "1) 가벼운 글 (1,500자)\n"
        "2) 표준 (2,000자, 추천)\n"
        "3) 상세한 글 (2,500~3,000자)\n"
        "4) 직접 입력"
    )
    return text, LENGTH_OPTIONS, {}


def _question_stage_message(stage: dict, answered_count: int) -> tuple[str, list[dict], dict]:
    """질문 stage 메시지 빌더 — prompt + 옵션 번호 목록 + 옵션 chip."""
    opts = stage.get("options") or []
    lines = [stage.get("prompt", "선택해주세요.")]
    for i, o in enumerate(opts):
        lines.append(f"{i + 1}) {o.get('label', '')}")
    text = "\n".join(lines)
    meta = {"question_key": stage.get("key"), "question_index": answered_count}
    return text, opts, meta


def _seo_message() -> tuple[str, list[dict], dict]:
    text = (
        "주요 키워드 1~3개를 입력해주세요.\n"
        "쉼표로 구분 (예: 추나치료, 디스크). 직접 정하기 어려우시면 [자동 생성]."
    )
    return text, SEO_OPTIONS, {}


def _emphasis_message() -> tuple[str, list[dict], dict]:
    text = (
        "원장님이 강조하고 싶은 치료 방법, 사례, 증상 등을 추가로 입력해주세요.\n"
        "(이 내용은 본문에서 강조되어 작성됩니다. 없으면 [건너뛰기])"
    )
    opts = [{"id": "skip", "label": "건너뛰기"}]
    return text, opts, {}


def _confirm_image_message() -> tuple[str, list[dict], dict]:
    text = (
        "마지막으로, 글 완성 후 이미지 5장을 연속으로 출력하시겠습니까?\n"
        "1) 예  2) 아니오"
    )
    return text, CONFIRM_IMAGE_OPTIONS, {}


def _ambiguous_message(options: list[dict]) -> tuple[str, list[dict], dict]:
    text = "잘 못 알아들었어요. 옵션을 클릭하시거나 다시 입력해주세요."
    return text, options, {"ambiguous": True}


# ── 1D-1 placeholder: 본문 생성을 임시 메시지로 대체 ─────────────


def _placeholder_generating(state: BlogChatState) -> None:
    """1D-1: generating stage placeholder. 1D-3에서 실제 generate_blog_stream 통합."""
    seo_str = ", ".join(state.seo_keywords) if state.seo_keywords else "없음"
    text = (
        f"(준비 중) 본문 생성은 다음 페이즈에서 통합됩니다.\n"
        f"  • 주제: {state.topic}\n"
        f"  • 길이: {state.length_chars}자\n"
        f"  • SEO 키워드: {seo_str}"
    )
    append_message(state, "assistant", text, options=[], meta={})


# ── process_turn — stage 분기의 진입점 ────────────────────────


def process_turn(state: BlogChatState, user_input: str) -> dict:
    """사용자 입력에 따라 stage 진행. state mutate + DB 저장 + 응답 dict 반환.

    1D-1: 결정론적 매칭만. 모호한 입력 → ambiguous 메시지.
    """
    from config_loader import load_config

    config = load_config() or {}
    # questions_enabled은 1D-2에서 활성 — 1D-1에선 항상 SEO 직진
    _ = bool((config.get("flow") or {}).get("questions_enabled", True))

    # 사용자 입력 echo
    if user_input:
        append_message(state, "user", user_input)

    s = state.stage

    # ── TOPIC ──
    if s == Stage.TOPIC:
        if not user_input:
            save_session(state)
            return _to_response(state, latest_n=1)
        state.topic = user_input.strip()[:200]
        transition(state, Stage.LENGTH)
        text, opts, meta = _length_message()
        append_message(state, "assistant", text, options=opts, meta=meta)
        save_session(state)
        return _to_response(state)

    # ── LENGTH ──
    if s == Stage.LENGTH:
        opt = match_option(LENGTH_OPTIONS, user_input)
        # 직접 숫자 입력 (예: "1800") fallback
        if opt is None:
            try:
                n = int(user_input.strip())
                if 500 <= n <= 9999:
                    state.length_chars = n
                    return _advance_to_seo(state)
            except (ValueError, AttributeError):
                pass
            # Haiku 자연어 fallback (1D-2)
            opt = llm_match_option(LENGTH_OPTIONS, user_input)
        if opt is None:
            text, opts, meta = _ambiguous_message(LENGTH_OPTIONS)
            append_message(state, "assistant", text, options=opts, meta=meta)
            save_session(state)
            return _to_response(state)
        if opt["id"] == "custom":
            text = "원하는 글자 수를 직접 입력해주세요. (500~9,999자 사이)"
            append_message(state, "assistant", text, options=[], meta={})
            save_session(state)
            return _to_response(state)
        state.length_chars = int(opt["id"])
        return _advance_to_seo(state)

    # ── QUESTIONS (옵션 카탈로그 진행) ──────────────────────────
    # blog_chat_options.BLOG_OPTION_STAGES 4개 stage를 chip으로 순차 노출.
    # 사용자 입력이 있으면 현재 stage 답변 처리 → 다음 stage 또는 SEO.
    if s == Stage.QUESTIONS:
        from blog_chat_options import get_stage, total_stages

        # multi-select stage는 같은 key로 여러 entry를 누적하므로,
        # 진행 카운트는 list 길이가 아닌 distinct key 수로 산출.
        def _answered_key_count() -> int:
            seen: set[str] = set()
            for a in state.questions_answered:
                k = a.get("key")
                if k:
                    seen.add(k)
            return len(seen)

        answered_count = _answered_key_count()

        if user_input:
            current = get_stage(answered_count)
            if current is None:
                # 모두 답한 상태에서 추가 입력 — SEO로 직진
                transition(state, Stage.SEO)
                text, opts, meta = _seo_message()
                append_message(state, "assistant", text, options=opts, meta=meta)
                save_session(state)
                return _to_response(state)

            is_multi = bool(current.get("multi"))
            if is_multi:
                opts_selected = match_options_multi(current["options"], user_input)
                # skip 단독 입력 처리: skip이 포함되면 skip만 채택 (다른 항목과 모순 방지)
                if any(o.get("id") == current.get("skip_id") for o in opts_selected):
                    opts_selected = [o for o in opts_selected if o.get("id") == current.get("skip_id")][:1]
                # 실패 시 단일 LLM fallback 시도
                if not opts_selected:
                    single = llm_match_option(current["options"], user_input)
                    if single:
                        opts_selected = [single]
                if not opts_selected:
                    text, opts, meta = _ambiguous_message(current["options"])
                    append_message(state, "assistant", text, options=opts, meta=meta)
                    save_session(state)
                    return _to_response(state)
                # 같은 key로 여러 항목 append (questions_answered list 그대로 누적)
                for opt in opts_selected:
                    state.questions_answered.append({
                        "key": current["key"],
                        "id": opt["id"],
                        "label": opt.get("label", ""),
                    })
                answered_count += 1
            else:
                opt = match_option(current["options"], user_input)
                if opt is None:
                    opt = llm_match_option(current["options"], user_input)
                if opt is None:
                    # ambiguous — 같은 stage 다시 묻기
                    text, opts, meta = _ambiguous_message(current["options"])
                    append_message(state, "assistant", text, options=opts, meta=meta)
                    save_session(state)
                    return _to_response(state)

                state.questions_answered.append({
                    "key": current["key"],
                    "id": opt["id"],
                    "label": opt.get("label", ""),
                })
                answered_count += 1

        # 다음 stage 또는 SEO 진입
        next_stage = get_stage(answered_count)
        if next_stage is None:
            transition(state, Stage.SEO)
            text, opts, meta = _seo_message()
            append_message(state, "assistant", text, options=opts, meta=meta)
            save_session(state)
            return _to_response(state)

        # 다음 stage 메시지 발송
        text, opts, meta = _question_stage_message(next_stage, answered_count)
        append_message(state, "assistant", text, options=opts, meta=meta)
        save_session(state)
        return _to_response(state)

    # ── SEO ──
    # SEO 입력은 자유 입력. 키워드 저장 후 CONFIRM_IMAGE 단계로 진입해서
    # "이미지 자동 생성" 옵션을 묻는다 (2026-05-01 추가).
    if s == Stage.SEO:
        normalized = (user_input or "").strip()
        if normalized in ("넘김", "skip", "스킵", "자동 생성", "자동생성", ""):
            state.seo_keywords = []
        else:
            kws = [k.strip() for k in normalized.split(",") if k.strip()]
            state.seo_keywords = kws[:5]
        # SEO 다음에 강조 사항 입력 단계
        transition(state, Stage.EMPHASIS)
        text, opts, meta = _emphasis_message()
        append_message(state, "assistant", text, options=opts, meta=meta)
        save_session(state)
        return _to_response(state)

    # ── EMPHASIS — 원장 강조 사항 입력 (자유 입력 또는 건너뛰기) ──
    if s == Stage.EMPHASIS:
        normalized = (user_input or "").strip()
        if normalized.lower() in ("skip", "건너뛰기", "넘김", "스킵", "없음", ""):
            state.emphasis = ""
        else:
            # 입력값 그대로 저장 (최대 500자 제한)
            state.emphasis = normalized[:500]
        transition(state, Stage.CONFIRM_IMAGE)
        text, opts, meta = _confirm_image_message()
        append_message(state, "assistant", text, options=opts, meta=meta)
        save_session(state)
        return _to_response(state)

    # ── CONFIRM_IMAGE ──
    # 사용자 응답에 따라 state.auto_image 결정. 그 다음 GENERATING SSE는
    # 라우트(main.py)가 트리거 (process_turn_streaming).
    if s == Stage.CONFIRM_IMAGE:
        opt = match_option(CONFIRM_IMAGE_OPTIONS, user_input)
        if opt is None:
            opt = llm_match_option(CONFIRM_IMAGE_OPTIONS, user_input)
        if opt is None:
            text, opts, meta = _ambiguous_message(CONFIRM_IMAGE_OPTIONS)
            append_message(state, "assistant", text, options=opts, meta=meta)
            save_session(state)
            return _to_response(state)
        state.auto_image = (opt.get("id") == "yes")
        save_session(state)
        # 라우트가 SSE 시작. 이 응답은 fallback (SSE 비활성 환경)일 때만 도달.
        transition(state, Stage.GENERATING)
        _placeholder_generating(state)
        transition(state, Stage.DONE)
        save_session(state)
        return _to_response(state)

    # ── IMAGE (1D-4 placeholder; 실제 이미지 호출은 Phase 1F) ──
    if s == Stage.IMAGE:
        opt = match_option(IMAGE_OPTIONS, user_input)
        if opt is None:
            text, opts, meta = _ambiguous_message(IMAGE_OPTIONS)
            append_message(state, "assistant", text, options=opts, meta=meta)
            save_session(state)
            return _to_response(state)
        if opt["id"] == "all":
            text = (
                "(준비 중) 이미지 5장 생성은 다음 페이즈에서 통합됩니다.\n"
                "본문 작성을 마치고 잠시 후 다시 진입해주세요."
            )
        else:
            text = "이미지 없이 종료합니다."
        append_message(state, "assistant", text, options=[], meta={})
        # 피드백 단계로 전이 — 짧게 의견 수집 후 DONE
        transition(state, Stage.FEEDBACK)
        fb_text = "오늘 사용 어떠셨어요? 불편한 점이나 개선 의견을 알려주세요. (생략하시려면 [넘김])"
        append_message(state, "assistant", fb_text, options=FEEDBACK_OPTIONS, meta={})
        save_session(state)
        return _to_response(state, latest_n=3)

    # ── FEEDBACK ──
    if s == Stage.FEEDBACK:
        normalized = (user_input or "").strip()
        if normalized in ("넘김", "skip", "스킵", "자동 생성", "자동생성", ""):
            thank_text = "감사합니다. 오늘 작업이 완료됐어요."
        else:
            # 자유 입력은 통합 피드백 저장 — 어드민 /admin/feedback에서 source=blog_chat 으로 확인
            _save_blog_chat_feedback(state, normalized)
            thank_text = "피드백 감사합니다. 더 나은 Cligent로 개선하겠습니다."
        append_message(state, "assistant", thank_text, options=[], meta={})
        transition(state, Stage.DONE)
        # DONE 안내 + [새 글 시작하기] 옵션 — 클라가 sessionStorage clear + reload
        append_message(
            state, "assistant",
            "새 글을 시작하시려면 아래 버튼을 눌러주세요.",
            options=NEW_SESSION_OPTIONS,
            meta={"new_session_action": True},
        )
        save_session(state)
        return _to_response(state, latest_n=3)

    # ── DONE ──
    # 클라는 placeholder/입력창 비활성화 상태이므로 일반적으로 도달하지 않지만,
    # 도달 시 새 글 시작 옵션을 다시 노출.
    if s == Stage.DONE:
        append_message(
            state, "assistant",
            "이번 글은 이미 완성됐어요. 새 글을 시작하시려면 아래를 눌러주세요.",
            options=NEW_SESSION_OPTIONS,
            meta={"new_session_action": True},
        )
        save_session(state)
        return _to_response(state)

    # GENERATING / IMAGE / FEEDBACK는 1D-3/1D-4에서 활성
    save_session(state)
    return _to_response(state)


# ── 피드백 통합 저장 (어드민 /admin/feedback 같은 통로) ─────────


def _save_blog_chat_feedback(state: BlogChatState, message: str) -> None:
    """FEEDBACK stage 자유 입력을 routers.dashboard._persist_feedback에 위임.

    실패는 fail-soft — 사용자 챗 흐름은 절대 중단하지 않음 (감사 메시지는 그대로).
    page="blog_chat" / context.source="blog_chat" 두 경로 모두 어드민에서 식별 가능.
    """
    try:
        from routers.dashboard import _persist_feedback  # 라우트 정의된 모듈에서 import
    except Exception:
        logger.warning("feedback persist helper unavailable; skipping chat feedback")
        return

    context = {
        "source": "blog_chat",
        "session_id": state.session_id,
        "stage": Stage.FEEDBACK.value,
        "topic": state.topic,
        "length_chars": state.length_chars,
        "seo_keywords": list(state.seo_keywords or []),
        "blog_history_id": state.blog_history_id,
    }
    try:
        _persist_feedback(
            clinic_id=state.clinic_id,
            user_id=state.user_id,
            page="blog_chat",
            message=message,
            context=context,
            user_email="",
        )
    except Exception:
        logger.exception("blog_chat feedback persist failed (fail-soft)")


# ── 내부 헬퍼 ─────────────────────────────────────────────────


def _advance_to_seo(state: BlogChatState) -> dict:
    """LENGTH 완료 후 다음 단계로 이동.

    questions_enabled (config.flow) 가 활성이고 BLOG_OPTION_STAGES가 비어있지 않으면
    QUESTIONS 진입 + 첫 옵션 stage 메시지 발송. 아니면 SEO 직진.
    """
    from blog_chat_options import BLOG_OPTION_STAGES, get_stage

    use_questions = bool(BLOG_OPTION_STAGES)
    try:
        from config_loader import load_config
        cfg = load_config() or {}
        flow = cfg.get("flow") or {}
        if "questions_enabled" in flow:
            use_questions = bool(flow.get("questions_enabled")) and bool(BLOG_OPTION_STAGES)
    except Exception:
        pass

    if use_questions:
        transition(state, Stage.QUESTIONS)
        first = get_stage(len(state.questions_answered))
        if first is None:
            # 안전망 — 카탈로그 비어 있으면 SEO 직진
            transition(state, Stage.SEO)
            text, opts, meta = _seo_message()
            append_message(state, "assistant", text, options=opts, meta=meta)
            save_session(state)
            return _to_response(state)
        text, opts, meta = _question_stage_message(first, len(state.questions_answered))
        append_message(state, "assistant", text, options=opts, meta=meta)
        save_session(state)
        return _to_response(state)

    transition(state, Stage.SEO)
    text, opts, meta = _seo_message()
    append_message(state, "assistant", text, options=opts, meta=meta)
    save_session(state)
    return _to_response(state)


# ── SSE generator (1D-3) ─────────────────────────────────────


def _sse_frame(obj: dict) -> str:
    """SSE 프레임 1건 — JSON one-line + 빈 줄."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _stream_generator_for_seo(state: BlogChatState, user_input: str):
    """SEO 입력 → 본문 streaming → IMAGE 옵션 메시지로 종료.

    주의: generator 함수. main.py 라우트가 StreamingResponse로 감싸 응답.
    프레임 type:
      user_message / message_start / token / replace / message_done /
      next_message / stage_change / stage_text / error / done
    """
    from blog_generator import generate_blog_stream
    from blog_history import save_blog_entry

    # API 키 (현재 BYOAI 비활성, env만 사용)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        yield _sse_frame({"type": "error", "message": "AI 키가 설정되지 않았습니다."})
        yield _sse_frame({"type": "done"})
        return

    # 1) CONFIRM_IMAGE 응답 echo + state.auto_image 설정 (2026-05-01)
    # SEO 키워드는 이미 SEO 단계에서 state.seo_keywords에 저장됨.
    if user_input:
        append_message(state, "user", user_input)
        yield _sse_frame({"type": "user_message",
                          "message": serialize_message(state.messages[-1])})

    opt = match_option(CONFIRM_IMAGE_OPTIONS, user_input or "")
    if opt is not None:
        state.auto_image = (opt.get("id") == "yes")

    # 2) GENERATING 진입
    transition(state, Stage.GENERATING)
    yield _sse_frame({"type": "stage_change",
                      "stage": Stage.GENERATING.value,
                      "stage_text": stage_text(Stage.GENERATING)})
    yield _sse_frame({"type": "stage_text", "text": "본문을 작성하고 있어요..."})

    # 3) streaming placeholder 메시지 추가 (active 태극 회전)
    placeholder = append_message(
        state, "assistant", "", options=[], meta={"streaming": True, "active": True}
    )
    save_session(state)
    yield _sse_frame({"type": "message_start",
                      "message": serialize_message(placeholder)})

    # 4) generate_blog_stream 호출 + chunk 통과 (chat 프레임으로 재포장)
    char_count = None
    if state.length_chars:
        char_count = {"min": state.length_chars - 200, "max": state.length_chars + 200}

    # 옵션 카탈로그 답변 → blog_generator 인자 매핑
    from blog_chat_options import to_blog_args
    # multi-select stage는 같은 key로 여러 entry 누적됨 → list로 모음.
    answers_dict: dict = {}
    for a in state.questions_answered:
        k = a.get("key")
        v = a.get("id")
        if not k or v is None:
            continue
        if k in answers_dict:
            existing = answers_dict[k]
            if isinstance(existing, list):
                existing.append(v)
            else:
                answers_dict[k] = [existing, v]
        else:
            answers_dict[k] = v
    blog_args = to_blog_args(answers_dict)

    # 강조 사항 (2026-05-02): EMPHASIS 단계 입력값을 answers dict로 전달.
    # blog_generator의 qa_text 빌더가 "원장 강조사항: ..." 형태로 본문 프롬프트에 삽입한다.
    answers_to_pass: dict = {"tone": "전문적"}
    if state.emphasis:
        answers_to_pass["원장 강조사항 (반드시 본문에서 비중 있게 다룰 것)"] = state.emphasis

    collected: list[str] = []
    cost_krw = 0
    try:
        gen = generate_blog_stream(
            keyword=state.topic,
            answers=answers_to_pass,
            api_key=api_key,
            seo_keywords=state.seo_keywords or [],
            char_count=char_count,
            mode=blog_args["mode"],
            reader_level=blog_args["reader_level"],
            explanation_types=blog_args["explanation_types"],
            format_id=blog_args["format_id"],
        )
        for chunk in gen:
            raw = chunk.removeprefix("data: ").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if "error" in data:
                yield _sse_frame({"type": "error", "message": data["error"]})
                yield _sse_frame({"type": "done"})
                return
            if "text" in data:
                collected.append(data["text"])
                yield _sse_frame({"type": "token", "text": data["text"]})
            elif "replace" in data:
                collected = [data["replace"]]
                yield _sse_frame({"type": "replace", "text": data["replace"]})
            elif "status" in data:
                yield _sse_frame({"type": "stage_text", "text": data["status"]})
            elif data.get("done"):
                cost_krw = (data.get("usage") or {}).get("cost_krw", 0)
    except Exception as exc:
        logger.exception("blog streaming failed")
        yield _sse_frame({"type": "error",
                          "message": "본문 생성 중 오류가 발생했어요. 다시 시도해주세요."})
        yield _sse_frame({"type": "done"})
        return

    blog_text = "".join(collected).strip()
    char_total = len(blog_text)

    # 5) blog_history 저장 (실패는 fail-soft)
    entry_id = None
    try:
        entry_id = save_blog_entry(
            state.topic, "전문적", char_total, cost_krw,
            state.seo_keywords or [], blog_text,
            clinic_id=state.clinic_id,
        )
    except Exception:
        logger.exception("save_blog_entry failed (chat)")

    # 6) state 갱신 + placeholder 본문 채우기
    placeholder.text = blog_text
    placeholder.options = []
    placeholder.meta = {
        "char_count": char_total,
        "cost_krw": cost_krw,
        "blog_history_id": entry_id,
    }
    state.blog_text = blog_text
    state.blog_history_id = entry_id

    yield _sse_frame({"type": "message_done",
                      "message": serialize_message(placeholder)})

    # 7) IMAGE 단계 진입.
    # auto_image=True (사용자가 자동 출력 선택) → 카운트다운 메시지 + client가 3초 후 자동 트리거
    # auto_image=False → 기존 옵션 메시지 (사용자 선택 대기)
    transition(state, Stage.IMAGE)
    if state.auto_image:
        img_text = (
            "본문이 완성됐어요. 3초 후 이미지 5장 생성을 자동으로 시작합니다.\n"
            "(5장 생성은 평균 6분 정도 소요됩니다.)\n"
            "지금 [전체 만들기]를 누르면 즉시 시작, [이미지 없이 종료]를 누르면 자동 시작이 취소됩니다."
        )
        # IMAGE_OPTIONS와 라벨 통일 — match_option 매칭 깨짐 방지 (2026-05-01)
        img_options = IMAGE_OPTIONS
        img_meta = {
            "kind": "auto_image_countdown",
            "countdown_sec": 3,
            "auto_action": "전체 만들기",  # client setTimeout이 sendTurn 인자로 사용
        }
    else:
        img_text = "본문이 완성됐어요. 이미지 5장을 만들까요?"
        img_options = IMAGE_OPTIONS
        img_meta = {}
    img_msg = append_message(state, "assistant", img_text, options=img_options, meta=img_meta)
    save_session(state)
    yield _sse_frame({"type": "next_message",
                      "message": serialize_message(img_msg)})
    yield _sse_frame({"type": "stage_change",
                      "stage": Stage.IMAGE.value,
                      "stage_text": stage_text(Stage.IMAGE)})
    yield _sse_frame({"type": "done",
                      "blog_history_id": entry_id, "char_count": char_total})


def process_turn_streaming(state: BlogChatState, user_input: str):
    """라우트에서 호출하는 SSE 진입점.

    CONFIRM_IMAGE 응답 → 본문 streaming (1D-3 + auto_image 분기).
    IMAGE 진입 + "all" 매칭 → 이미지 5단계 텍스트 SSE (1F).
    """
    if state.stage == Stage.CONFIRM_IMAGE:
        return _stream_generator_for_seo(state, user_input)
    if state.stage == Stage.IMAGE:
        return _stream_generator_for_image(state, user_input)
    # 안전 fallback — 다른 stage에서 잘못 호출되면 단일 error frame
    def _err():
        yield _sse_frame({"type": "error",
                          "message": "스트리밍이 적용되지 않는 단계입니다."})
        yield _sse_frame({"type": "done"})
    return _err()


# ── 이미지 단계 SSE (1F, M0 게이트) ────────────────────────────


# v10 plan E3 — 5단계 텍스트 발송 누적 시간(초). 실제 이미지 호출 60~120s에 맞춤.
# env BLOG_CHAT_IMAGE_DELAYS로 조절 (테스트는 "0,0,0,0,0").
_DEFAULT_IMAGE_DELAYS_SEC = [5, 5, 10, 20, 20]
# v10 plan E3 — partial_images 게이트. M0=0(OFF), M1+=3.
# env BLOG_CHAT_IMAGE_PARTIAL_FRAMES (기본 0).


def _image_delays() -> list[float]:
    raw = os.getenv("BLOG_CHAT_IMAGE_DELAYS", "")
    if not raw:
        return list(_DEFAULT_IMAGE_DELAYS_SEC)
    try:
        return [float(x.strip()) for x in raw.split(",")][:5]
    except (ValueError, AttributeError):
        return list(_DEFAULT_IMAGE_DELAYS_SEC)


def image_partial_frames() -> int:
    """M0=0 (OFF) / M1+=3 (partial_images=3). cohort wave 진입 시 env 갱신."""
    raw = os.getenv("BLOG_CHAT_IMAGE_PARTIAL_FRAMES", "0").strip()
    try:
        n = int(raw)
        return n if 0 <= n <= 5 else 0
    except (ValueError, TypeError):
        return 0


_IMAGE_STAGE_TEXTS = [
    "본문을 분석하고 있어요...",
    "5장의 컨셉을 정리하는 중...",
    "이미지 세션을 준비하는 중... (전체 약 6분 소요, 5장 기준)",
    # [3], [4]는 동적 'N/5' 메시지로 대체 (2026-05-01)
]


# ── 이미지 생성 취소 (2026-05-02) ────────────────────────────────
# 단일 worker 메모리 set으로 cancel flag 관리.
# 멀티 worker 환경 시 DB 컬럼으로 이전 필요.
_CANCELLED_IMAGE_SESSIONS: set[str] = set()


def cancel_image_session(session_id: str) -> bool:
    """이미지 생성 중단을 마크. 다음 루프 이터에서 break."""
    if not session_id:
        return False
    _CANCELLED_IMAGE_SESSIONS.add(session_id)
    return True


def is_image_session_cancelled(session_id: str) -> bool:
    return bool(session_id) and session_id in _CANCELLED_IMAGE_SESSIONS


def _clear_cancel_flag(session_id: str) -> None:
    """완료·실패 시 set에서 제거 (메모리 누수 방지)."""
    _CANCELLED_IMAGE_SESSIONS.discard(session_id)


def _stream_generator_for_image(state: BlogChatState, user_input: str):
    """IMAGE stage '전체 만들기' → 프롬프트 자동 추출 → image2 호출 → 갤러리 메시지.

    흐름:
      1. user_input echo + 옵션 매칭
      2. Stage 1+2: image_prompt_generator 로 5 프롬프트 추출 (Anthropic)
      3. plan 조회 + image_session 발급
      4. generate_initial_set(첫 프롬프트, n=5) — gpt-image-2 한 번 호출
      5. 갤러리 메시지 (meta.images = [b64...], meta.image_session_id, meta.quota)
      6. FEEDBACK stage 전이

    M0: partial_images OFF (image_partial_frames()==0). M1+에서 env 토글.
    실패는 friendly error 프레임 + done. state는 IMAGE stage 유지 (재시도 가능 자리).
    """
    # 사용자 메시지 echo
    if user_input:
        append_message(state, "user", user_input)
        yield _sse_frame({"type": "user_message",
                          "message": serialize_message(state.messages[-1])})

    opt = match_option(IMAGE_OPTIONS, user_input)
    if opt is None or opt.get("id") != "all":
        yield _sse_frame({"type": "error",
                          "message": "이미지 시작 옵션이 잘못 인식됐어요."})
        yield _sse_frame({"type": "done"})
        return

    save_session(state)
    yield _sse_frame({"type": "stage_change",
                      "stage": Stage.IMAGE.value,
                      "stage_text": stage_text(Stage.IMAGE)})

    # ── 1. 프롬프트 추출 (Anthropic) ─────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        yield _sse_frame({"type": "error", "message": "AI 키가 설정되지 않았습니다."})
        yield _sse_frame({"type": "done"})
        return
    if not state.blog_text:
        yield _sse_frame({"type": "error", "message": "본문이 없어 이미지 프롬프트를 만들 수 없어요."})
        yield _sse_frame({"type": "done"})
        return

    yield _sse_frame({"type": "stage_text", "text": _IMAGE_STAGE_TEXTS[0]})

    prompts: list[str] = []
    try:
        from image_prompt_generator import generate_image_prompts_stream
        gen = generate_image_prompts_stream(
            keyword=state.topic,
            blog_content=state.blog_text,
            api_key=api_key,
            style="photorealistic",
            tone="cool_white",
        )
        for chunk in gen:
            raw = chunk.removeprefix("data: ").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if "error" in data:
                yield _sse_frame({"type": "error", "message": data["error"]})
                yield _sse_frame({"type": "done"})
                return
            if data.get("status") == "generating":
                yield _sse_frame({"type": "stage_text", "text": _IMAGE_STAGE_TEXTS[1]})
            if data.get("done"):
                prompts = list(data.get("prompts") or [])
    except Exception:
        logger.exception("image prompt generation failed")
        yield _sse_frame({"type": "error", "message": "이미지 프롬프트 생성에 실패했어요."})
        yield _sse_frame({"type": "done"})
        return

    if not prompts:
        yield _sse_frame({"type": "error", "message": "이미지 프롬프트가 비어 있어요. 다시 시도해주세요."})
        yield _sse_frame({"type": "done"})
        return

    # 5장 모두 다른 모듈 prompt — 각 1장씩 5번 호출 (2026-05-01 결정)
    # gpt-image-2는 별도 negative_prompt 인자가 없음 — Stage 2가 출력한
    # negative_prompt 필드를 본문 끝에 합쳐야 실제 효과. env IMAGE_INJECT_NEGATIVES=0으로 끔.
    inject_negatives = os.getenv("IMAGE_INJECT_NEGATIVES", "1").strip() != "0"
    prompt_list: list[str] = []
    title_list: list[str] = []
    for p in prompts[:5]:
        if isinstance(p, str):
            prompt_list.append(p)
            title_list.append("")
        else:
            body = p.get("prompt") or ""
            neg = (p.get("negative_prompt") or "").strip()
            if inject_negatives and neg:
                body = body.rstrip() + f"\n\nNegative aspects to avoid: {neg}"
            prompt_list.append(body)
            title_list.append((p.get("title_ko") or "").strip())
    if len(prompt_list) < 5:
        yield _sse_frame({"type": "error",
                          "message": "이미지 프롬프트가 5개 미만이에요. 다시 시도해주세요."})
        yield _sse_frame({"type": "done"})
        return
    if any(not (isinstance(p, str) and p.strip()) for p in prompt_list):
        yield _sse_frame({"type": "error", "message": "이미지 프롬프트가 비어 있어요."})
        yield _sse_frame({"type": "done"})
        return
    # 호환 — primary_prompt는 카드 0번 prompt로 유지 (구버전 클라이언트 안전망)
    primary_prompt = prompt_list[0]

    # ── 2. plan 조회 + image_session 발급 ───────────────────
    yield _sse_frame({"type": "stage_text", "text": _IMAGE_STAGE_TEXTS[2]})

    try:
        from plan_guard import get_effective_plan
        from image_generator import (
            generate_initial_set, get_quota_status,
            ImageQuotaExceeded as _IGQuota,  # noqa: F401
        )
        from image_session_manager import create_session as _create_image_session
        from ai_client import AIClientError as _AIClientError
    except Exception:
        logger.exception("image module import failed")
        yield _sse_frame({"type": "error", "message": "이미지 모듈을 불러오지 못했어요."})
        yield _sse_frame({"type": "done"})
        return

    plan = get_effective_plan(state.clinic_id) or {}
    plan_id = plan.get("plan_id", "free")

    image_session_id = None
    try:
        image_session_id = _create_image_session(
            clinic_id=state.clinic_id,
            user_id=state.user_id,
            blog_keyword=state.topic or "",
            plan_id_at_start=plan_id,
        )
        state.image_session_id = image_session_id
        save_session(state)
    except Exception:
        logger.exception("image_session create failed")
        yield _sse_frame({"type": "error", "message": "이미지 세션을 만들지 못했어요."})
        yield _sse_frame({"type": "done"})
        return

    # pending cancel (image_session 생성 전 클라가 취소 누른 경우) 처리
    pending_key = f"pending:{state.session_id}"
    if is_image_session_cancelled(pending_key):
        _clear_cancel_flag(pending_key)
        cancel_image_session(image_session_id)  # 즉시 취소 마크
        yield _sse_frame({"type": "image_cancelled",
                          "message": "이미지 생성이 취소됐어요."})
        yield _sse_frame({"type": "done"})
        return

    # ── 3. gpt-image-2 호출 (5번, 사이사이 진행 안내) ───────
    # 실측: 1장당 평균 ~60초 (조직 인증 직후·고해상도). 사용자가 다른 일 가능하도록
    # 예상 시간 + 모듈 제목 노출. 분 단위로 자연스럽게 표시.
    # 클라이언트가 취소 버튼을 만들 수 있도록 image_session_id 사전 통지 (backup, primary는 stage_change)
    yield _sse_frame({"type": "image_session_started",
                      "image_session_id": image_session_id})

    SECONDS_PER_IMAGE = 60
    try:
        from image_generator import get_plan_dimensions
        from ai_client import call_openai_image_generate
        size, quality = get_plan_dimensions(plan_id)
        images: list[str] = []
        for idx, p in enumerate(prompt_list):
            # 매 루프 시작 시 cancel flag 체크 (2026-05-02)
            if is_image_session_cancelled(image_session_id):
                _clear_cancel_flag(image_session_id)
                yield _sse_frame({"type": "image_cancelled",
                                  "message": f"{idx}/5장에서 사용자가 취소했어요."})
                yield _sse_frame({"type": "done"})
                return
            remaining_sec = (5 - idx) * SECONDS_PER_IMAGE
            if remaining_sec >= 60:
                remaining_label = f"약 {remaining_sec // 60}분 남음"
            else:
                remaining_label = f"약 {remaining_sec}초 남음"
            title = title_list[idx] or f"{idx + 1}번 장면"
            stage_msg = (
                f"이미지 {idx + 1}/5 — {title} 그리는 중... "
                f"({remaining_label})"
            )
            yield _sse_frame({"type": "stage_text", "text": stage_msg})
            responses = call_openai_image_generate(
                prompt=p, size=size, quality=quality, n=1,
            )
            if not responses:
                _clear_cancel_flag(image_session_id)
                yield _sse_frame({"type": "error",
                                  "message": "OpenAI에서 이미지를 받지 못했어요."})
                yield _sse_frame({"type": "done"})
                return
            images.append(responses[0].content)
            # OpenAI 응답 후에도 한 번 더 체크 — 마지막 장 직전에 취소되었을 때
            if is_image_session_cancelled(image_session_id) and idx < len(prompt_list) - 1:
                _clear_cancel_flag(image_session_id)
                yield _sse_frame({"type": "image_cancelled",
                                  "message": f"{idx + 1}/5장 완료 후 취소됐어요. 완료된 장은 폐기됩니다."})
                yield _sse_frame({"type": "done"})
                return
        # 정상 완료 — flag 정리
        _clear_cancel_flag(image_session_id)

        # ImageSet 호환 객체 (gallery_meta가 result.* 필드 사용)
        from image_generator import ImageSet, normalize_plan_id
        result = ImageSet(
            images=images,
            plan_id=normalize_plan_id(plan_id),
            size=size,
            quality=quality,
            mode="initial",
        )
    except _AIClientError as exc:
        logger.warning("image generation failed (%s)", exc)
        yield _sse_frame({"type": "error",
                          "message": f"이미지 생성에 실패했어요: {getattr(exc, 'message', str(exc))}"})
        yield _sse_frame({"type": "done"})
        return
    except Exception:
        logger.exception("image generation crashed")
        yield _sse_frame({"type": "error", "message": "이미지 생성 중 오류가 발생했어요."})
        yield _sse_frame({"type": "done"})
        return

    quota = get_quota_status(plan_id, regen_used=0, edit_used=0)

    # ── 4. 갤러리 메시지 (b64 5장 + meta) ───────────────────
    yield _sse_frame({"type": "stage_text", "text": "5장 완성됐어요."})

    gallery_meta = {
        "kind": "image_gallery",
        "image_session_id": image_session_id,
        "plan_id": result.plan_id,
        "size": result.size,
        "quality": result.quality,
        "images": result.images,           # b64 5장
        "prompts": prompt_list,            # 카드별 [↺] 재생성용 (각 카드의 모듈 prompt)
        "primary_prompt": primary_prompt,  # 호환성 — 구버전 클라이언트 fallback
        "quota": quota,
        "partial_frames": image_partial_frames(),
        "filename_base": (state.topic or "image").strip(),
    }
    gallery_msg = append_message(
        state, "assistant",
        "이미지 5장이 준비됐어요. 마음에 드는 장은 다운로드, 아쉬운 장은 [수정]·[재생성] 해보세요.",
        options=[],
        meta=gallery_meta,
    )
    save_session(state)
    yield _sse_frame({"type": "next_message",
                      "message": serialize_message(gallery_msg)})

    # ── 4-b. 완료 안내 + 3 버튼 (본문 복사·전체 다운로드·발행 확인) ──
    completion_text = (
        "블로그 글과 이미지가 모두 출력되었습니다. "
        "이미지를 확인해보시고 만족스럽지 못한 이미지는 수정 또는 재생성 해주세요. "
        "수정이 재생성보다 이미지를 빨리 만듭니다."
    )
    completion_meta = {
        "kind": "completion_summary",
        "blog_history_id": getattr(state, "blog_history_id", None),
        "blog_text": getattr(state, "blog_text", "") or "",
        "filename_base": (state.topic or "image").strip(),
        "image_count": len(images),
    }
    completion_msg = append_message(
        state, "assistant", completion_text,
        options=[], meta=completion_meta,
    )
    save_session(state)
    yield _sse_frame({"type": "next_message",
                      "message": serialize_message(completion_msg)})

    # ── 5. FEEDBACK stage 전이 + 옵션 메시지 ────────────────
    transition(state, Stage.FEEDBACK)
    fb_text = (
        "오늘 사용 어떠셨어요? 불편한 점이나 개선 의견을 알려주세요. (생략하시려면 [넘김])"
    )
    fb_msg = append_message(state, "assistant", fb_text,
                            options=FEEDBACK_OPTIONS, meta={})
    save_session(state)
    yield _sse_frame({"type": "next_message",
                      "message": serialize_message(fb_msg)})
    yield _sse_frame({"type": "stage_change",
                      "stage": Stage.FEEDBACK.value,
                      "stage_text": stage_text(Stage.FEEDBACK)})
    yield _sse_frame({"type": "done",
                      "image_session_id": image_session_id,
                      "quota": quota})
