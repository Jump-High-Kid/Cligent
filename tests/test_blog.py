"""
test_blog.py — Cligent 블로그 생성기 pytest 테스트 (7개)
FastAPI TestClient + unittest.mock으로 Anthropic API 호출을 모킹합니다.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-only")

from main import app
from auth_manager import get_current_user

client = TestClient(app)

FAKE_USER = {
    "id": 1,
    "clinic_id": 1,
    "role": "chief_director",
    "email": "test@test.com",
    "is_active": True,
}


@pytest.fixture(autouse=True)
def override_auth():
    """모든 테스트에서 JWT 인증을 가짜 유저로 대체."""
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    yield
    app.dependency_overrides.clear()


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def make_stream_mock(text_chunks: list[str], input_tokens: int = 100, output_tokens: int = 200):
    """client.messages.stream() 컨텍스트 매니저 모킹용 헬퍼"""
    mock_stream = MagicMock()
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_stream.text_stream = iter(text_chunks)

    # 최종 메시지 사용량 모킹
    final_msg = MagicMock()
    final_msg.usage.input_tokens = input_tokens
    final_msg.usage.output_tokens = output_tokens
    mock_stream.get_final_message = MagicMock(return_value=final_msg)

    return mock_stream


def parse_sse(content: bytes) -> list[dict]:
    """SSE 응답 바이트를 파싱해 JSON 이벤트 목록으로 반환"""
    events = []
    for line in content.decode("utf-8").splitlines():
        if line.startswith("data: "):
            raw = line[6:].strip()
            if raw:
                events.append(json.loads(raw))
    return events


# ── 테스트 1: 블로그 생성 성공 (≥1500자 확인) ────────────────────────────────

def test_generate_success():
    """정상적인 블로그 생성 — 스트리밍으로 텍스트가 전달되고 완료 이벤트가 수신된다"""
    long_text = "가" * 1500  # 한국어 1500자
    chunks = [long_text[i:i+100] for i in range(0, len(long_text), 100)]

    mock_stream = make_stream_mock(chunks, input_tokens=500, output_tokens=800)

    with patch("blog_generator.anthropic.Anthropic") as mock_client_cls, \
         patch("routers.blog.check_blog_limit"):
        mock_client_cls.return_value.messages.stream.return_value = mock_stream
        with patch("main.os.getenv", return_value="sk-ant-test-key"):
            res = client.post(
                "/generate",
                json={"keyword": "소화불량 한방 치료", "answers": {}},
            )

    assert res.status_code == 200
    events = parse_sse(res.content)

    # 텍스트 청크가 있어야 함
    text_events = [e for e in events if "text" in e]
    assert len(text_events) > 0

    # 전체 텍스트 길이 확인
    full_text = "".join(e["text"] for e in text_events)
    assert len(full_text) >= 1500

    # 완료 이벤트 확인
    done_events = [e for e in events if e.get("done")]
    assert len(done_events) == 1
    assert done_events[0]["usage"]["input"] == 500
    assert done_events[0]["usage"]["output"] == 800


# ── 테스트 2: 잘못된 API 키 (401) ────────────────────────────────────────────

def test_generate_invalid_api_key():
    """잘못된 API 키 → 한국어 오류 메시지 반환"""
    import anthropic as anthropic_lib

    with patch("blog_generator.anthropic.Anthropic") as mock_client_cls, \
         patch("routers.blog.check_blog_limit"):
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(side_effect=anthropic_lib.AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body={},
        ))
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.messages.stream.return_value = mock_stream

        with patch("main.os.getenv", return_value="sk-ant-invalid"):
            res = client.post(
                "/generate",
                json={"keyword": "소화불량", "answers": {}},
            )

    events = parse_sse(res.content)
    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert "API 키" in error_events[0]["error"]


# ── 테스트 3: 크레딧 부족 (402) ──────────────────────────────────────────────

def test_generate_insufficient_funds():
    """Claude 크레딧 부족(402) → 한국어 충전 안내 메시지"""
    import anthropic as anthropic_lib

    with patch("blog_generator.anthropic.Anthropic") as mock_client_cls, \
         patch("routers.blog.check_blog_limit"):
        mock_stream = MagicMock()
        err = anthropic_lib.APIStatusError(
            message="Your credit balance is too low",
            response=MagicMock(status_code=402),
            body={},
        )
        mock_stream.__enter__ = MagicMock(side_effect=err)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value.messages.stream.return_value = mock_stream

        with patch("main.os.getenv", return_value="sk-ant-test"):
            res = client.post(
                "/generate",
                json={"keyword": "소화불량", "answers": {}},
            )

    events = parse_sse(res.content)
    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert "크레딧" in error_events[0]["error"]


# ── 테스트 4: 빈 출력 처리 ───────────────────────────────────────────────────

def test_generate_empty_output():
    """Claude가 아무 텍스트도 생성하지 않는 경우 — done 이벤트는 정상 수신"""
    mock_stream = make_stream_mock([], input_tokens=50, output_tokens=0)

    with patch("blog_generator.anthropic.Anthropic") as mock_client_cls, \
         patch("routers.blog.check_blog_limit"):
        mock_client_cls.return_value.messages.stream.return_value = mock_stream
        with patch("main.os.getenv", return_value="sk-ant-test"):
            res = client.post(
                "/generate",
                json={"keyword": "테스트 주제", "answers": {}},
            )

    events = parse_sse(res.content)
    done_events = [e for e in events if e.get("done")]
    assert len(done_events) == 1  # 빈 출력이어도 done 이벤트는 와야 함


# ── 테스트 5: 빈 keyword 입력 검증 ──────────────────────────────────────────

def test_generate_empty_keyword():
    """빈 keyword → SSE 오류 이벤트 반환"""
    with patch("main.os.getenv", return_value="sk-ant-test"), \
         patch("routers.blog.check_blog_limit"):
        res = client.post("/generate", json={"keyword": "", "answers": {}})

    events = parse_sse(res.content)
    error_events = [e for e in events if "error" in e]
    assert len(error_events) == 1
    assert "주제" in error_events[0]["error"]


# ── 테스트 6: 대화 흐름 생성 성공 ───────────────────────────────────────────

def test_conversation_flow_generated():
    """/conversation-flow — 질문+선택지 목록 반환"""
    mock_flow = [
        {"id": "tone", "message": "어떤 톤으로 쓸까요?", "options": ["전문적", "친근한", "설명적"]},
        {"id": "audience", "message": "주요 독자는 누구인가요?", "options": ["만성 환자", "처음 내원 고려 중", "일반인"]},
    ]
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=__import__('json').dumps(mock_flow, ensure_ascii=False))]

    with patch("conversation_flow.anthropic.Anthropic") as mock_client_cls, \
         patch("main.os.getenv", return_value="sk-ant-test"):
        mock_client_cls.return_value.messages.create.return_value = mock_message
        res = client.post("/conversation-flow", json={"keyword": "소화불량"})

    assert res.status_code == 200
    data = res.json()
    assert "questions" in data
    assert len(data["questions"]) == 2
    assert data["questions"][0]["id"] == "tone"


# ── 테스트 7: 대화 흐름 — 빈 keyword ────────────────────────────────────────

def test_conversation_flow_empty_keyword():
    """/conversation-flow에 빈 keyword → 오류 응답"""
    res = client.post("/conversation-flow", json={"keyword": ""})
    assert res.status_code == 200
    data = res.json()
    assert "error" in data
