"""
blog_generator.py — 주제와 Q&A 답변을 받아 블로그를 스트리밍으로 생성합니다.
FastAPI의 StreamingResponse와 연결되어 실시간으로 텍스트를 전달합니다.
"""
import json
from pathlib import Path
from typing import Generator, Optional
import anthropic
from config_loader import load_config, load_prompt
from pattern_selector import select_patterns

# claude-sonnet-4-6 가격 (2025년 기준, 달러 기준)
PRICE_INPUT_PER_M = 3.0    # $3 / 1M 입력 토큰
PRICE_OUTPUT_PER_M = 15.0  # $15 / 1M 출력 토큰
KRW_PER_USD = 1400         # 환율 (대략적인 참고값)


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
        lines = [l.strip() for l in resp.content[0].text.strip().splitlines() if l.strip()]
        return lines[:3]
    except Exception:
        return []


def generate_blog_stream(
    keyword: str, answers: dict, api_key: str,
    materials: Optional[dict] = None,
    mode: str = "정보",
    reader_level: str = "일반인",
) -> Generator[str, None, None]:
    """
    블로그 생성 스트리밍 제너레이터

    SSE(Server-Sent Events) 형식으로 데이터를 yield합니다.
    - 생성 중: {"text": "..."}
    - 완료 시: {"done": true, "usage": {...}}
    - 오류 시: {"error": "..."}

    Args:
        keyword: 블로그 주제
        answers: Q&A 답변 딕셔너리 {"질문": "답변", ...}
        api_key: Anthropic API 키
    """
    config = load_config()

    # 톤: 대화에서 선택한 값 우선, 없으면 config 기본값
    tone = answers.get("tone", config["blog"]["tone"]) if answers else config["blog"]["tone"]

    # 블로그 히스토리 경로 (data/ 폴더 기준)
    history_path = Path(__file__).parent.parent / "data" / "blog_history.json"

    # 패턴 조합 선택 (5개 레이어 검증 + 화제 전환 포함)
    pattern_result = select_patterns(
        keyword=keyword,
        materials=materials,
        history_path=history_path,
    )

    # 프롬프트 파일 로드 후 설정값 + 패턴/모드/독자수준 지시 삽입
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
    )

    # Q&A 답변을 컨텍스트로 구성 (tone 제외, 답변이 있는 항목만 포함)
    qa_text = ""
    if answers:
        filled = {k: v for k, v in answers.items() if k != "tone" and str(v).strip()}
        if filled:
            qa_text = "\n\n## 추가 정보 (아래 내용을 블로그에 반영해주세요)\n"
            for key, value in filled.items():
                qa_text += f"- {key}: {value}\n"

    # 추가 자료 컨텍스트 구성
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

    user_message = f"블로그 주제: {keyword}{qa_text}{materials_text}"

    client = anthropic.Anthropic(api_key=api_key)

    try:
        collected_text: list[str] = []
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=3000,  # 2000자 한국어 ≈ 최대 3000토큰
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            # 텍스트 청크를 SSE 형식으로 실시간 전달
            for text_chunk in stream.text_stream:
                collected_text.append(text_chunk)
                yield f"data: {json.dumps({'text': text_chunk}, ensure_ascii=False)}\n\n"

            # 스트리밍 완료 후 토큰 사용량 및 비용 계산
            final = stream.get_final_message()
            input_tokens = final.usage.input_tokens
            output_tokens = final.usage.output_tokens
            cost_usd = (input_tokens * PRICE_INPUT_PER_M + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000
            cost_krw = int(cost_usd * KRW_PER_USD)

            series = generate_series_suggestions(keyword, "".join(collected_text), api_key)
            yield f"data: {json.dumps({'done': True, 'usage': {'input': input_tokens, 'output': output_tokens, 'cost_krw': cost_krw}, 'series': series}, ensure_ascii=False)}\n\n"

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
