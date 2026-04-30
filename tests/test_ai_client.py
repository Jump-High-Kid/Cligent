"""
ai_client 단위 테스트 (Phase 2, 2026-04-30)

목표:
  - Anthropic / OpenAI 호출이 표준 AIClientError로 정확히 변환되는지
  - prompt caching 파라미터가 SDK에 제대로 전달되는지
  - 입력 검증(빈 프롬프트, n 범위 등) 동작
  - secret_manager가 OpenAI 키 lookup 경로로 사용되는지
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from src import ai_client  # noqa: E402
from src.ai_client import (  # noqa: E402
    AIClientError,
    AIResponse,
    call_anthropic_messages,
    call_openai_image_edit,
    call_openai_image_generate,
)


# ── 헬퍼: 가짜 anthropic / openai 모듈 주입 ──────────────────


class _FakeAnthropicUsage:
    def __init__(self):
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_read_input_tokens = 80
        self.cache_creation_input_tokens = 20


class _FakeAnthropicResponse:
    def __init__(self, text: str = "응답 본문"):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = _FakeAnthropicUsage()


class _FakeAnthropicClient:
    """anthropic.Anthropic 대체. messages.create 호출을 캡처."""

    last_call_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        self.messages = self
        type(self).last_call_kwargs = {}

    def create(self, **kwargs):
        type(self).last_call_kwargs = kwargs
        return _FakeAnthropicResponse()


def _install_fake_anthropic(monkeypatch, raise_kind=None):
    """anthropic 모듈 mock. raise_kind 지정 시 해당 예외 발생."""
    fake_mod = types.ModuleType("anthropic")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg)
            self.status_code = status_code

    fake_mod.AuthenticationError = AuthenticationError
    fake_mod.RateLimitError = RateLimitError
    fake_mod.APITimeoutError = APITimeoutError
    fake_mod.APIStatusError = APIStatusError

    err_map = {
        "auth": AuthenticationError("invalid key"),
        "rate_limit": RateLimitError("429"),
        "timeout": APITimeoutError("slow"),
        "server": APIStatusError("boom", status_code=503),
    }

    class _Client(_FakeAnthropicClient):
        def create(self, **kwargs):
            type(self).last_call_kwargs = kwargs
            if raise_kind:
                raise err_map[raise_kind]
            return _FakeAnthropicResponse()

    fake_mod.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    return _Client


# ── Anthropic ─────────────────────────────────────────────


class TestAnthropic:
    def test_no_api_key_raises_auth(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(AIClientError) as exc:
            call_anthropic_messages("claude-sonnet-4-6", "sys", "hi")
        assert exc.value.kind == "auth"

    def test_success_with_cache_control(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client_cls = _install_fake_anthropic(monkeypatch)

        resp = call_anthropic_messages(
            "claude-sonnet-4-6", "system text", "user text", cache_system=True
        )

        assert isinstance(resp, AIResponse)
        assert resp.content == "응답 본문"
        assert resp.usage["input_tokens"] == 100
        assert resp.usage["cache_read_tokens"] == 80
        assert resp.usage["cache_create_tokens"] == 20

        # system 파라미터에 cache_control 포함
        kwargs = client_cls.last_call_kwargs
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["text"] == "system text"

    def test_success_without_cache(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        client_cls = _install_fake_anthropic(monkeypatch)

        call_anthropic_messages("claude-sonnet-4-6", "sys", "hi", cache_system=False)
        kwargs = client_cls.last_call_kwargs
        # cache_system=False면 system이 문자열 그대로
        assert kwargs["system"] == "sys"

    @pytest.mark.parametrize(
        "raise_kind,expected",
        [
            ("auth", "auth"),
            ("rate_limit", "rate_limit"),
            ("timeout", "timeout"),
            ("server", "server"),
        ],
    )
    def test_error_classification(self, monkeypatch, raise_kind, expected):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        _install_fake_anthropic(monkeypatch, raise_kind=raise_kind)

        with pytest.raises(AIClientError) as exc:
            call_anthropic_messages("claude-sonnet-4-6", "s", "u")
        assert exc.value.kind == expected


# ── OpenAI image generate ────────────────────────────────


class _FakeOpenAIImageItem:
    def __init__(self, b64="ZmFrZQ=="):
        self.b64_json = b64


class _FakeOpenAIImageResponse:
    def __init__(self, n=1):
        self.data = [_FakeOpenAIImageItem() for _ in range(n)]


def _install_fake_openai(monkeypatch, raise_kind=None, n_images=1):
    """openai 모듈 mock."""
    fake_mod = types.ModuleType("openai")

    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class BadRequestError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIError(Exception):
        def __init__(self, msg, status_code=500):
            super().__init__(msg)
            self.status_code = status_code

    fake_mod.AuthenticationError = AuthenticationError
    fake_mod.RateLimitError = RateLimitError
    fake_mod.BadRequestError = BadRequestError
    fake_mod.APITimeoutError = APITimeoutError
    fake_mod.APIError = APIError

    err_map = {
        "auth": AuthenticationError("bad key"),
        "rate_limit": RateLimitError("limit"),
        "bad_request": BadRequestError("nope"),
        "timeout": APITimeoutError("slow"),
        "server": APIError("boom", status_code=502),
    }

    class _Images:
        last_kwargs = {}

        def generate(self, **kwargs):
            type(self).last_kwargs = kwargs
            if raise_kind:
                raise err_map[raise_kind]
            return _FakeOpenAIImageResponse(n=n_images)

        def edit(self, **kwargs):
            type(self).last_kwargs = kwargs
            if raise_kind:
                raise err_map[raise_kind]
            return _FakeOpenAIImageResponse(n=n_images)

    class _OpenAI:
        last_init_kwargs = {}

        def __init__(self, **kwargs):
            type(self).last_init_kwargs = kwargs
            self.images = _Images()

    fake_mod.OpenAI = _OpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_mod)
    return _OpenAI, _Images


class TestOpenAIImageGenerate:
    def test_empty_prompt_rejected(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        with pytest.raises(AIClientError) as exc:
            call_openai_image_generate("")
        assert exc.value.kind == "bad_request"

    def test_n_out_of_range_rejected(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        with pytest.raises(AIClientError) as exc:
            call_openai_image_generate("프롬프트", n=0)
        assert exc.value.kind == "bad_request"

        with pytest.raises(AIClientError) as exc:
            call_openai_image_generate("프롬프트", n=11)
        assert exc.value.kind == "bad_request"

    def test_missing_openai_key_raises_auth(self, monkeypatch):
        # secret_manager 자리에 미등록 상태 흉내
        def _no_key(name):
            return None

        # _get_openai_key 내부의 from secret_manager import get_server_secret 모킹
        fake_sm = types.ModuleType("secret_manager")
        fake_sm.get_server_secret = _no_key
        monkeypatch.setitem(sys.modules, "secret_manager", fake_sm)

        with pytest.raises(AIClientError) as exc:
            ai_client._get_openai_key()
        assert exc.value.kind == "auth"

    def test_generate_success_returns_list(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        _install_fake_openai(monkeypatch, n_images=5)

        results = call_openai_image_generate("프롬프트", n=5)
        assert len(results) == 5
        assert all(isinstance(r, AIResponse) for r in results)
        assert results[0].content == "ZmFrZQ=="
        assert results[0].usage["mode"] == "generate"

    def test_generate_passes_size_quality(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        _, images_cls = _install_fake_openai(monkeypatch, n_images=1)

        call_openai_image_generate(
            "프롬프트", size="1536x1024", quality="high", n=1
        )
        kwargs = images_cls.last_kwargs
        assert kwargs["size"] == "1536x1024"
        assert kwargs["quality"] == "high"
        assert kwargs["model"] == "gpt-image-2"

    @pytest.mark.parametrize(
        "raise_kind,expected",
        [
            ("auth", "auth"),
            ("rate_limit", "rate_limit"),
            ("bad_request", "bad_request"),
            ("timeout", "timeout"),
            ("server", "server"),
        ],
    )
    def test_generate_error_classification(self, monkeypatch, raise_kind, expected):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        _install_fake_openai(monkeypatch, raise_kind=raise_kind)

        with pytest.raises(AIClientError) as exc:
            call_openai_image_generate("프롬프트", n=1)
        assert exc.value.kind == expected


# ── OpenAI image edit ────────────────────────────────────


class TestOpenAIImageEdit:
    def test_empty_image_rejected(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        with pytest.raises(AIClientError) as exc:
            call_openai_image_edit(b"", "프롬프트")
        assert exc.value.kind == "bad_request"

    def test_empty_prompt_rejected(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        with pytest.raises(AIClientError) as exc:
            call_openai_image_edit(b"PNGDATA", "")
        assert exc.value.kind == "bad_request"

    def test_edit_success(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        _, images_cls = _install_fake_openai(monkeypatch, n_images=1)

        results = call_openai_image_edit(
            image_bytes=b"PNG_RAW", prompt="신유혈 강조", n=1
        )
        assert len(results) == 1
        assert results[0].usage["mode"] == "edit"
        kwargs = images_cls.last_kwargs
        assert kwargs["model"] == "gpt-image-2"
        # image / mask는 BytesIO로 감싸짐
        assert "image" in kwargs

    def test_edit_with_mask(self, monkeypatch):
        monkeypatch.setattr(ai_client, "_get_openai_key", lambda: "sk-test")
        _, images_cls = _install_fake_openai(monkeypatch, n_images=1)

        call_openai_image_edit(
            image_bytes=b"PNG", prompt="prompt", mask_bytes=b"MASK", n=1
        )
        kwargs = images_cls.last_kwargs
        assert "mask" in kwargs


# ── 데이터 클래스 ────────────────────────────────────────


class TestAIResponse:
    def test_frozen_dataclass(self):
        r = AIResponse(content="x", usage={"k": 1})
        with pytest.raises(Exception):  # FrozenInstanceError
            r.content = "y"  # type: ignore[misc]

    def test_default_usage(self):
        r = AIResponse(content="x")
        assert r.usage == {}


class TestAIClientError:
    def test_kind_in_message(self):
        e = AIClientError("rate_limit", "too fast", 429)
        assert "[rate_limit]" in str(e)
        assert e.kind == "rate_limit"
        assert e.status_code == 429
