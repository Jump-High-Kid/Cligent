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

OpenAI 가격표는 Commit 3b 에서 추가.
"""

from __future__ import annotations

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
