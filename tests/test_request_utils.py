"""
test_request_utils.py — dependencies.get_real_ip 유닛 테스트

K-2 보안 감사: Caddy 뒤에서 request.client.host 가 항상 127.0.0.1 이 되어
레이트 리밋·login_history·의심 IP 감지가 무력화되던 문제 해결.

X-Forwarded-For 체인의 last-hop 만 신뢰 (Caddy 가 추가한 값 = 진짜 클라이언트 IP).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dependencies import get_real_ip


def _make_request(xff: str = None, client_host: str = "127.0.0.1"):
    """FastAPI Request 객체 mock — headers + client.host 만 사용."""
    req = MagicMock()
    req.headers = {"X-Forwarded-For": xff} if xff is not None else {}
    if client_host is None:
        req.client = None
    else:
        req.client = MagicMock()
        req.client.host = client_host
    # MagicMock 의 headers.get 은 자동으로 dict.get 으로 위임됨
    req.headers = req.headers  # noqa
    return req


class TestGetRealIp:
    def test_no_xff_falls_back_to_client_host(self):
        req = _make_request(xff=None, client_host="203.0.113.5")
        assert get_real_ip(req) == "203.0.113.5"

    def test_single_xff_value(self):
        req = _make_request(xff="203.0.113.5", client_host="127.0.0.1")
        assert get_real_ip(req) == "203.0.113.5"

    def test_xff_chain_returns_last_hop(self):
        # 클라이언트 위조 가능: "spoofed-ip, real-client-ip"
        # Caddy 가 마지막에 진짜 IP 를 append → last-hop 만 신뢰
        req = _make_request(xff="1.2.3.4, 203.0.113.5", client_host="127.0.0.1")
        assert get_real_ip(req) == "203.0.113.5"

    def test_xff_chain_with_whitespace(self):
        req = _make_request(xff="1.2.3.4 ,  203.0.113.5  ", client_host="127.0.0.1")
        assert get_real_ip(req) == "203.0.113.5"

    def test_no_client_no_xff_returns_none(self):
        req = _make_request(xff=None, client_host=None)
        assert get_real_ip(req) is None

    def test_empty_xff_falls_back_to_client_host(self):
        req = _make_request(xff="", client_host="203.0.113.5")
        assert get_real_ip(req) == "203.0.113.5"

    def test_xff_with_trailing_comma(self):
        # "ip," 같은 비정상 입력 → strip 후 빈 문자열도 그대로 반환
        # (정상 트래픽에선 발생 안 함, 보안 관점 무해 — 빈 IP 는 어차피 매칭 안 됨)
        req = _make_request(xff="203.0.113.5,", client_host="127.0.0.1")
        assert get_real_ip(req) == ""
