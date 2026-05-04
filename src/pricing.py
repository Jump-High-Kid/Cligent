"""
pricing.py — AI API 비용 계산 단일 진실원 (Commit 3, 2026-05-04)

설계:
  - 호출자(ai_client / blog_generator / image_generator)는 토큰 카운터만 넘기고
    USD 환산은 이 모듈이 단일 진실원으로 책임진다.
  - cost_logs.cost_usd 컬럼에 적재될 값을 생성.
  - KRW 변환은 어드민 표시 시점에 환율 적용 (이 모듈은 USD만).

가격표 갱신 절차:
  1. 모델 단가 변경 → ANTHROPIC_PRICES / OPENAI_*_PRICES 상수 수정
  2. 회귀 테스트(test_pricing_anthropic.py + test_pricing_openai.py) 통과 확인
  3. CLAUDE.md 가격 섹션 업데이트

Anthropic 가격표 (2026-05, claude.ai/pricing 공식):
  claude-sonnet-4-6:  $3.00 / $15.00 / $3.75 / $0.30  (in/out/cache_write/cache_read per MTok)
  claude-haiku-4-5:   $0.80 / $4.00  / $1.00 / $0.08

OpenAI 이미지 가격표 (2026-05, OpenAI 공식 cross-checked):
  gpt-image-2     1024x1024  $0.006 / $0.053 / $0.211   (low/medium/high per image)
                  1024x1536  $0.005 / $0.041 / $0.165
                  1536x1024  $0.005 / $0.041 / $0.165
  gpt-image-1.5   1024x1024  $0.009 / $0.034 / $0.133
                  1024x1536  $0.013 / $0.05  / $0.20
                  1536x1024  $0.013 / $0.05  / $0.20

OpenAI 토큰 부가비용 (per MTok, edits 시 reference 이미지가 image_input 토큰으로 가산):
  text_input  $5.00  / cached $1.25  (두 모델 공통)
  image_input $8.00  / cached $2.00  (두 모델 공통)
  image_output                       gpt-image-2 $30 / gpt-image-1.5 $32

청구 정책 (outcome 파라미터, OpenAI 공식 정책 cross-checked):
  - "success"        : per-image + 모든 토큰 billed (정상 완료)
  - "input_blocked"  : per-image=0, image_output=0, input 토큰만 billed (Stage 1)
  - "output_blocked" : per-image=0, input + image_output 토큰 billed (Stage 2 — 이미지
                       생성됐으나 출력 필터에 차단. "still billed" 케이스)
"""

from __future__ import annotations

from typing import Tuple

# ----------------------------------------------------------------------
# Anthropic Claude — per MTok
# ----------------------------------------------------------------------

ANTHROPIC_PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_write_per_mtok": 3.75,
        "cache_read_per_mtok": 0.30,
    },
    "claude-haiku-4-5": {
        "input_per_mtok": 0.80,
        "output_per_mtok": 4.00,
        "cache_write_per_mtok": 1.00,
        "cache_read_per_mtok": 0.08,
    },
}


def calculate_anthropic_cost(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read: int = 0,
    cache_create: int = 0,
) -> float:
    """Anthropic API 호출 1회의 USD 비용을 반환.

    Args:
        model: ANTHROPIC_PRICES 키와 일치해야 함.
        tokens_in: 평문 입력 토큰 (캐시 제외).
        tokens_out: 출력 토큰.
        cache_read: 캐시 hit 토큰 수 (할인된 단가 적용).
        cache_create: 캐시 write 토큰 수 (가산 단가 적용).

    Raises:
        ValueError: 알 수 없는 모델.
    """
    if model not in ANTHROPIC_PRICES:
        raise ValueError(f"Unknown Anthropic model: {model}")
    p = ANTHROPIC_PRICES[model]
    total_micro_usd = (
        tokens_in * p["input_per_mtok"]
        + tokens_out * p["output_per_mtok"]
        + cache_read * p["cache_read_per_mtok"]
        + cache_create * p["cache_write_per_mtok"]
    )
    return total_micro_usd / 1_000_000


# ----------------------------------------------------------------------
# OpenAI Image — per-image (output 단가) + token 단가
# ----------------------------------------------------------------------

# (size, quality) → USD per single image
OPENAI_IMAGE_PRICES: dict[str, dict[Tuple[str, str], float]] = {
    "gpt-image-2": {
        ("1024x1024", "low"): 0.006,
        ("1024x1024", "medium"): 0.053,
        ("1024x1024", "high"): 0.211,
        ("1024x1536", "low"): 0.005,
        ("1024x1536", "medium"): 0.041,
        ("1024x1536", "high"): 0.165,
        ("1536x1024", "low"): 0.005,
        ("1536x1024", "medium"): 0.041,
        ("1536x1024", "high"): 0.165,
    },
    "gpt-image-1.5": {
        ("1024x1024", "low"): 0.009,
        ("1024x1024", "medium"): 0.034,
        ("1024x1024", "high"): 0.133,
        ("1024x1536", "low"): 0.013,
        ("1024x1536", "medium"): 0.05,
        ("1024x1536", "high"): 0.20,
        ("1536x1024", "low"): 0.013,
        ("1536x1024", "medium"): 0.05,
        ("1536x1024", "high"): 0.20,
    },
}

# 토큰 단가 — 두 모델의 입력 단가는 동일, image_output 단가만 다름
OPENAI_TOKEN_PRICES: dict[str, dict[str, float]] = {
    "gpt-image-2": {
        "text_input_per_mtok": 5.00,
        "text_input_cached_per_mtok": 1.25,
        "image_input_per_mtok": 8.00,
        "image_input_cached_per_mtok": 2.00,
        "image_output_per_mtok": 30.00,
    },
    "gpt-image-1.5": {
        "text_input_per_mtok": 5.00,
        "text_input_cached_per_mtok": 1.25,
        "image_input_per_mtok": 8.00,
        "image_input_cached_per_mtok": 2.00,
        "image_output_per_mtok": 32.00,
    },
}

_VALID_OUTCOMES = ("success", "input_blocked", "output_blocked")


def calculate_openai_image_cost(
    model: str,
    size: str,
    quality: str,
    count: int = 1,
    text_input_tokens: int = 0,
    image_input_tokens: int = 0,
    image_output_tokens: int = 0,
    cached_text_input_tokens: int = 0,
    cached_image_input_tokens: int = 0,
    outcome: str = "success",
) -> float:
    """OpenAI 이미지 API 호출 1회의 USD 비용을 반환.

    Args:
        model: OPENAI_IMAGE_PRICES 키 (gpt-image-2 / gpt-image-1.5).
        size: "1024x1024" / "1024x1536" / "1536x1024".
        quality: "low" / "medium" / "high".
        count: 한 호출에 생성된 이미지 수 (success 일 때만 의미).
        text_input_tokens: 프롬프트 토큰 (캐시 제외).
        image_input_tokens: edit 의 reference 이미지 토큰 (캐시 제외).
        image_output_tokens: 생성된 이미지의 토큰화 비용.
        cached_text_input_tokens: 캐시 hit 텍스트 토큰.
        cached_image_input_tokens: 캐시 hit 이미지 토큰.
        outcome: "success" / "input_blocked" / "output_blocked" — 청구 정책 분기.

    Raises:
        ValueError: 알 수 없는 model / (size, quality) / outcome.
    """
    if model not in OPENAI_IMAGE_PRICES:
        raise ValueError(f"Unknown OpenAI image model: {model}")
    if (size, quality) not in OPENAI_IMAGE_PRICES[model]:
        raise ValueError(f"Unknown size/quality for {model}: {size}/{quality}")
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome} (expected one of {_VALID_OUTCOMES})")

    per_img = OPENAI_IMAGE_PRICES[model][(size, quality)]
    tok = OPENAI_TOKEN_PRICES[model]

    # per-image 단가는 success 일 때만 청구
    image_cost = per_img * count if outcome == "success" else 0.0

    # input 토큰은 outcome 무관 모두 청구 (Stage 1·2 모두 모델이 받기는 함)
    token_micro_usd = (
        text_input_tokens * tok["text_input_per_mtok"]
        + cached_text_input_tokens * tok["text_input_cached_per_mtok"]
        + image_input_tokens * tok["image_input_per_mtok"]
        + cached_image_input_tokens * tok["image_input_cached_per_mtok"]
    )

    # image_output 토큰: 실제 생성이 일어났을 때만 청구 (success / output_blocked)
    if outcome in ("success", "output_blocked"):
        token_micro_usd += image_output_tokens * tok["image_output_per_mtok"]

    return image_cost + token_micro_usd / 1_000_000
