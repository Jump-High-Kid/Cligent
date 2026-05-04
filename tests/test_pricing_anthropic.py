"""
test_pricing_anthropic.py — Anthropic 비용 계산 회귀 (Commit 3a)

배경 (2026-05-04):
  베타 KPI Commit 3 — cost_logs 테이블에 적재할 USD 비용을 계산하는
  순수 함수. 호출자(ai_client / blog_generator)는 토큰 카운터만 넘기고
  USD 환산은 이 모듈이 단일 진실원으로 책임진다.

가격표 (2026-05 기준, claude.ai/pricing 공식):
  claude-sonnet-4-6:  $3.00 in / $15.00 out / $3.75 cache_write / $0.30 cache_read  (per MTok)
  claude-haiku-4-5:   $0.80 in / $4.00 out / $1.00 cache_write / $0.08 cache_read   (per MTok)

검증 항목:
  1. 알 수 없는 모델 → ValueError
  2. sonnet 평문 in/out 만
  3. sonnet 캐시 hit + write 가산
  4. haiku 평문
  5. 모든 토큰 0 → $0.00
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_unknown_model_raises():
    from pricing import calculate_anthropic_cost

    with pytest.raises(ValueError):
        calculate_anthropic_cost(
            model="claude-opus-99",
            tokens_in=100,
            tokens_out=100,
        )


def test_sonnet_basic_no_cache():
    """sonnet 평문 호출 — 1500 in + 3000 out
    expected: (1500 * 3 + 3000 * 15) / 1_000_000 = 49500 / 1M = $0.0495
    """
    from pricing import calculate_anthropic_cost

    cost = calculate_anthropic_cost(
        model="claude-sonnet-4-6",
        tokens_in=1500,
        tokens_out=3000,
    )
    assert cost == pytest.approx(0.0495, abs=1e-9)


def test_sonnet_with_cache():
    """sonnet + 캐시 — 1000 in + 2000 out + 500 cache_read + 200 cache_create
    expected: (1000*3 + 2000*15 + 500*0.30 + 200*3.75) / 1M
            = (3000 + 30000 + 150 + 750) / 1M
            = 33900 / 1M = $0.0339
    """
    from pricing import calculate_anthropic_cost

    cost = calculate_anthropic_cost(
        model="claude-sonnet-4-6",
        tokens_in=1000,
        tokens_out=2000,
        cache_read=500,
        cache_create=200,
    )
    assert cost == pytest.approx(0.0339, abs=1e-9)


def test_haiku_basic():
    """haiku — 5000 in + 1000 out
    expected: (5000 * 0.80 + 1000 * 4.00) / 1M = 8000 / 1M = $0.008
    """
    from pricing import calculate_anthropic_cost

    cost = calculate_anthropic_cost(
        model="claude-haiku-4-5",
        tokens_in=5000,
        tokens_out=1000,
    )
    assert cost == pytest.approx(0.008, abs=1e-9)


def test_zero_tokens_returns_zero():
    """모든 토큰 0 → $0.00 (defensive)"""
    from pricing import calculate_anthropic_cost

    cost = calculate_anthropic_cost(
        model="claude-sonnet-4-6",
        tokens_in=0,
        tokens_out=0,
    )
    assert cost == 0.0


def test_anthropic_prices_constant_shape():
    """ANTHROPIC_PRICES 가 두 모델 모두 4개 키(in/out/cache_write/cache_read) 보유"""
    from pricing import ANTHROPIC_PRICES

    expected_keys = {"input_per_mtok", "output_per_mtok", "cache_write_per_mtok", "cache_read_per_mtok"}
    for model in ("claude-sonnet-4-6", "claude-haiku-4-5"):
        assert model in ANTHROPIC_PRICES
        assert set(ANTHROPIC_PRICES[model].keys()) == expected_keys
