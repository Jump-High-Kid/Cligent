import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from src.main import app

client = TestClient(app)


def get_auth_cookies():
    res = client.post("/api/auth/login", json={
        "email": "owner@cligent.dev",
        "password": "Demo1234!"
    })
    return res.cookies


def test_get_available_agents():
    cookies = get_auth_cookies()
    res = client.get("/api/agents/available", cookies=cookies)
    assert res.status_code == 200
    agents = res.json()["agents"]
    assert any(a["name"] == "blog-agent" for a in agents)


def test_agent_chat_routing():
    cookies = get_auth_cookies()
    with patch("src.main.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="블로그 아이디어입니다.")]
        mock_msg.usage.input_tokens = 100
        mock_msg.usage.output_tokens = 50
        mock_client.messages.create.return_value = mock_msg

        with patch("src.main._create_anthropic_client", return_value=mock_client):
            res = client.post("/api/agent/chat", json={
                "message": "블로그 아이디어 알려줘"
            }, cookies=cookies)
    assert res.status_code == 200
    data = res.json()
    assert data["agent_name"] == "blog-agent"
    assert "response" in data


def test_agent_chat_no_match():
    cookies = get_auth_cookies()
    res = client.post("/api/agent/chat", json={
        "message": "오늘 점심 뭐 먹지"
    }, cookies=cookies)
    assert res.status_code == 200
    data = res.json()
    assert data["agent_name"] is None
    assert "매칭되는 에이전트가 없습니다" in data["response"]


def test_agent_chat_path_traversal_rejected():
    """등록되지 않은 agent 명시 지정 시 에러 응답"""
    cookies = get_auth_cookies()
    res = client.post("/api/agent/chat", json={
        "message": "test",
        "agent": "../../.env"
    }, cookies=cookies)
    assert res.status_code in (400, 200)
    if res.status_code == 200:
        assert res.json().get("error") is True


def test_rate_limit_61st_request_blocked():
    """분당 61번째 요청은 차단"""
    from src.main import _check_rate_limit, _rate_buckets
    _rate_buckets.clear()
    for _ in range(60):
        assert _check_rate_limit("test-clinic") is True
    assert _check_rate_limit("test-clinic") is False
