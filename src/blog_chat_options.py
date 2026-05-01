"""
blog_chat_options.py — 블로그 작성 옵션 카탈로그 (chat-driven UX, 확장성 ↑)

각 stage = 사용자에게 chip으로 묻는 1개 옵션 카테고리.
새 옵션 추가 = BLOG_OPTION_STAGES에 dict 1개 append. chat 흐름 코드는 무변경.

stage dict 키:
  - key:        state.questions_answered 에 저장될 식별자 (blog_generator 매핑용)
  - prompt:     어시스턴트 메시지 텍스트
  - options:    [{id, label}] — id가 generate_blog_stream 인자로 변환
  - skip_id:    이 ID는 "건너뛰기/자동" 의미 (선택). to_blog_args 가 처리.

매핑 함수 to_blog_args:
  questions_answered 누적 → generate_blog_stream(mode/reader_level/explanation_types/format_id) 인자.
"""

from __future__ import annotations

from typing import Optional


BLOG_OPTION_STAGES: list[dict] = [
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
        "prompt": "어떤 관점으로 설명할까요? (여러 개 선택 가능 — 쉼표로 구분, 예: 1,3)",
        "options": [
            {"id": "변증시치", "label": "변증시치"},
            {"id": "체질의학", "label": "체질의학(사상체질)"},
            {"id": "해부학", "label": "해부학"},
            {"id": "내분비", "label": "내분비"},
            {"id": "신경학", "label": "신경학"},
            {"id": "기타 서양의학", "label": "기타 서양의학"},
            {"id": "skip", "label": "건너뛰기"},
        ],
        "skip_id": "skip",
        "multi": True,
    },
    {
        "key": "format_id",
        "prompt": "글 형식을 골라주세요.",
        "options": [
            {"id": "auto", "label": "자동"},
            {"id": "information", "label": "정보형"},
            {"id": "case_study", "label": "사례형"},
            {"id": "qna", "label": "Q&A"},
            {"id": "comparison", "label": "비교형"},
            {"id": "seasonal", "label": "계절형"},
            {"id": "lifestyle", "label": "생활습관형"},
        ],
        "skip_id": "auto",
    },
]


def get_stage(index: int) -> Optional[dict]:
    """0-based index의 stage. 범위 밖이면 None (모든 질문 완료 신호)."""
    if 0 <= index < len(BLOG_OPTION_STAGES):
        return BLOG_OPTION_STAGES[index]
    return None


def total_stages() -> int:
    return len(BLOG_OPTION_STAGES)


def to_blog_args(answers: dict) -> dict:
    """
    questions_answered 누적 dict → generate_blog_stream 인자 매핑.

    Args:
        answers: {key: option_id}.
            예) {"mode": "정보", "reader_level": "일반인",
                 "explanation_type": "변증시치", "format_id": "auto"}.

    Returns:
        {mode, reader_level, explanation_types, format_id}.
        skip 처리: explanation_type==skip → explanation_types=None
                   format_id==auto       → format_id=None  (blog_generator default 사용)

    누락된 키는 보수적 default ("정보" / "일반인" / None / None).

    explanation_type은 multi-select 지원: value가 list거나 str 모두 허용.
    "skip"은 무시. 나머지는 그대로 list로 정규화.
    """
    def _as_str(v) -> str:
        if isinstance(v, list):
            return (v[-1] if v else "") or ""
        return (v or "").strip() if isinstance(v, str) else ""

    mode = _as_str(answers.get("mode")) or "정보"
    reader_level = _as_str(answers.get("reader_level")) or "일반인"

    expl_raw = answers.get("explanation_type")
    expl_list: list[str] = []
    if isinstance(expl_raw, list):
        expl_list = [s.strip() for s in expl_raw if isinstance(s, str) and s.strip()]
    elif isinstance(expl_raw, str) and expl_raw.strip():
        expl_list = [expl_raw.strip()]
    expl_list = [e for e in expl_list if e and e != "skip"]
    # 사상체질·변증시치 상호 배타 가드 (blog_generator도 가드하지만 이중 방어)
    explanation_types: Optional[list[str]] = expl_list or None

    fmt = _as_str(answers.get("format_id"))
    format_id: Optional[str] = None
    if fmt and fmt != "auto":
        format_id = fmt

    return {
        "mode": mode,
        "reader_level": reader_level,
        "explanation_types": explanation_types,
        "format_id": format_id,
    }
