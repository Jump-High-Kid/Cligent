"""
image_generator 단위 테스트 (Phase 2, 2026-04-30)

검증:
  - 플랜별 한도(Standard 1+2 / Pro 2+4) 적용
  - 플랜별 해상도 (1024 medium / 1536 high)
  - 초기 5장 생성, 재생성 5장, edit 1장
  - quota 초과 시 ImageQuotaExceeded
  - 입력 검증 (빈 프롬프트, 빈 이미지)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import image_generator as ig  # noqa: E402
from ai_client import AIClientError, AIResponse  # noqa: E402
from image_generator import (  # noqa: E402
    INITIAL_SET_SIZE,
    ImageQuotaExceeded,
    ImageSet,
    edit_image,
    generate_initial_set,
    get_plan_dimensions,
    get_plan_limits,
    get_quota_status,
    normalize_plan_id,
    regenerate_set,
)


# ── 헬퍼 ─────────────────────────────────────────────────


def _fake_responses(n: int) -> list[AIResponse]:
    return [
        AIResponse(content=f"BASE64_{i}", usage={"mode": "test"})
        for i in range(n)
    ]


# ── 플랜 정책 ────────────────────────────────────────────


class TestPlanPolicy:
    def test_normalize_unknown_to_free(self):
        assert normalize_plan_id(None) == "free"
        assert normalize_plan_id("enterprise") == "free"
        assert normalize_plan_id("standard") == "standard"
        assert normalize_plan_id("pro") == "pro"
        assert normalize_plan_id("trial") == "trial"

    def test_standard_limits(self):
        limits = get_plan_limits("standard")
        assert limits == {"regen_free": 1, "edit_free": 2}

    def test_pro_limits(self):
        limits = get_plan_limits("pro")
        assert limits == {"regen_free": 2, "edit_free": 4}

    def test_trial_treated_as_standard(self):
        assert get_plan_limits("trial") == get_plan_limits("standard")

    def test_free_has_zero_quota(self):
        assert get_plan_limits("free") == {"regen_free": 0, "edit_free": 0}

    def test_standard_dimensions(self):
        assert get_plan_dimensions("standard") == ("1024x1024", "medium")

    def test_pro_dimensions(self):
        assert get_plan_dimensions("pro") == ("1536x1024", "high")


# ── 초기 5장 생성 ─────────────────────────────────────────


class TestGenerateInitialSet:
    def test_creates_five_images(self):
        with patch.object(
            ig, "call_openai_image_generate", return_value=_fake_responses(5)
        ) as mock_gen:
            result = generate_initial_set("프롬프트", "standard")

        assert isinstance(result, ImageSet)
        assert len(result.images) == 5
        assert result.images[0] == "BASE64_0"
        assert result.mode == "initial"
        assert result.plan_id == "standard"

        # ai_client에 정확한 파라미터 전달
        mock_gen.assert_called_once_with(
            prompt="프롬프트", size="1024x1024", quality="medium", n=5
        )

    def test_pro_uses_high_resolution(self):
        with patch.object(
            ig, "call_openai_image_generate", return_value=_fake_responses(5)
        ) as mock_gen:
            result = generate_initial_set("프롬프트", "pro")

        assert result.size == "1536x1024"
        assert result.quality == "high"
        mock_gen.assert_called_once_with(
            prompt="프롬프트", size="1536x1024", quality="high", n=5
        )

    def test_empty_prompt_rejected(self):
        with pytest.raises(AIClientError) as exc:
            generate_initial_set("", "standard")
        assert exc.value.kind == "bad_request"

    def test_initial_no_quota_check(self):
        # initial은 카운터 없이 항상 생성 (무료 한도 무관)
        with patch.object(
            ig, "call_openai_image_generate", return_value=_fake_responses(5)
        ):
            result = generate_initial_set("프롬프트", "free")
        assert len(result.images) == 5


# ── 재생성 (regen) 한도 ───────────────────────────────────


class TestRegenerate:
    def test_standard_first_regen_allowed(self):
        with patch.object(
            ig, "call_openai_image_generate", return_value=_fake_responses(5)
        ):
            result = regenerate_set("프롬프트", "standard", regen_used=0)
        assert result.mode == "regen"
        assert len(result.images) == INITIAL_SET_SIZE

    def test_standard_second_regen_blocked(self):
        with pytest.raises(ImageQuotaExceeded) as exc:
            regenerate_set("프롬프트", "standard", regen_used=1)
        assert exc.value.kind == "regen"
        assert exc.value.plan_id == "standard"
        assert exc.value.used == 1
        assert exc.value.limit == 1

    def test_pro_two_regens_allowed(self):
        with patch.object(
            ig, "call_openai_image_generate", return_value=_fake_responses(5)
        ):
            # 0회·1회는 통과
            regenerate_set("프롬프트", "pro", regen_used=0)
            regenerate_set("프롬프트", "pro", regen_used=1)

    def test_pro_third_regen_blocked(self):
        with pytest.raises(ImageQuotaExceeded) as exc:
            regenerate_set("프롬프트", "pro", regen_used=2)
        assert exc.value.limit == 2

    def test_free_blocks_regen_immediately(self):
        with pytest.raises(ImageQuotaExceeded) as exc:
            regenerate_set("프롬프트", "free", regen_used=0)
        assert exc.value.limit == 0

    def test_regen_empty_prompt_rejected(self):
        with pytest.raises(AIClientError) as exc:
            regenerate_set("", "standard", regen_used=0)
        assert exc.value.kind == "bad_request"


# ── 이미지 수정 (edit) 한도 ───────────────────────────────


class TestEditImage:
    def test_standard_two_edits_allowed(self):
        with patch.object(
            ig, "call_openai_image_edit", return_value=_fake_responses(1)
        ) as mock_edit:
            result = edit_image(
                image_bytes=b"PNG", prompt="강조", plan_id="standard", edit_used=0
            )
            edit_image(
                image_bytes=b"PNG", prompt="강조", plan_id="standard", edit_used=1
            )

        assert result.mode == "edit"
        assert len(result.images) == 1
        # 1024x1024 medium 전달
        mock_edit.assert_called_with(
            image_bytes=b"PNG",
            prompt="강조",
            size="1024x1024",
            quality="medium",
            mask_bytes=None,
            n=1,
        )

    def test_standard_third_edit_blocked(self):
        with pytest.raises(ImageQuotaExceeded) as exc:
            edit_image(
                image_bytes=b"PNG", prompt="강조", plan_id="standard", edit_used=2
            )
        assert exc.value.kind == "edit"
        assert exc.value.limit == 2

    def test_pro_four_edits_allowed(self):
        with patch.object(
            ig, "call_openai_image_edit", return_value=_fake_responses(1)
        ):
            for used in range(4):
                edit_image(
                    image_bytes=b"PNG", prompt="x", plan_id="pro", edit_used=used
                )

    def test_pro_fifth_edit_blocked(self):
        with pytest.raises(ImageQuotaExceeded) as exc:
            edit_image(
                image_bytes=b"PNG", prompt="x", plan_id="pro", edit_used=4
            )
        assert exc.value.limit == 4

    def test_edit_with_mask(self):
        with patch.object(
            ig, "call_openai_image_edit", return_value=_fake_responses(1)
        ) as mock_edit:
            edit_image(
                image_bytes=b"PNG",
                prompt="x",
                plan_id="pro",
                edit_used=0,
                mask_bytes=b"MASK",
            )
        kwargs = mock_edit.call_args.kwargs
        assert kwargs["mask_bytes"] == b"MASK"

    def test_empty_image_rejected(self):
        with pytest.raises(AIClientError) as exc:
            edit_image(
                image_bytes=b"", prompt="x", plan_id="standard", edit_used=0
            )
        assert exc.value.kind == "bad_request"

    def test_empty_prompt_rejected(self):
        with pytest.raises(AIClientError) as exc:
            edit_image(
                image_bytes=b"PNG", prompt="", plan_id="standard", edit_used=0
            )
        assert exc.value.kind == "bad_request"


# ── 한도 상태 조회 ────────────────────────────────────────


class TestQuotaStatus:
    def test_standard_initial_state(self):
        status = get_quota_status("standard", regen_used=0, edit_used=0)
        assert status["plan_id"] == "standard"
        assert status["regen"]["remaining"] == 1
        assert status["edit"]["remaining"] == 2

    def test_pro_partial_use(self):
        status = get_quota_status("pro", regen_used=1, edit_used=2)
        assert status["regen"]["remaining"] == 1
        assert status["edit"]["remaining"] == 2

    def test_negative_remaining_clamped_to_zero(self):
        status = get_quota_status("standard", regen_used=5, edit_used=10)
        assert status["regen"]["remaining"] == 0
        assert status["edit"]["remaining"] == 0

    def test_unknown_plan_normalized_to_free(self):
        status = get_quota_status("nope", regen_used=0, edit_used=0)
        assert status["plan_id"] == "free"
        assert status["regen"]["free_limit"] == 0
        assert status["edit"]["free_limit"] == 0
