"""
test_pricing_openai.py — OpenAI 이미지 비용 계산 회귀 (Commit 3b)

배경 (2026-05-04):
  베타 KPI Commit 3b — gpt-image-2 / gpt-image-1.5 호출의 USD 비용 계산.
  per-image 단가 + 토큰 단가 합산 (edits 의 reference 이미지 토큰 포함).

가격표 (2026-05 기준, OpenAI 공식 cross-checked):
  gpt-image-2 per-image (low/medium/high):
    1024x1024: $0.006 / $0.053 / $0.211
    1024x1536: $0.005 / $0.041 / $0.165
    1536x1024: $0.005 / $0.041 / $0.165
  gpt-image-1.5 per-image:
    1024x1024: $0.009 / $0.034 / $0.133
    1024x1536: $0.013 / $0.05  / $0.20
    1536x1024: $0.013 / $0.05  / $0.20
  토큰 (per MTok, 두 모델 공통 입력 단가):
    text_input $5.00 / cached $1.25
    image_input $8.00 / cached $2.00
    image_output: gpt-image-2 $30 / gpt-image-1.5 $32

청구 정책 (outcome 파라미터):
  - "success"        : per-image + 모든 토큰 billed
  - "input_blocked"  : per-image=0, image_output=0, input 토큰만 billed
  - "output_blocked" : per-image=0, input + image_output 토큰 billed (Stage 2)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_unknown_model_raises():
    from pricing import calculate_openai_image_cost

    with pytest.raises(ValueError):
        calculate_openai_image_cost(
            model="dall-e-99", size="1024x1024", quality="medium"
        )


def test_unknown_size_quality_raises():
    from pricing import calculate_openai_image_cost

    with pytest.raises(ValueError):
        calculate_openai_image_cost(
            model="gpt-image-2", size="4096x4096", quality="high"
        )


def test_invalid_outcome_raises():
    from pricing import calculate_openai_image_cost

    with pytest.raises(ValueError):
        calculate_openai_image_cost(
            model="gpt-image-2",
            size="1024x1024",
            quality="medium",
            outcome="weird",
        )


def test_gpt_image_2_standard_default():
    """Standard 기본 — gpt-image-2 1024x1024 medium 1장 success → $0.053"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2", size="1024x1024", quality="medium"
    )
    assert cost == pytest.approx(0.053, abs=1e-9)


def test_gpt_image_2_pro_high_landscape():
    """Pro — gpt-image-2 1536x1024 high 1장 success → $0.165"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2", size="1536x1024", quality="high"
    )
    assert cost == pytest.approx(0.165, abs=1e-9)


def test_gpt_image_1_5_medium_default():
    """gpt-image-1.5 1024x1024 medium → $0.034 (모델별 단가 분리 검증)"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-1.5", size="1024x1024", quality="medium"
    )
    assert cost == pytest.approx(0.034, abs=1e-9)


def test_count_multiplier():
    """5장 한 번에 — 5 * $0.053 = $0.265"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2", size="1024x1024", quality="medium", count=5
    )
    assert cost == pytest.approx(0.265, abs=1e-9)


def test_text_input_tokens_added():
    """프롬프트 200 토큰 → 0.053 + 200 * $5/1M = 0.053 + 0.001 = $0.054"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        text_input_tokens=200,
    )
    assert cost == pytest.approx(0.054, abs=1e-9)


def test_image_input_tokens_for_edit():
    """edit reference 이미지 1500 토큰 → 0.053 + 1500 * $8/1M = 0.053 + 0.012 = $0.065"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        image_input_tokens=1500,
    )
    assert cost == pytest.approx(0.065, abs=1e-9)


def test_cached_text_cheaper_than_uncached():
    """캐시 hit 단가 ($1.25) 가 평문 ($5) 보다 1/4 수준 — 1000 토큰만 비교"""
    from pricing import calculate_openai_image_cost

    uncached = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        text_input_tokens=1000,
    )
    cached = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        cached_text_input_tokens=1000,
    )
    # uncached - 0.053 = 1000 * 5 / 1M = 0.005
    # cached - 0.053 = 1000 * 1.25 / 1M = 0.00125
    assert (uncached - cached) == pytest.approx(0.005 - 0.00125, abs=1e-9)


def test_image_output_tokens_added():
    """image_output 2000 토큰 (gpt-image-2 $30/MTok) → 0.053 + 0.06 = $0.113"""
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        image_output_tokens=2000,
    )
    assert cost == pytest.approx(0.113, abs=1e-9)


def test_outcome_input_blocked_only_input_billed():
    """Stage 1 (입력 모더레이션) — per-image=0, image_output=0, input 만 billed.
    text_input 200 → 200 * $5/1M = $0.001 only.
    """
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        text_input_tokens=200,
        image_output_tokens=2000,  # 무시되어야 함
        outcome="input_blocked",
    )
    assert cost == pytest.approx(0.001, abs=1e-9)


def test_outcome_output_blocked_billed_no_image():
    """Stage 2 (출력 필터) — per-image=0, but input + image_output billed.
    text_input 200 + image_output 2000:
      0 (no per-image) + 0.001 + 0.06 = $0.061
    """
    from pricing import calculate_openai_image_cost

    cost = calculate_openai_image_cost(
        model="gpt-image-2",
        size="1024x1024",
        quality="medium",
        text_input_tokens=200,
        image_output_tokens=2000,
        outcome="output_blocked",
    )
    assert cost == pytest.approx(0.061, abs=1e-9)


def test_openai_image_prices_constant_shape():
    """OPENAI_IMAGE_PRICES — 두 모델 모두 9개 (size, quality) 콤보 보유"""
    from pricing import OPENAI_IMAGE_PRICES

    expected_combos = {
        ("1024x1024", "low"), ("1024x1024", "medium"), ("1024x1024", "high"),
        ("1024x1536", "low"), ("1024x1536", "medium"), ("1024x1536", "high"),
        ("1536x1024", "low"), ("1536x1024", "medium"), ("1536x1024", "high"),
    }
    for model in ("gpt-image-2", "gpt-image-1.5"):
        assert model in OPENAI_IMAGE_PRICES
        assert set(OPENAI_IMAGE_PRICES[model].keys()) == expected_combos


def test_openai_token_prices_constant_shape():
    """OPENAI_TOKEN_PRICES — 두 모델 모두 5개 토큰 종류 보유"""
    from pricing import OPENAI_TOKEN_PRICES

    expected_keys = {
        "text_input_per_mtok",
        "text_input_cached_per_mtok",
        "image_input_per_mtok",
        "image_input_cached_per_mtok",
        "image_output_per_mtok",
    }
    for model in ("gpt-image-2", "gpt-image-1.5"):
        assert model in OPENAI_TOKEN_PRICES
        assert set(OPENAI_TOKEN_PRICES[model].keys()) == expected_keys
