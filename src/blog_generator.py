"""
blog_generator.py — 주제와 Q&A 답변을 받아 블로그를 스트리밍으로 생성합니다.
FastAPI의 StreamingResponse와 연결되어 실시간으로 텍스트를 전달합니다.
"""
import json
from pathlib import Path
from typing import Generator, List, Optional
import anthropic
from config_loader import load_config, load_prompt
from pattern_selector import select_patterns
from blog_history import get_recent_posts
from format_selector import select_format, load_format_template
from hook_selector import select_hook, get_hook_instruction
from citation_provider import build_citation_block

# claude-sonnet-4-6 가격 (2025년 기준, 달러 기준)
PRICE_INPUT_PER_M = 3.0    # $3 / 1M 입력 토큰
PRICE_OUTPUT_PER_M = 15.0  # $15 / 1M 출력 토큰
KRW_PER_USD = 1400         # 환율 (대략적인 참고값)

# v0.3 충돌 방지 — hook과 format 간 불호환 쌍
# key: format_id, value: 이 format과 함께 사용하면 안 되는 hook id 집합
_HOOK_FORMAT_INCOMPATIBLE: dict[str, set] = {
    "case_study": {"classic_quote", "case"},  # 서론 형식 충돌 (고전인용 / 환자스토리 오프닝)
    "qna":        {"classic_quote"},           # Q&A 형식은 질문으로 시작해야 함
}

# v0.3 충돌 방지 — format과 pattern_selector body 패턴 간 중복 쌍
# key: format_id, value: 제외할 BODY 패턴 ID 집합
_FORMAT_BODY_EXCLUDE: dict[str, set] = {
    "qna":        {"Q&A_자주묻는질문형"},
    "case_study": {"케이스_스터디형"},
    "comparison": {"서양_한의학_비교형"},
}


_MODE_INSTRUCTIONS = {
    "광고": (
        "## 글 목적 — 광고 모드\n"
        "- 내원 유도를 명확하게: 마무리 단락에서 '지금 예약하세요', '상담 문의 주세요' 등 CTA 1회 포함\n"
        "- 치료 성과를 구체적으로 묘사 (단, 의료법 준수 — 보장·완치 표현 금지)\n"
        "- 한의원 고유 장점(경험, 접근법)을 자연스럽게 어필\n"
        "- 독자가 '이 한의원에 가고 싶다'는 생각이 들도록 신뢰감 형성에 집중"
    ),
    "정보": (
        "## 글 목적 — 정보 모드\n"
        "- 순수 정보 제공 중심: 내원 유도 표현 최소화 (마무리에 1문장 이하)\n"
        "- 독자가 스스로 판단할 수 있도록 균형 잡힌 시각 제공\n"
        "- 출처와 근거를 강조하여 신뢰도 있는 건강 정보지 스타일로 작성\n"
        "- 한의학 외 서양의학적 관점도 간략히 병기하여 포괄적 정보 제공"
    ),
}

_READER_LEVEL_INSTRUCTIONS = {
    "일반인": (
        "## 독자 수준 — 일반인\n"
        "- 한의학 전문 용어 사용 후 반드시 쉬운 말로 바로 풀이 (괄호 또는 다음 문장)\n"
        "- 비유와 생활 예시를 적극 활용 (예: '소화가 막힌 느낌 = 기체(氣滯)')\n"
        "- 문장 길이를 짧게 유지, 전문 개념은 1개씩 천천히 소개\n"
        "- '어렵지 않아요', '쉽게 말하면' 같은 친근한 연결 표현 사용"
    ),
    "건강관심": (
        "## 독자 수준 — 건강 관심층\n"
        "- 건강 상식과 생활 습관에 관심 있는 독자 대상\n"
        "- 증상·원인·예방법 위주로 실용적 정보 구성\n"
        "- 전문 용어는 사용하되 간단한 풀이 병기 (자세한 설명은 생략 가능)\n"
        "- 집에서 실천 가능한 관리법을 구체적으로 2~3가지 제시"
    ),
    "한의학관심": (
        "## 독자 수준 — 한의학 관심층\n"
        "- 한의학 개념에 어느 정도 익숙한 독자 대상\n"
        "- 변증(辨證), 경락(經絡), 오장육부(五臟六腑) 등 전문 용어 적극 활용 가능\n"
        "- 치료 원리와 처방 논리를 심층적으로 설명\n"
        "- 국제 학술 용어(TKM, RCT 등) 및 연구 근거를 상세히 인용"
    ),
}


def _fix_keyword_counts(text: str, seo_keywords: List[str], target_min: int = 6) -> str:
    """스트리밍 완료 후 키워드 횟수가 부족하면 코드로 직접 보강합니다."""
    # 관련 글 / 태그 섹션 이후는 수정 대상에서 제외
    tail = ""
    for marker in ["---\n**관련 글", "\n**관련 글", "\n관련 글", "\n태그:", "\n## 관련 시리즈 주제 제안", "\n---\n## 관련 시리즈"]:
        idx = text.find(marker)
        if idx != -1:
            tail = text[idx:]
            text = text[:idx]
            break

    for kw in seo_keywords:
        if text.count(kw) >= target_min:
            continue

        needed = target_min - text.count(kw)
        parts = kw.split()

        # 복합 키워드: suffix만 단독으로 쓰인 곳에 prefix 추가
        # 단, 마크다운 링크 [텍스트](url) 내부는 건드리지 않음
        if len(parts) >= 2 and needed > 0:
            suffix = parts[-1]
            prefix_space = " ".join(parts[:-1]) + " "
            pos = 0
            replaced = 0
            chunks: list[str] = []

            while pos < len(text) and replaced < needed:
                idx = text.find(suffix, pos)
                if idx == -1:
                    break

                # 마크다운 링크 내부 여부 확인: idx 직전의 마지막 [ vs ] 위치 비교
                open_bracket = text.rfind("[", 0, idx)
                close_bracket = text.rfind("]", 0, idx)
                inside_link = open_bracket > close_bracket

                already_full = (
                    idx >= len(prefix_space)
                    and text[idx - len(prefix_space):idx] == prefix_space
                )

                if not already_full and not inside_link:
                    chunks.append(text[pos:idx])
                    chunks.append(kw)
                    pos = idx + len(suffix)
                    replaced += 1
                else:
                    chunks.append(text[pos:idx + len(suffix)])
                    pos = idx + len(suffix)

            chunks.append(text[pos:])
            text = "".join(chunks)

        # 여전히 부족하면 소제목(##) 바로 뒤 줄에 삽입 — 키워드가 문장 안에 자연스럽게 녹아드는 패턴
        _inject_templates = [
            "{kw}로 고생하시는 분들은 어떻게 치료하면 좋을지 고민이 있으실 겁니다.",
            "{kw}는 올바른 치료 시기를 놓치지 않는 것이 중요합니다.",
            "{kw}로 인한 증상, 원인부터 정확히 파악하는 것이 첫걸음입니다.",
            "{kw} 치료, 한의학적으로 어떻게 접근할 수 있을까요?",
            "{kw}로 고민 중이시라면 아래 내용이 도움이 될 수 있습니다.",
            "{kw}의 증상과 치료 방법을 함께 살펴보겠습니다.",
        ]
        still_needed = target_min - text.count(kw)
        if still_needed > 0:
            lines = text.split("\n")
            new_lines: list[str] = []
            added = 0
            for line in lines:
                new_lines.append(line)
                if added < still_needed and line.startswith("## ") and kw not in line \
                        and not any(w in line for w in ("참고", "미주", "출처", "References")):
                    tmpl = _inject_templates[added % len(_inject_templates)]
                    new_lines.append(tmpl.format(kw=kw))
                    added += 1
            text = "\n".join(new_lines)

    return text + tail


def extract_faq_schema(blog_text: str, keyword: str) -> Optional[dict]:
    """
    블로그 본문의 Q: / A: 패턴을 파싱해 FAQPage JSON-LD 딕셔너리를 반환합니다.
    네이버·구글 리치 결과(검색 결과 내 FAQ 펼침) 노출용.
    Q&A가 없으면 None 반환.
    """
    import re
    # "Q:" 또는 "Q. " 두 형식 모두 허용, A: 또는 A. 도 동일하게 처리
    pairs = re.findall(
        r'Q[:.]\s*(.+?)\s*A[:.]\s*(.+?)(?=Q[:.]\s*|$)',
        blog_text,
        re.DOTALL,
    )
    if not pairs:
        return None

    entities = []
    for q, a in pairs[:5]:  # 최대 5개만
        q_clean = q.strip().replace('\n', ' ')
        a_clean = a.strip().replace('\n', ' ')[:300]
        if len(q_clean) < 5 or len(a_clean) < 5:
            continue
        entities.append({
            "@type": "Question",
            "name": q_clean,
            "acceptedAnswer": {"@type": "Answer", "text": a_clean},
        })

    if not entities:
        return None

    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": entities,
    }


def generate_series_suggestions(keyword: str, blog_text: str, api_key: str) -> list[str]:
    """블로그 완성 후 연관 시리즈 주제 3개 추천"""
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f"한의원 블로그 주제 '{keyword}'로 글을 작성했습니다.\n"
        "독자가 다음에 읽으면 좋을 연관 블로그 주제 3개를 추천해주세요.\n"
        "조건: 한의원 블로그에 적합하고, 검색 유입이 기대되는 구체적인 주제.\n"
        "형식: 번호 없이 주제만 한 줄씩, 총 3줄."
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        lines = [
            l.strip() for l in resp.content[0].text.strip().splitlines()
            if l.strip() and not l.strip().startswith('#')
        ]
        return lines[:3]
    except Exception:
        return []


def _build_seo_keywords_section(seo_keywords: List[str]) -> str:
    """SEO 키워드 프롬프트 블록 생성"""
    if not seo_keywords:
        return ""
    tag_line = " ".join("#" + k for k in seo_keywords)
    kw_blocks = []
    for kw in seo_keywords:
        kw_blocks.append(
            f"  - 키워드: '{kw}'\n"
            f"    본문에 6~8회 자연스럽게 분산 삽입. 키워드가 문장 안에 녹아들어야 하며, 매번 다른 문맥과 구조 사용.\n"
            f"    같은 문장 패턴 반복 금지.\n"
            f"    예시:\n"
            f"    · '{kw}로 고생하시는 분들은 어떻게 치료하면 좋을지 고민이 있으실 겁니다.'\n"
            f"    · '{kw}는 추나 치료로 교정을 받으면 한결 나아지실 수 있습니다.'\n"
            f"    · '{kw}로 인한 통증은 그 원인부터 파악하는 것이 중요합니다.'\n"
            f"    · '{kw} 치료, 한의학에서는 어떻게 접근할까요?'"
        )
    kw_lines = "\n".join(kw_blocks)
    return (
        f"\n## 🔴 지정 SEO 키워드 — CRITICAL\n"
        f"아래 키워드를 분리하지 말고 각각 6~8회 삽입하세요.\n"
        f"{kw_lines}\n"
        f"- 제목(#), 소제목(##), 각 섹션 첫 문장에 우선 배치하세요.\n"
        f"- 글 맨 끝에 다음 한 줄을 추가하세요: 태그: {tag_line}"
    )


_EXPLANATION_DESCRIPTIONS = {
    "변증시치": (
        "변증시치(辨證施治, pattern identification and treatment): "
        "환자의 증상을 한의학적 변증(辨證)으로 분류하고, 그에 따른 치법(治法)과 처방을 상세히 설명하세요. "
        "기허(氣虛), 음허(陰虛), 어혈(瘀血), 담음(痰飮) 등 변증 유형과 대응 처방을 구체적으로 기술합니다."
    ),
    "사상체질": (
        "사상체질(四象體質, Sasang constitutional medicine): "
        "태양인·태음인·소양인·소음인 체질 분류에 따른 원인과 치료 접근을 설명하세요. "
        "체질별 특성, 적합한 치료법, 주의사항을 구체적으로 기술합니다."
    ),
    "해부학": (
        "해부학적 관점(Anatomy): "
        "관련 근육·인대·신경·뼈 구조 등 해부학적 구조물을 중심으로 원인과 기전을 설명하세요. "
        "구체적인 해부학 명칭과 위치를 포함하여 독자가 이해하기 쉽게 풀어 기술합니다."
    ),
    "내분비": (
        "내분비학적 관점(Endocrinology): "
        "호르몬 불균형, 대사 이상 등 내분비계 기전을 중심으로 원인을 설명하세요. "
        "관련 호르몬(코르티솔, 인슐린, 갑상선 호르몬 등)과 그 영향을 구체적으로 기술합니다."
    ),
    "신경학": (
        "신경학적 관점(Neurology): "
        "중추신경·말초신경·자율신경계 등 신경계 기전을 중심으로 원인과 증상 발생 과정을 설명하세요. "
        "신경전달물질, 신경 경로, 관련 신경 이름을 포함하여 구체적으로 기술합니다."
    ),
    "기타 서양의학": (
        "서양의학적 관점(Western Medicine): "
        "현대 의학의 병태생리(pathophysiology) 중심으로 원인과 기전을 설명하세요. "
        "관련 연구 근거, 진단 기준, 치료 원칙을 포함하여 기술합니다."
    ),
}


def _build_explanation_section(explanation_types: Optional[List[str]]) -> str:
    """선택된 설명 방식으로 프롬프트 블록 생성. 미선택 항목은 글에 포함하지 않음."""
    if not explanation_types:
        # 아무것도 선택하지 않으면 일반 설명(제약 없음)
        return ""

    lines = ["## 설명 방식 지침 (CRITICAL — 반드시 준수)"]
    lines.append("원인 설명 섹션은 아래에 지정된 관점으로만 작성하세요.")
    lines.append("지정되지 않은 관점(예: 선택하지 않은 변증시치, 사상체질 등)은 절대 포함하지 마세요.\n")
    lines.append("**선택된 설명 방식:**")

    custom_items = []
    for item in explanation_types:
        if item in _EXPLANATION_DESCRIPTIONS:
            lines.append(f"- {_EXPLANATION_DESCRIPTIONS[item]}")
        else:
            # 기타 직접 입력
            custom_items.append(item)

    if custom_items:
        lines.append(f"- 기타 관점: {', '.join(custom_items)} — 이 관점을 중심으로 원인을 설명하세요.")

    lines.append("\n**미선택 관점 생략 규칙 (절대 준수):**")
    all_keys = list(_EXPLANATION_DESCRIPTIONS.keys())
    omitted = [k for k in all_keys if k not in explanation_types]
    if omitted:
        lines.append(f"다음 관점은 선택되지 않았으므로 원인 설명 섹션에 포함하지 마세요: {', '.join(omitted)}")

    return "\n".join(lines)


def _get_current_season_note() -> str:
    """현재 월 기준 한국 계절 정보 문자열 반환."""
    from datetime import datetime, timezone
    month = datetime.now(timezone.utc).month
    if month in (3, 4, 5):
        season, period = "봄", "3~5월"
    elif month in (6, 7, 8):
        season, period = "여름", "6~8월"
    elif month in (9, 10, 11):
        season, period = "가을", "9~11월"
    else:
        season, period = "겨울", "12~2월"
    return f"현재는 {season}({period})입니다. 계절 관련 내용은 이 계절에 맞게 작성하세요."


def _build_diversity_section(
    keyword: str,
    diversity_config: dict,
    format_id: Optional[str] = None,
    mode: str = "정보",
    char_count: Optional[dict] = None,
) -> dict:
    """
    v0.3 다양성 레이어 — format/hook/citation 선택 및 프롬프트 블록 반환.
    충돌 방지 로직 포함:
      - lifestyle + 짧은 글자 수 → information으로 대체
      - hook-format 불호환 쌍 자동 제외
      - case_study: 가상 임상 시나리오 프레임 + 환자 사례 금지 재정의
      - comparison + 광고 mode: 결론부에만 CTA 허용 지시
      - seasonal: 현재 계절 컨텍스트 주입
      - citation_block 존재 시: LLM의 참고 자료 섹션 생략 지시
    """
    if not diversity_config.get("enabled", False):
        return {
            "format_id": None, "hook_id": None, "prompt_block": "", "citation_block": "",
            "exclude_intro_pattern": False, "exclude_body_patterns": set(),
        }

    # 1. Format 선택
    chosen_format = select_format(diversity_config, user_choice=format_id)
    fmt_id = chosen_format["id"]

    # lifestyle + 짧은 글자 수(2000자 미만): 팁 5~7개 구조 충족 불가 → information으로 교체
    if fmt_id == "lifestyle":
        min_chars = char_count.get("min", 2000) if char_count else 2000
        if min_chars < 2000:
            info_raw = next(
                (f for f in diversity_config.get("blog_formats", []) if f["id"] == "information"),
                None,
            )
            if info_raw:
                from format_selector import FormatChoice
                chosen_format = FormatChoice(
                    id=info_raw["id"],
                    label=info_raw["label"],
                    template_path=info_raw["template"],
                )
                fmt_id = chosen_format["id"]

    # 2. Hook 선택 — format과 불호환 hook 제외
    excluded_hooks = _HOOK_FORMAT_INCOMPATIBLE.get(fmt_id, set())
    hook_id = select_hook(diversity_config, excluded_hooks=excluded_hooks)
    hook_instruction = get_hook_instruction(hook_id)

    format_template = load_format_template(chosen_format["template_path"])
    citation_block = build_citation_block(keyword, diversity_config)

    prompt_lines: list[str] = []

    # 3. citation이 있으면 LLM의 참고 자료 섹션 생략 지시
    if citation_block:
        prompt_lines.append(
            "\n[참고 자료 생략] 관련 학술자료는 시스템이 자동으로 추가합니다. "
            "본문에서 '## 참고 자료', '## 미주', '## 출처' 등 별도 섹션을 작성하지 마세요."
        )

    # 4. Format sub-prompt
    if format_template:
        prompt_lines.append(f"\n## 이번 글 형식 — {chosen_format['label']}")
        prompt_lines.append(format_template)

        if fmt_id == "case_study":
            prompt_lines.append(
                "\n[형식 재정의] 케이스 스터디 형식에서는 위의 '환자 익명 처리 규칙'을 준수한 "
                "가상 임상 시나리오 작성이 허용됩니다. "
                "기본 프롬프트의 '환자 사례 금지'는 실명·식별 가능 정보 금지를 의미하며, "
                "'환자 A씨'·연령대(50대)·증상만 사용하는 가상 시나리오는 허용됩니다."
            )

        if fmt_id == "comparison" and mode == "광고":
            prompt_lines.append(
                "\n[모드 조정] 비교형 형식은 중립적 비교를 원칙으로 합니다. "
                "광고 모드의 내원 유도 표현은 비교 섹션이 아닌 결론부에서만 1회 사용하세요."
            )

        if fmt_id == "seasonal":
            prompt_lines.append(f"\n[현재 계절] {_get_current_season_note()}")

    # 5. Hook 지시 — hook이 서론을 전담하므로 pattern_selector의 INTRO는 skip됨
    if hook_instruction:
        prompt_lines.append(f"\n## 도입부 hook — {hook_id}")
        prompt_lines.append(f"글의 첫 문단을 다음 방식으로 시작하세요: {hook_instruction}")

    return {
        "format_id": fmt_id,
        "format_label": chosen_format["label"],
        "hook_id": hook_id,
        "prompt_block": "\n".join(prompt_lines),
        "citation_block": citation_block,
        "exclude_intro_pattern": True,
        "exclude_body_patterns": _FORMAT_BODY_EXCLUDE.get(fmt_id, set()),
    }


def _build_clinic_info_section(clinic_info: str) -> str:
    """한의원 차별화 정보 프롬프트 블록 생성"""
    if not clinic_info or not clinic_info.strip():
        return ""
    return (
        f"\n## 우리 한의원 차별화 정보 (반드시 본문에 자연스럽게 녹여 반영)\n"
        f"{clinic_info.strip()}\n"
        f"- 위 정보를 광고 티 나지 않게 본문 치료 접근 또는 마무리 섹션에 자연스럽게 포함하세요.\n"
        f"- 다른 한의원과의 차별점이 독자에게 전달되도록 작성하세요."
    )


def _build_related_posts_section(recent_posts: List[dict], current_keyword: str) -> str:
    """이전 포스트 연관 링크 프롬프트 블록 생성"""
    if not recent_posts:
        return ""
    # 현재 주제와 키워드가 겹치는 글만 필터
    related = [
        p for p in recent_posts
        if p["keyword"] != current_keyword and (
            any(k in current_keyword for k in p.get("seo_keywords", []))
            or p["keyword"] in current_keyword
            or current_keyword in p["keyword"]
        )
    ]
    if not related:
        return ""
    post_lines = "\n".join(
        f"  - 제목: {p['title']} (주제: {p['keyword']})" for p in related[:3]
    )
    return (
        f"\n## 연관 이전 포스트 (글 하단 '관련 글' 섹션에 링크로 추가)\n"
        f"아래 이전 블로그 글과 연결하여 독자의 체류 시간을 높이세요.\n"
        f"{post_lines}\n"
        f"- 글 맨 끝 마무리 다음에 '---\\n**관련 글 더 보기**' 섹션을 추가하고,\n"
        f"  각 글을 '[제목](URL을_여기에_입력)' 형식으로 나열하세요.\n"
        f"- URL은 실제 네이버 블로그 발행 후 수동으로 채워 넣으면 됩니다."
    )


def build_prompt_text(
    keyword: str,
    answers: Optional[dict] = None,
    materials: Optional[dict] = None,
    mode: str = "정보",
    reader_level: str = "일반인",
    seo_keywords: Optional[List[str]] = None,
    clinic_info: str = "",
    format_id: Optional[str] = None,
    explanation_types: Optional[List[str]] = None,
) -> dict:
    """
    Claude에 전송할 system_prompt + user_message를 반환한다.
    API 호출 없이 프롬프트만 조립 — T1(프롬프트 복사) 기능용.

    반환: {"system_prompt": str, "user_message": str, "format_id": str|None, "hook_id": str|None}
    """
    config = load_config()
    tone = answers.get("tone", config["blog"]["tone"]) if answers else config["blog"]["tone"]
    history_path = Path(__file__).parent.parent / "data" / "blog_history.json"

    diversity_cfg = config.get("diversity", {})
    diversity = _build_diversity_section(keyword, diversity_cfg, format_id=format_id, mode=mode)

    # case_study 형식에서 reader_level=일반인 → 한의학관심 자동 상향
    if diversity.get("format_id") == "case_study" and reader_level == "일반인":
        reader_level = "한의학관심"

    pattern_result = select_patterns(
        keyword=keyword,
        materials=materials,
        history_path=history_path,
        exclude_intro=diversity.get("exclude_intro_pattern", False),
        exclude_body_ids=diversity.get("exclude_body_patterns", set()),
    )
    recent_posts = get_recent_posts(limit=5)

    prompt_template = load_prompt("blog")
    system_prompt = prompt_template.format(
        min_chars=config["blog"]["min_chars"],
        max_chars=config["blog"]["max_chars"],
        tone=tone,
        pattern_instructions=pattern_result["prompt_block"],
        mode_instructions=_MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS["정보"]),
        reader_level_instructions=_READER_LEVEL_INSTRUCTIONS.get(
            reader_level, _READER_LEVEL_INSTRUCTIONS["일반인"]
        ),
        seo_keywords_section=_build_seo_keywords_section(seo_keywords or []),
        explanation_section=_build_explanation_section(explanation_types),
        clinic_info_section=_build_clinic_info_section(clinic_info),
        related_posts_section=_build_related_posts_section(recent_posts, keyword),
    )
    if diversity["prompt_block"]:
        system_prompt = system_prompt + "\n\n" + diversity["prompt_block"]

    qa_text = ""
    if answers:
        filled = {k: v for k, v in answers.items() if k != "tone" and str(v).strip()}
        if filled:
            qa_text = "\n\n## 추가 정보 (아래 내용을 블로그에 반영해주세요)\n"
            for key, value in filled.items():
                qa_text += f"- {key}: {value}\n"

    materials_text = ""
    if materials:
        if materials.get("text", "").strip():
            materials_text += f"\n\n## 추가 자료 — 텍스트 메모\n{materials['text']}"
        if materials.get("webLinks"):
            materials_text += "\n\n## 추가 자료 — 웹 링크 (참고)\n"
            materials_text += "\n".join(f"- {url}" for url in materials["webLinks"])
        if materials.get("youtubeLinks"):
            materials_text += "\n\n## 추가 자료 — 유튜브 링크 (참고)\n"
            materials_text += "\n".join(f"- {url}" for url in materials["youtubeLinks"])

    seo_prefix = ""
    if seo_keywords:
        kw_blocks = []
        for kw in seo_keywords:
            examples = "\n".join([
                f'    · "{kw}로 고민하시는 분들께..."',
                f'    · "{kw} 치료, 한의학적으로 어떻게 접근할까요?"',
                f'    · "{kw} 환자분들이 가장 많이 묻는 질문..."',
                f'    · "{kw} 증상이 있으시다면..."',
                f'    · "{kw}의 한방 치료 핵심은..."',
                f'    · "오늘은 {kw}에 대해 알아보겠습니다."',
            ])
            kw_blocks.append(
                f"  키워드: '{kw}' — 반드시 이 문자열 그대로 6~8회 사용\n"
                f"  아래와 같은 형태로 각 섹션에 자연스럽게 삽입하세요:\n{examples}"
            )
        kw_section = "\n\n".join(kw_blocks)
        seo_prefix = (
            f"## ⚠ SEO 키워드 필수 삽입 — 작성 전 반드시 읽을 것\n"
            f"아래 키워드를 절대 분리하지 말고, 제시된 예시 형태로 6~8회 삽입하세요.\n\n"
            f"{kw_section}\n\n"
        )

    user_message = f"{seo_prefix}블로그 주제: {keyword}{qa_text}{materials_text}"
    return {
        "system_prompt": system_prompt,
        "user_message": user_message,
        "format_id": diversity.get("format_id"),
        "hook_id": diversity.get("hook_id"),
    }


def generate_blog_stream(
    keyword: str,
    answers: dict,
    api_key: str,
    materials: Optional[dict] = None,
    mode: str = "정보",
    reader_level: str = "일반인",
    seo_keywords: Optional[List[str]] = None,
    clinic_info: str = "",
    explanation_types: Optional[List[str]] = None,
    char_count: Optional[dict] = None,
    format_id: Optional[str] = None,
) -> Generator[str, None, None]:
    """
    블로그 생성 스트리밍 제너레이터

    SSE(Server-Sent Events) 형식으로 데이터를 yield합니다.
    - 생성 중: {"text": "..."}
    - 완료 시: {"done": true, "usage": {...}, "series": [...], "format_id": "...", "hook_id": "..."}
    - 오류 시: {"error": "..."}
    """
    config = load_config()
    tone = answers.get("tone", config["blog"]["tone"]) if answers else config["blog"]["tone"]

    history_path = Path(__file__).parent.parent / "data" / "blog_history.json"

    # v0.3 다양성 레이어 — pattern_selector보다 먼저 실행해야 충돌 방지 파라미터 전달 가능
    diversity_cfg = config.get("diversity", {})
    diversity = _build_diversity_section(
        keyword, diversity_cfg, format_id=format_id, mode=mode, char_count=char_count
    )

    # case_study 형식에서 reader_level=일반인 → 한의학관심 자동 상향
    # (임상 용어 과도한 풀이 요구로 케이스 스터디 흐름이 끊기는 문제 방지)
    if diversity.get("format_id") == "case_study" and reader_level == "일반인":
        reader_level = "한의학관심"

    # hook이 서론을 담당 + format과 중복되는 body 패턴 제외
    pattern_result = select_patterns(
        keyword=keyword,
        materials=materials,
        history_path=history_path,
        exclude_intro=diversity.get("exclude_intro_pattern", False),
        exclude_body_ids=diversity.get("exclude_body_patterns", set()),
    )

    # 이전 포스트 연관 링크
    recent_posts = get_recent_posts(limit=5)

    # char_count가 프론트에서 전달된 경우 config 값을 오버라이드
    min_chars = char_count["min"] if char_count and "min" in char_count else config["blog"]["min_chars"]
    max_chars = char_count["max"] if char_count and "max" in char_count else config["blog"]["max_chars"]

    prompt_template = load_prompt("blog")
    system_prompt = prompt_template.format(
        min_chars=min_chars,
        max_chars=max_chars,
        tone=tone,
        pattern_instructions=pattern_result["prompt_block"],
        mode_instructions=_MODE_INSTRUCTIONS.get(mode, _MODE_INSTRUCTIONS["정보"]),
        reader_level_instructions=_READER_LEVEL_INSTRUCTIONS.get(
            reader_level, _READER_LEVEL_INSTRUCTIONS["일반인"]
        ),
        seo_keywords_section=_build_seo_keywords_section(seo_keywords or []),
        explanation_section=_build_explanation_section(explanation_types),
        clinic_info_section=_build_clinic_info_section(clinic_info),
        related_posts_section=_build_related_posts_section(recent_posts, keyword),
    )
    # v0.3: diversity 지시 블록 추가 (기존 프롬프트 뒤에 append)
    if diversity["prompt_block"]:
        system_prompt = system_prompt + "\n\n" + diversity["prompt_block"]

    # Q&A 답변 컨텍스트
    qa_text = ""
    if answers:
        filled = {k: v for k, v in answers.items() if k != "tone" and str(v).strip()}
        if filled:
            qa_text = "\n\n## 추가 정보 (아래 내용을 블로그에 반영해주세요)\n"
            for key, value in filled.items():
                qa_text += f"- {key}: {value}\n"

    # 추가 자료 컨텍스트
    materials_text = ""
    if materials:
        if materials.get("text", "").strip():
            materials_text += f"\n\n## 추가 자료 — 텍스트 메모\n{materials['text']}"
        if materials.get("webLinks"):
            materials_text += "\n\n## 추가 자료 — 웹 링크 (참고)\n"
            materials_text += "\n".join(f"- {url}" for url in materials["webLinks"])
        if materials.get("youtubeLinks"):
            materials_text += "\n\n## 추가 자료 — 유튜브 링크 (참고)\n"
            materials_text += "\n".join(f"- {url}" for url in materials["youtubeLinks"])

    seo_prefix = ""
    if seo_keywords:
        kw_blocks = []
        for kw in seo_keywords:
            examples = "\n".join([
                f'    · "{kw}로 고민하시는 분들께..."',
                f'    · "{kw} 치료, 한의학적으로 어떻게 접근할까요?"',
                f'    · "{kw} 환자분들이 가장 많이 묻는 질문..."',
                f'    · "{kw} 증상이 있으시다면..."',
                f'    · "{kw}의 한방 치료 핵심은..."',
                f'    · "오늘은 {kw}에 대해 알아보겠습니다."',
            ])
            kw_blocks.append(
                f"  키워드: '{kw}' — 반드시 이 문자열 그대로 6~8회 사용\n"
                f"  아래와 같은 형태로 각 섹션에 자연스럽게 삽입하세요:\n{examples}"
            )
        kw_section = "\n\n".join(kw_blocks)
        seo_prefix = (
            f"## ⚠ SEO 키워드 필수 삽입 — 작성 전 반드시 읽을 것\n"
            f"아래 키워드를 절대 분리하지 말고, 제시된 예시 형태로 6~8회 삽입하세요.\n\n"
            f"{kw_section}\n\n"
        )

    user_message = f"{seo_prefix}블로그 주제: {keyword}{qa_text}{materials_text}"

    client = anthropic.Anthropic(api_key=api_key)

    try:
        collected_text: list[str] = []
        input_tokens = 0
        output_tokens = 0

        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=4500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text_chunk in stream.text_stream:
                collected_text.append(text_chunk)
                yield f"data: {json.dumps({'text': text_chunk}, ensure_ascii=False)}\n\n"

            # 1단계: 키워드 보강 — 사용자가 입력한 SEO 키워드 태그만 대상
            # keyword(블로그 주제)는 제외 — 주제는 AI가 자연스럽게 반영함
            original_text = "".join(collected_text)
            effective_keywords = list(seo_keywords or [])
            fixed_text = _fix_keyword_counts(original_text, effective_keywords)

            # v0.3: citation block을 본문 끝에 추가
            citation_block = diversity.get("citation_block", "")
            if citation_block:
                fixed_text = fixed_text.rstrip() + "\n\n" + citation_block

            if fixed_text != original_text:
                yield f"data: {json.dumps({'replace': fixed_text}, ensure_ascii=False)}\n\n"
            else:
                fixed_text = original_text

            # 2단계: 토큰 사용량 (실패해도 done은 전송)
            try:
                final = stream.get_final_message()
                input_tokens = final.usage.input_tokens
                output_tokens = final.usage.output_tokens
            except Exception:
                pass

        cost_usd = (input_tokens * PRICE_INPUT_PER_M + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000
        cost_krw = int(cost_usd * KRW_PER_USD)

        # 3단계: 시리즈 추천 (실패해도 done은 전송)
        try:
            series = generate_series_suggestions(keyword, fixed_text, api_key)
        except Exception:
            series = []

        # 4단계: FAQPage JSON-LD 추출 (실패해도 done은 전송)
        # qna 형식은 블로그 전체가 Q&A 구조이므로 추출 skip (중복 방지)
        if diversity.get("format_id") == "qna":
            faq_schema = None
        else:
            try:
                faq_schema = extract_faq_schema(fixed_text, keyword)
            except Exception:
                faq_schema = None

        yield f"data: {json.dumps({'done': True, 'usage': {'input': input_tokens, 'output': output_tokens, 'cost_krw': cost_krw}, 'series': series, 'seo_keywords': seo_keywords or [], 'faq_schema': faq_schema, 'format_id': diversity.get('format_id'), 'hook_id': diversity.get('hook_id')}, ensure_ascii=False)}\n\n"

    except anthropic.AuthenticationError:
        yield _error_event("API 키를 확인해주세요. .env 파일의 ANTHROPIC_API_KEY를 확인하세요.")
    except anthropic.RateLimitError:
        yield _error_event("잠시 후 다시 시도해주세요. (요청 한도 초과)")
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            yield _error_event("Claude 크레딧을 충전해주세요. console.anthropic.com에서 확인하세요.")
        else:
            yield _error_event(f"API 오류 ({e.status_code}): {e.message}")
    except Exception as e:
        yield _error_event(f"오류가 발생했습니다: {str(e)}")


def _error_event(message: str) -> str:
    """SSE 오류 이벤트 포맷"""
    return f"data: {json.dumps({'error': message}, ensure_ascii=False)}\n\n"
