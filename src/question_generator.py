"""
question_generator.py — 블로그 주제를 받아 맞춤 질문을 생성합니다.
Claude API를 한 번 호출해 JSON 배열로 질문을 받아옵니다.
"""
import json
import anthropic
from config_loader import load_config, load_prompt


def generate_questions(keyword: str, api_key: str) -> list[str]:
    """
    키워드를 입력받아 Claude가 생성한 맞춤 질문 목록 반환

    Args:
        keyword: 블로그 주제 (예: "소화불량 한방 치료")
        api_key: Anthropic API 키

    Returns:
        질문 문자열 리스트 (예: ["증상 패턴은?", "강조할 치료법은?"])

    Raises:
        ValueError: API 키 오류, 요청 한도 초과 등
    """
    config = load_config()
    count = config["flow"]["questions_count"]

    # 프롬프트 파일 로드 후 질문 개수 삽입
    prompt_template = load_prompt("questions")
    system_prompt = prompt_template.format(count=count)

    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": f"블로그 주제: {keyword}"}],
        )

        # Claude 응답을 JSON으로 파싱
        text = message.content[0].text.strip()
        questions = json.loads(text)
        return questions[:count]  # 설정된 개수만큼만 반환

    except anthropic.AuthenticationError:
        raise ValueError("API 키를 확인해주세요. .env 파일의 ANTHROPIC_API_KEY를 확인하세요.")
    except anthropic.RateLimitError:
        raise ValueError("잠시 후 다시 시도해주세요. (요청 한도 초과)")
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            raise ValueError("Claude 크레딧을 충전해주세요. console.anthropic.com에서 확인하세요.")
        raise ValueError(f"API 오류 ({e.status_code}): {e.message}")
    except json.JSONDecodeError:
        # Claude가 JSON 형식을 지키지 않은 경우 줄 단위로 파싱 시도
        lines = [l.strip("- •").strip() for l in text.split("\n") if l.strip() and l.strip() not in ["[", "]"]]
        return [l for l in lines if l][:count]
