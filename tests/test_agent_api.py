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


def test_agent_chat_disabled_returns_410():
    """K-7 (2026-05-04): /api/agent/chat 라우트 비활성화 — 410 Gone.

    베타 미사용 라우트. 어뷰저의 무제한 입력 + Claude API 비용 트리거 진입점 봉인.
    재도입 시 본 테스트를 라우팅·rate_limit·길이 제한 검증 테스트로 교체."""
    cookies = get_auth_cookies()
    res = client.post("/api/agent/chat", json={
        "message": "블로그 아이디어 알려줘"
    }, cookies=cookies)
    assert res.status_code == 410
    data = res.json()
    assert data["error"] is True
    assert "비활성화" in data["response"]


def test_agent_chat_disabled_no_body_processing():
    """비활성 라우트는 거대 입력도 즉시 차단 (body parse 전에 410)."""
    cookies = get_auth_cookies()
    res = client.post("/api/agent/chat", json={
        "message": "x" * 100000,
        "agent": "blog-agent"
    }, cookies=cookies)
    assert res.status_code == 410


def test_rate_limit_61st_request_blocked():
    """분당 61번째 요청은 차단"""
    from src.main import _check_rate_limit, _rate_buckets
    _rate_buckets.clear()
    for _ in range(60):
        assert _check_rate_limit("test-clinic") is True
    assert _check_rate_limit("test-clinic") is False
