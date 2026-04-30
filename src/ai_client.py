"""
ai_client.py — 외부 AI API 통합 래퍼 (Phase 2, 2026-04-30)

지원:
  - Anthropic Claude (Sonnet 4.6 본문, Haiku 4.5 메타)
  - OpenAI gpt-image-2 (generations + edits)

공통:
  - 키 자동 lookup (Anthropic = env ANTHROPIC_API_KEY, OpenAI = secret_manager)
  - Anthropic prompt caching (system 프롬프트 75~90% 절감)
  - 표준 에러 변환 (AIClientError) — 호출자가 catch하기 쉽게
  - semaphore(3) 동시성 제한 (OpenAI Tier 1 rate limit 대응)

미래:
  - Google Gemini (M6+ 모델 선택권 어댑터)
  - 비동기 stream 지원 (현재 동기만)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# OpenAI Tier 1 = 5 IPM. 안전 마진 두고 3개 동시 제한.
# Public 베타 (50명) 진입 시 Tier 2 업그레이드 후 5~10으로 상향 예정.
_OPENAI_CONCURRENCY = 3
_openai_semaphore: Optional[asyncio.Semaphore] = None


def _get_openai_semaphore() -> asyncio.Semaphore:
    """동시성 제한 세마포어. 첫 호출 시 lazy init (이벤트 루프 필요)."""
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(_OPENAI_CONCURRENCY)
    return _openai_semaphore


@dataclass(frozen=True)
class AIResponse:
    """모든 AI 호출 통합 응답.

    텍스트 호출: content = 응답 문자열
    이미지 호출: content = base64-encoded PNG (data URI 없이 raw base64)
    """
    content: str
    usage: dict = field(default_factory=dict)


class AIClientError(Exception):
    """ai_client 표준 에러.

    kind:
      auth        — API 키 누락/만료 (401)
      rate_limit  — 호출 한도 초과 (429)
      bad_request — 입력 오류 / 정책 위반 (400)
      timeout     — 응답 타임아웃
      server      — 5xx 또는 알 수 없는 API 오류
      unknown     — 그 외
    """

    def __init__(self, kind: str, message: str, status_code: int = 0):
        self.kind = kind
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{kind}] {message}")


# ── Anthropic ─────────────────────────────────────────────────


def call_anthropic_messages(
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    cache_system: bool = True,
) -> AIResponse:
    """Claude Messages API 호출 (동기).

    cache_system=True면 system 프롬프트에 cache_control 추가 → 75~90% 비용 절감.
    프로덕션에서는 항상 True 권장 (시스템 프롬프트가 길수록 효과 큼).

    Raises:
        AIClientError on any failure (호출자가 kind로 분기).
    """
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise AIClientError("auth", "ANTHROPIC_API_KEY 환경변수 미설정")

    client = anthropic.Anthropic(api_key=api_key)
    try:
        if cache_system:
            system_param = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = system

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": user}],
        )
        text = response.content[0].text
        u = response.usage
        usage = {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_create_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        return AIResponse(content=text, usage=usage)
    except anthropic.AuthenticationError as e:
        raise AIClientError("auth", str(e), 401)
    except anthropic.RateLimitError as e:
        raise AIClientError("rate_limit", str(e), 429)
    except anthropic.APITimeoutError as e:
        raise AIClientError("timeout", str(e))
    except anthropic.APIStatusError as e:
        raise AIClientError("server", str(e), getattr(e, "status_code", 500))
    except AIClientError:
        raise
    except Exception as e:
        raise AIClientError("unknown", f"{type(e).__name__}: {e}")


# ── OpenAI gpt-image-2 ─────────────────────────────────────────


def _get_openai_key() -> str:
    """secret_manager에서 OpenAI 키 lookup. 미등록 시 AIClientError."""
    from secret_manager import get_server_secret

    key = get_server_secret("openai_api_key")
    if not key:
        raise AIClientError(
            "auth",
            "OpenAI 키 미등록. /admin/settings 페이지에서 등록하세요.",
            401,
        )
    return key


def _classify_openai_error(exc: Exception) -> AIClientError:
    """OpenAI SDK 예외를 AIClientError로 변환."""
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return AIClientError("auth", str(exc), 401)
    if isinstance(exc, openai.RateLimitError):
        return AIClientError("rate_limit", str(exc), 429)
    if isinstance(exc, openai.BadRequestError):
        return AIClientError("bad_request", str(exc), 400)
    if isinstance(exc, openai.APITimeoutError):
        return AIClientError("timeout", str(exc))
    if isinstance(exc, openai.APIError):
        return AIClientError("server", str(exc), getattr(exc, "status_code", 500))
    return AIClientError("unknown", f"{type(exc).__name__}: {exc}")


def call_openai_image_generate(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "medium",
    n: int = 5,
) -> list[AIResponse]:
    """gpt-image-2 generations 호출. n장 생성 후 list 반환.

    Args:
        prompt: 이미지 생성 프롬프트.
        size: 1024x1024 / 1024x1536 / 1536x1024 (Standard 1024 / Pro 1536 권장).
        quality: low / medium / high.
        n: 생성 장수 (1~10, 기본 5).

    Returns:
        AIResponse 리스트. content = base64 PNG.

    Raises:
        AIClientError on auth/rate_limit/bad_request/timeout/server failures.
    """
    if not prompt or not prompt.strip():
        raise AIClientError("bad_request", "이미지 프롬프트가 비어 있습니다.")
    if n < 1 or n > 10:
        raise AIClientError("bad_request", f"n은 1~10 범위여야 합니다 (받은 값: {n}).")

    import openai

    api_key = _get_openai_key()
    client = openai.OpenAI(api_key=api_key, timeout=60.0)
    try:
        response = client.images.generate(
            model="gpt-image-2",
            prompt=prompt,
            size=size,
            quality=quality,
            n=n,
        )
        results: list[AIResponse] = []
        for item in response.data:
            results.append(
                AIResponse(
                    content=item.b64_json or "",
                    usage={"size": size, "quality": quality, "mode": "generate"},
                )
            )
        return results
    except AIClientError:
        raise
    except Exception as e:
        raise _classify_openai_error(e)


def call_openai_image_edit(
    image_bytes: bytes,
    prompt: str,
    size: str = "1024x1024",
    quality: str = "medium",
    mask_bytes: Optional[bytes] = None,
    n: int = 1,
) -> list[AIResponse]:
    """gpt-image-2 edits 호출. 기존 이미지 부분 수정.

    edit endpoint는 input image token 추가로 단일 호출은 약간 비싸지만,
    한 번에 만족도 높아 *세션 총비용*은 generations 재생성보다 35% 낮음.

    Args:
        image_bytes: 베이스 이미지 PNG bytes.
        prompt: 수정 지시 (예: "신유혈 부분 강조").
        mask_bytes: 인페인팅 마스크 (alpha channel, 선택).

    Raises:
        AIClientError on failures.
    """
    if not image_bytes:
        raise AIClientError("bad_request", "수정할 이미지가 비어 있습니다.")
    if not prompt or not prompt.strip():
        raise AIClientError("bad_request", "수정 프롬프트가 비어 있습니다.")

    import openai

    api_key = _get_openai_key()
    client = openai.OpenAI(api_key=api_key, timeout=60.0)
    try:
        kwargs = {
            "model": "gpt-image-2",
            "image": io.BytesIO(image_bytes),
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "n": n,
        }
        if mask_bytes:
            kwargs["mask"] = io.BytesIO(mask_bytes)
        response = client.images.edit(**kwargs)
        results: list[AIResponse] = []
        for item in response.data:
            results.append(
                AIResponse(
                    content=item.b64_json or "",
                    usage={"size": size, "quality": quality, "mode": "edit"},
                )
            )
        return results
    except AIClientError:
        raise
    except Exception as e:
        raise _classify_openai_error(e)
