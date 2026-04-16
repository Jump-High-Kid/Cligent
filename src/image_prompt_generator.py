"""
image_prompt_generator.py — 블로그 본문을 분석하여 이미지 프롬프트 5개를 스트리밍으로 생성합니다.
블로그 흐름(도입 → 원인 → 치료 → 생활 → 마무리)에 맞춰 순차 배치 프롬프트를 반환합니다.
"""
import json
from typing import Generator
import anthropic
from config_loader import load_prompt


def generate_image_prompts_stream(
    keyword: str, blog_content: str, api_key: str
) -> Generator[str, None, None]:
    """
    이미지 프롬프트 5개를 SSE 스트리밍으로 생성합니다.

    SSE 형식:
    - 생성 중: {"text": "..."}
    - 완료 시: {"done": true, "usage": {...}}
    - 오류 시: {"error": "..."}

    Args:
        keyword: 블로그 주제
        blog_content: 생성된 블로그 본문 (마크다운)
        api_key: Anthropic API 키
    """
    # 이미지 프롬프트 생성 지침 로드
    system_prompt = load_prompt("image_prompt")

    user_message = (
        f"블로그 주제: {keyword}\n\n"
        f"=== 블로그 본문 ===\n{blog_content}\n=== 본문 끝 ==="
    )

    client = anthropic.Anthropic(api_key=api_key)

    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text_chunk in stream.text_stream:
                yield f"data: {json.dumps({'text': text_chunk}, ensure_ascii=False)}\n\n"

            final = stream.get_final_message()
            input_tokens = final.usage.input_tokens
            output_tokens = final.usage.output_tokens

            yield f"data: {json.dumps({'done': True, 'usage': {'input': input_tokens, 'output': output_tokens}}, ensure_ascii=False)}\n\n"

    except anthropic.AuthenticationError:
        yield _error_event("API 키를 확인해주세요.")
    except anthropic.RateLimitError:
        yield _error_event("잠시 후 다시 시도해주세요. (요청 한도 초과)")
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            yield _error_event("Claude 크레딧을 충전해주세요.")
        else:
            yield _error_event(f"API 오류 ({e.status_code}): {e.message}")
    except Exception as e:
        yield _error_event(f"오류가 발생했습니다: {str(e)}")


def _error_event(message: str) -> str:
    return f"data: {json.dumps({'error': message}, ensure_ascii=False)}\n\n"
