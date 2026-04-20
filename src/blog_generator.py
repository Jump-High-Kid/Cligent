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


def generate_blog_stream(
    keyword: str, answers: dict, api_key: str, materials: Optional[dict] = None
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

    # 프롬프트 파일 로드 후 설정값 + 패턴 지시 삽입
    prompt_template = load_prompt("blog")
    system_prompt = prompt_template.format(
        min_chars=config["blog"]["min_chars"],
        max_chars=config["blog"]["max_chars"],
        tone=tone,
        pattern_instructions=pattern_result["prompt_block"],
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
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=3000,  # 2000자 한국어 ≈ 최대 3000토큰
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            # 텍스트 청크를 SSE 형식으로 실시간 전달
            for text_chunk in stream.text_stream:
                yield f"data: {json.dumps({'text': text_chunk}, ensure_ascii=False)}\n\n"

            # 스트리밍 완료 후 토큰 사용량 및 비용 계산
            final = stream.get_final_message()
            input_tokens = final.usage.input_tokens
            output_tokens = final.usage.output_tokens
            cost_usd = (input_tokens * PRICE_INPUT_PER_M + output_tokens * PRICE_OUTPUT_PER_M) / 1_000_000
            cost_krw = int(cost_usd * KRW_PER_USD)

            yield f"data: {json.dumps({'done': True, 'usage': {'input': input_tokens, 'output': output_tokens, 'cost_krw': cost_krw}}, ensure_ascii=False)}\n\n"

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
