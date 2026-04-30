"""
metadata_generator 단위 테스트 (Phase 3, 2026-04-30)

검증:
  - JSON 파싱: 깨끗한 JSON / 코드펜스 둘러싼 JSON / 서론 섞인 JSON
  - 필드 검증: 필수 필드 누락, 잘못된 타입
  - 길이 가드: title 60자 / summary 200자 / og_desc 160자 / tags 7개
  - 빈 본문 거부
  - 본문 5000자 초과 시 snippet 압축
  - generate_metadata_safe: fail-soft 동작
  - Haiku 모델·prompt caching 파라미터 전달 확인
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import metadata_generator as mg  # noqa: E402
from ai_client import AIClientError, AIResponse  # noqa: E402
from metadata_generator import (  # noqa: E402
    BlogMetadata,
    HAIKU_MODEL,
    _extract_json,
    _validate_meta,
    generate_metadata,
    generate_metadata_safe,
)


# ── 헬퍼 ─────────────────────────────────────────────────


def _ok_response(payload: dict) -> AIResponse:
    import json

    return AIResponse(content=json.dumps(payload, ensure_ascii=False), usage={})


_VALID_PAYLOAD = {
    "title": "허리 디스크, 한방 추나 치료로 호전된 사례",
    "tags": ["허리디스크", "추나치료", "한방", "디스크치료", "한의원"],
    "summary": "허리 디스크는 추간판 탈출로 인한 신경 압박입니다. 한방 추나 치료는 척추 정렬을 회복합니다.",
    "og_description": "허리 디스크로 고생하시나요? 한방 추나 치료로 수술 없이 회복한 사례를 소개합니다.",
}


# ── _extract_json ────────────────────────────────────────


class TestExtractJson:
    def test_clean_json(self):
        raw = '{"title": "test"}'
        assert _extract_json(raw) == {"title": "test"}

    def test_with_code_fence(self):
        raw = '```json\n{"title": "test"}\n```'
        assert _extract_json(raw) == {"title": "test"}

    def test_with_intro_text(self):
        raw = '여기 JSON입니다:\n{"title": "test", "tags": ["a"]}\n끝.'
        assert _extract_json(raw) == {"title": "test", "tags": ["a"]}

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _extract_json("그냥 텍스트")

    def test_malformed_raises(self):
        with pytest.raises(Exception):  # JSONDecodeError
            _extract_json('{"title":')


# ── _validate_meta ───────────────────────────────────────


class TestValidateMeta:
    def test_valid_passes(self):
        _validate_meta(_VALID_PAYLOAD)

    def test_missing_field(self):
        bad = {**_VALID_PAYLOAD}
        del bad["title"]
        with pytest.raises(ValueError):
            _validate_meta(bad)

    def test_empty_title(self):
        with pytest.raises(ValueError):
            _validate_meta({**_VALID_PAYLOAD, "title": "  "})

    def test_tags_must_be_list(self):
        with pytest.raises(ValueError):
            _validate_meta({**_VALID_PAYLOAD, "tags": "tag1,tag2"})

    def test_tags_must_be_strings(self):
        with pytest.raises(ValueError):
            _validate_meta({**_VALID_PAYLOAD, "tags": ["ok", 123]})


# ── generate_metadata ────────────────────────────────────


class TestGenerateMetadata:
    def test_success_returns_metadata(self, monkeypatch):
        monkeypatch.setattr(
            mg, "call_anthropic_messages", lambda **kw: _ok_response(_VALID_PAYLOAD)
        )
        result = generate_metadata("본문 텍스트", keyword="허리디스크")
        assert isinstance(result, BlogMetadata)
        assert result.title == _VALID_PAYLOAD["title"]
        assert len(result.tags) == 5
        assert "추나" in result.summary

    def test_uses_haiku_model_with_caching(self, monkeypatch):
        captured = {}

        def fake_call(**kwargs):
            captured.update(kwargs)
            return _ok_response(_VALID_PAYLOAD)

        monkeypatch.setattr(mg, "call_anthropic_messages", fake_call)
        generate_metadata("본문", keyword="kw")

        assert captured["model"] == HAIKU_MODEL
        assert captured["cache_system"] is True
        assert captured["max_tokens"] == 400

    def test_keyword_in_user_message(self, monkeypatch):
        captured = {}

        def fake_call(**kwargs):
            captured.update(kwargs)
            return _ok_response(_VALID_PAYLOAD)

        monkeypatch.setattr(mg, "call_anthropic_messages", fake_call)
        generate_metadata("본문", keyword="허리디스크", seo_keywords=["추나치료"])

        assert "허리디스크" in captured["user"]
        assert "추나치료" in captured["user"]

    def test_long_content_compressed(self, monkeypatch):
        """5000자 초과 → 앞 4000 + 끝 1000으로 snippet 압축."""
        captured = {}

        def fake_call(**kwargs):
            captured.update(kwargs)
            return _ok_response(_VALID_PAYLOAD)

        monkeypatch.setattr(mg, "call_anthropic_messages", fake_call)

        # 6000자 본문 (앞 a-only, 끝 z-only로 구분 가능)
        body = "a" * 4500 + "MIDDLE" + "z" * 1500
        generate_metadata(body, keyword="x")

        user_msg = captured["user"]
        assert "[...중략...]" in user_msg
        assert "MIDDLE" not in user_msg  # 중간 부분은 잘림

    def test_empty_body_rejected(self):
        with pytest.raises(AIClientError) as exc:
            generate_metadata("")
        assert exc.value.kind == "bad_request"

    def test_invalid_json_raises_value_error(self, monkeypatch):
        monkeypatch.setattr(
            mg,
            "call_anthropic_messages",
            lambda **kw: AIResponse(content="not json at all", usage={}),
        )
        with pytest.raises(ValueError):
            generate_metadata("본문")

    def test_missing_field_raises(self, monkeypatch):
        bad = {**_VALID_PAYLOAD}
        del bad["tags"]
        monkeypatch.setattr(
            mg, "call_anthropic_messages", lambda **kw: _ok_response(bad)
        )
        with pytest.raises(ValueError):
            generate_metadata("본문")

    def test_length_clamps_applied(self, monkeypatch):
        long_payload = {
            "title": "ㄱ" * 100,  # 100자 → 60자로 잘림
            "tags": ["t"] * 10,  # 10개 → 7개로 잘림
            "summary": "ㄴ" * 300,  # 300자 → 200자
            "og_description": "ㄷ" * 200,  # 200자 → 160자
        }
        monkeypatch.setattr(
            mg, "call_anthropic_messages", lambda **kw: _ok_response(long_payload)
        )
        result = generate_metadata("본문")

        assert len(result.title) == 60
        assert len(result.tags) == 7
        assert len(result.summary) == 200
        assert len(result.og_description) == 160


# ── generate_metadata_safe ───────────────────────────────


class TestGenerateMetadataSafe:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            mg, "call_anthropic_messages", lambda **kw: _ok_response(_VALID_PAYLOAD)
        )
        result = generate_metadata_safe("본문")
        assert result is not None
        assert result.title == _VALID_PAYLOAD["title"]

    def test_returns_none_on_api_error(self, monkeypatch):
        def fail(**kw):
            raise AIClientError("rate_limit", "limit", 429)

        monkeypatch.setattr(mg, "call_anthropic_messages", fail)
        assert generate_metadata_safe("본문") is None

    def test_returns_none_on_value_error(self, monkeypatch):
        monkeypatch.setattr(
            mg,
            "call_anthropic_messages",
            lambda **kw: AIResponse(content="garbage", usage={}),
        )
        assert generate_metadata_safe("본문") is None
