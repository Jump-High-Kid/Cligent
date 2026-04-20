"""
image_prompt_generator.py — 2단계 파이프라인으로 이미지 프롬프트 5개를 생성합니다.

Stage 1: 블로그 분석 → 구조화된 JSON (장면 계획, 경혈 선택, 카메라 앵글)
Stage 2: JSON + 스타일/톤 → 이미지 AI용 프롬프트 배열 (JSON)

SSE 이벤트 형식:
  {"status": "analyzing", "message": "블로그 분석 중..."}
  {"status": "generating", "message": "프롬프트 생성 중..."}
  {"done": true, "prompts": [...], "usage": {"input": N, "output": N}}
  {"error": "오류 메시지"}
"""
import json
import re
from typing import Generator

import anthropic

from config_loader import load_prompt

# 허용된 스타일/톤 값 (입력 검증용)
VALID_STYLES = {"photorealistic", "anime", "cartoon", "illustration", "watercolor", "3d_render"}
VALID_TONES  = {"warm", "cool_white", "soft", "editorial", "minimal", "natural"}


def _call_claude(system: str, user: str, api_key: str, max_tokens: int) -> tuple[str, dict]:
    """Claude API 단순 호출 → (응답 텍스트, usage) 반환"""
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = response.content[0].text
    usage = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }
    return text, usage


def _parse_json_response(text: str) -> dict:
    """응답에서 JSON 추출 — 코드 블록(```json ... ```) 포함 처리"""
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    return json.loads(cleaned)


def _analyze_blog(keyword: str, blog_content: str, api_key: str) -> tuple[dict, dict]:
    """Stage 1: 블로그 분석 → 구조화된 JSON 반환"""
    system = load_prompt("image_analysis")
    user = (
        f"블로그 주제: {keyword}\n\n"
        f"=== 블로그 본문 ===\n{blog_content}\n=== 본문 끝 ==="
    )
    text, usage = _call_claude(system, user, api_key, max_tokens=1500)
    return _parse_json_response(text), usage


def _generate_prompts(
    analysis: dict,
    api_key: str,
    style: str = "photorealistic",
    tone: str = "warm",
) -> tuple[dict, dict]:
    """Stage 2: 분석 JSON + 스타일/톤 → 이미지 프롬프트 배열 JSON 반환"""
    system = load_prompt("image_generation")
    user = (
        f"IMAGE_STYLE: {style}\n"
        f"IMAGE_TONE: {tone}\n\n"
        f"블로그 분석 결과:\n"
        f"{json.dumps(analysis, ensure_ascii=False, indent=2)}"
    )
    text, usage = _call_claude(system, user, api_key, max_tokens=4000)
    return _parse_json_response(text), usage


def generate_image_prompts_stream(
    keyword: str,
    blog_content: str,
    api_key: str,
    style: str = "photorealistic",
    tone: str = "warm",
) -> Generator[str, None, None]:
    """
    이미지 프롬프트 5개를 2단계 파이프라인으로 생성하고 SSE로 반환합니다.

    SSE 이벤트 순서:
      1. {"status": "analyzing", "message": "..."}
      2. {"status": "generating", "message": "..."}
      3. {"done": true, "prompts": [...], "usage": {...}}
      또는 {"error": "..."}
    """
    def _event(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 허용 범위 밖의 값은 기본값으로 대체
    safe_style = style if style in VALID_STYLES else "photorealistic"
    safe_tone  = tone  if tone  in VALID_TONES  else "warm"

    try:
        yield _event({"status": "analyzing", "message": "블로그 분석 중..."})
        analysis, usage1 = _analyze_blog(keyword, blog_content, api_key)

        yield _event({"status": "generating", "message": "프롬프트 생성 중..."})
        result, usage2 = _generate_prompts(analysis, api_key, safe_style, safe_tone)

        total_usage = {
            "input":  usage1["input"]  + usage2["input"],
            "output": usage1["output"] + usage2["output"],
        }
        yield _event({
            "done":   True,
            "prompts": result.get("prompts", []),
            "usage":  total_usage,
        })

    except json.JSONDecodeError as e:
        yield _event({"error": f"응답 파싱 오류: {str(e)}"})
    except anthropic.AuthenticationError:
        yield _event({"error": "API 키를 확인해주세요."})
    except anthropic.RateLimitError:
        yield _event({"error": "잠시 후 다시 시도해주세요. (요청 한도 초과)"})
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            yield _event({"error": "Claude 크레딧을 충전해주세요."})
        else:
            yield _event({"error": f"API 오류 ({e.status_code}): {e.message}"})
    except Exception as e:
        yield _event({"error": f"오류가 발생했습니다: {str(e)}"})
