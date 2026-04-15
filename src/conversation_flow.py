"""
conversation_flow.py — 블로그 주제를 받아 동적 대화 흐름을 생성합니다.
Claude API를 호출해 질문+선택지 목록을 JSON으로 반환합니다.
"""
import json
import anthropic
from config_loader import load_prompt

# 파싱 실패 시 사용할 기본 대화 흐름
DEFAULT_FLOW = [
    {
        "id": "tone",
        "message": "어떤 톤으로 쓸까요?",
        "options": ["전문적", "친근한", "설명적"],
    }
]


def generate_conversation_flow(keyword: str, api_key: str) -> list[dict]:
    """
    키워드를 입력받아 대화형 설정 흐름 반환

    Returns:
        [
            {"id": "tone", "message": "...", "options": ["...", ...]},
            {"id": "audience", "message": "...", "options": ["...", ...]},
            ...
        ]
    """
    system_prompt = load_prompt("conversation")
    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": f"블로그 주제: {keyword}"}],
        )

        text = message.content[0].text.strip()
        flow = json.loads(text)

        # 유효성 검사: 리스트이고 각 항목에 id/message/options 있는지 확인
        if not isinstance(flow, list):
            return DEFAULT_FLOW
        for item in flow:
            if not all(k in item for k in ("id", "message", "options")):
                return DEFAULT_FLOW

        return flow

    except anthropic.AuthenticationError:
        raise ValueError("API 키를 확인해주세요. .env 파일의 ANTHROPIC_API_KEY를 확인하세요.")
    except anthropic.RateLimitError:
        raise ValueError("잠시 후 다시 시도해주세요. (요청 한도 초과)")
    except anthropic.APIStatusError as e:
        if e.status_code == 402:
            raise ValueError("Claude 크레딧을 충전해주세요. console.anthropic.com에서 확인하세요.")
        raise ValueError(f"API 오류 ({e.status_code}): {e.message}")
    except json.JSONDecodeError:
        # Claude가 JSON 형식을 지키지 않은 경우 기본 흐름 반환
        return DEFAULT_FLOW
