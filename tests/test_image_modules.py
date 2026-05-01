"""
test_image_modules.py — 이미지 모듈 11종 단위 테스트 (2026-05-01)

검증:
  - IMAGE_MODULES dict 무결성 (1~11 다 존재, 필수 키 보유)
  - get_module fallback (None / 알 수 없는 ID → 11번)
  - build_module_addendum 출력 (모듈 fragment 포함, 손/발 자동 디렉티브)
  - build_global_directives 출력 (단일 이미지 강제 + global negatives)
  - 매트릭스 일관성 — 모듈별 booster·negative 매트릭스 결정 그대로 반영
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from image_modules import (  # noqa: E402
    GLOBAL_NEGATIVES,
    HAND_FOOT_DIRECTIVES,
    IMAGE_MODULES,
    SINGLE_IMAGE_DIRECTIVE,
    build_global_directives,
    build_module_addendum,
    get_module,
    total_modules,
)


REQUIRED_KEYS = {"name_ko", "directives", "negatives", "boosters", "style_suffix"}


# ── dict 무결성 ────────────────────────────────────────────


class TestModuleCatalog:
    def test_eleven_modules_present(self):
        assert total_modules() == 11
        assert set(IMAGE_MODULES.keys()) == set(range(1, 12))

    def test_each_module_has_required_keys(self):
        for mid, m in IMAGE_MODULES.items():
            missing = REQUIRED_KEYS - set(m.keys())
            assert not missing, f"module {mid} missing keys: {missing}"

    def test_directives_negatives_are_lists(self):
        for mid, m in IMAGE_MODULES.items():
            assert isinstance(m["directives"], list), f"module {mid} directives not list"
            assert isinstance(m["negatives"], list), f"module {mid} negatives not list"
            assert isinstance(m["boosters"], list), f"module {mid} boosters not list"

    def test_name_ko_is_korean(self):
        for mid, m in IMAGE_MODULES.items():
            name = m["name_ko"]
            assert name and isinstance(name, str)
            # 한글 문자가 1개 이상 있어야 함
            assert any('가' <= c <= '힣' for c in name)


# ── get_module fallback ───────────────────────────────────


class TestGetModule:
    def test_valid_id_returns_module(self):
        m = get_module(1)
        assert m["name_ko"] == "해부학 이미지"

    def test_none_falls_back_to_eleven(self):
        m = get_module(None)
        assert m["name_ko"] == "기타"

    def test_unknown_id_falls_back_to_eleven(self):
        assert get_module(99)["name_ko"] == "기타"
        assert get_module(0)["name_ko"] == "기타"
        assert get_module(-1)["name_ko"] == "기타"


# ── build_module_addendum ─────────────────────────────────


class TestBuildModuleAddendum:
    def test_includes_module_id_and_name(self):
        addendum = build_module_addendum(2)
        assert "Module: 2" in addendum
        assert "인체 치료" in addendum

    def test_includes_directives(self):
        addendum = build_module_addendum(1)  # 해부학
        assert "isolated on pure white" in addendum
        assert "Directives" in addendum

    def test_includes_negatives(self):
        addendum = build_module_addendum(2)  # 침/뜸
        assert "Negatives" in addendum
        assert "needle through clothing" in addendum

    def test_includes_boosters_when_present(self):
        addendum = build_module_addendum(1)  # 해부학 → ultra-detailed 보유
        assert "ultra-detailed" in addendum
        assert "medical illustration accuracy" in addendum

    def test_skips_boosters_when_empty(self):
        # 모듈 11(기타)는 boosters=[]
        addendum = build_module_addendum(11)
        assert "Quality boosters" not in addendum

    def test_includes_style_suffix(self):
        addendum = build_module_addendum(2)
        assert "Style suffix" in addendum
        assert "soft natural lighting" in addendum

    def test_hand_directives_when_anatomical_region_hand(self):
        addendum = build_module_addendum(2, anatomical_region="hand")
        assert "exactly five fingers and five toes" in addendum

    def test_foot_directives_when_anatomical_region_foot(self):
        addendum = build_module_addendum(2, anatomical_region="foot")
        assert "exactly five fingers and five toes" in addendum

    def test_no_hand_directives_for_other_regions(self):
        addendum = build_module_addendum(2, anatomical_region="back")
        assert "exactly five fingers and five toes" not in addendum

    def test_unknown_module_falls_back_to_eleven_addendum(self):
        # 99 → 모듈 11 fallback. 매트릭스: gown ❌, 환자복 ❌, 한국정체성 ✅
        addendum = build_module_addendum(99)
        assert "기타" in addendum


# ── build_global_directives ───────────────────────────────


class TestBuildGlobalDirectives:
    def test_includes_single_image_directive(self):
        text = build_global_directives()
        assert SINGLE_IMAGE_DIRECTIVE in text

    def test_includes_global_negatives(self):
        text = build_global_directives()
        for neg in GLOBAL_NEGATIVES:
            assert neg in text


# ── 매트릭스 일관성 (사용자 확정 결정 반영) ──────────────


class TestMatrixConsistency:
    """사용자 확정 매트릭스대로 적용됐는지 핵심 항목 검증."""

    def test_module1_anatomy_no_white_coat_no_patient_gown(self):
        m = IMAGE_MODULES[1]
        # 해부학 도해 — 가운/환자복 디렉티브 없어야 함
        joined = " ".join(m["directives"]).lower()
        assert "white clinical coat" not in joined
        assert "patient gown" not in joined

    def test_module1_anatomy_has_medical_illustration_booster(self):
        m = IMAGE_MODULES[1]
        boosters = " ".join(m["boosters"]).lower()
        assert "medical illustration accuracy" in boosters
        assert "ultra-detailed" in boosters

    def test_module2_treatment_has_white_coat_and_patient_gown(self):
        m = IMAGE_MODULES[2]
        joined = " ".join(m["directives"]).lower()
        # Western-style lab coat 명시 (2026-05-01 강화 — 한국 한의사 가운 표현)
        assert "western-style white lab coat" in joined
        assert "patient gown" in joined
        # Chinese 부정 명시 (positive 디렉티브 안에)
        assert "not chinese tunic suit" in joined
        assert "not mandarin collar" in joined

    def test_module2_negatives_explicit_chinese_japanese(self):
        """가운·의료기구·배경 모두에 명시적 China/Japan 차단 (2026-05-01)."""
        negs = " ".join(IMAGE_MODULES[2]["negatives"]).lower()
        # 가운
        assert "chinese tunic suit" in negs
        assert "mandarin collar coat" in negs
        assert "changshan style robe" in negs
        # 의료 기구
        assert "chinese reusable thicker needles" in negs
        # 배경
        assert "red lanterns" in negs
        assert "chinese calligraphy wall scrolls" in negs

    def test_module3_chuna_has_western_coat(self):
        joined = " ".join(IMAGE_MODULES[3]["directives"]).lower()
        assert "western-style white lab coat" in joined
        assert "korean chuna therapy adjustable padded vinyl clinic table" in joined

    def test_module8_consultation_has_western_coat_and_korean_decor(self):
        joined = " ".join(IMAGE_MODULES[8]["directives"]).lower()
        assert "western-style white lab coat" in joined
        assert "hangul" in joined  # 한글 게시물·인터페이스
        assert "not chinese tunic suit" in joined


# ── 5대 추가 negatives 분배 (2026-05-01) ──────────────────


class TestFiveStarNegatives:
    """⭐ 5개 추가 negatives의 모듈별 적용 검증.

    1. 한글 텍스트 박힘 — 1·2·3·4·7·8·10·11 (텍스트 의도 모듈 5·6·9 제외)
    2. 침 과도 — 모듈 2만
    3. 양방 응급실 톤 — 모듈 2·3·8
    4. 아동 — 모듈 2·3·7·10
    5. 노출 — 모듈 2·3·10
    """

    HANGUL_TEXT_MODULES = [1, 2, 3, 4, 7, 8, 10, 11]
    HANGUL_TEXT_EXCLUDED = [5, 6, 9]

    NEEDLE_FIELD_MODULES = [2]
    FLUORESCENT_MODULES = [2, 3, 8]
    CHILD_PATIENT_MODULES = [2, 3, 7, 10]
    NUDITY_MODULES = [2, 3, 10]

    def test_hangul_text_negative_in_target_modules(self):
        for mid in self.HANGUL_TEXT_MODULES:
            negs = " ".join(IMAGE_MODULES[mid]["negatives"]).lower()
            assert "hangul or korean text rendering" in negs, (
                f"module {mid} missing hangul text negative"
            )

    def test_hangul_text_negative_excluded_from_text_intent_modules(self):
        # 5(포스터), 6(도서), 9(인포그래픽) — 텍스트가 의도된 디자인이라 차단 X
        for mid in self.HANGUL_TEXT_EXCLUDED:
            negs = " ".join(IMAGE_MODULES[mid]["negatives"]).lower()
            assert "hangul or korean text rendering" not in negs, (
                f"module {mid} should NOT have hangul text negative (intended text)"
            )

    def test_needle_field_negative_only_in_module2(self):
        for mid, m in IMAGE_MODULES.items():
            negs = " ".join(m["negatives"]).lower()
            has_field = "needle field" in negs or "dozens of needles" in negs
            if mid in self.NEEDLE_FIELD_MODULES:
                assert has_field, f"module {mid} missing needle field negative"
            else:
                assert not has_field, f"module {mid} should NOT have needle field"

    def test_fluorescent_negative_in_clinical_modules(self):
        for mid in self.FLUORESCENT_MODULES:
            negs = " ".join(IMAGE_MODULES[mid]["negatives"]).lower()
            assert "harsh hospital fluorescent lighting" in negs, (
                f"module {mid} missing fluorescent negative"
            )

    def test_child_patient_negative_in_human_modules(self):
        for mid in self.CHILD_PATIENT_MODULES:
            negs = " ".join(IMAGE_MODULES[mid]["negatives"]).lower()
            assert "child patient, infant, toddler" in negs, (
                f"module {mid} missing child patient negative"
            )

    def test_nudity_negative_in_body_exposure_modules(self):
        for mid in self.NUDITY_MODULES:
            negs = " ".join(IMAGE_MODULES[mid]["negatives"]).lower()
            assert "exposed underwear" in negs, (
                f"module {mid} missing nudity negative"
            )

    def test_module2_treatment_has_dslr_booster(self):
        boosters = " ".join(IMAGE_MODULES[2]["boosters"]).lower()
        assert "dslr" in boosters
        assert "anatomically accurate" in boosters

    def test_module4_herbal_food_has_both_branches(self):
        # 한약 + 음식 모두 포함
        m = IMAGE_MODULES[4]
        joined = " ".join(m["directives"]).lower()
        assert "herbal medicine" in joined
        assert "korean cuisine" in joined or "korean ingredients" in joined

    def test_module4_no_anatomical_booster(self):
        # 한약·음식은 anatomically accurate 부스터 ❌
        boosters = " ".join(IMAGE_MODULES[4]["boosters"]).lower()
        assert "anatomically accurate" not in boosters

    def test_module5_poster_has_editorial_booster(self):
        boosters = " ".join(IMAGE_MODULES[5]["boosters"]).lower()
        assert "editorial photography" in boosters

    def test_module5_poster_no_korean_identity_directive(self):
        # 매트릭스: 한국정체성 ❌
        m = IMAGE_MODULES[5]
        joined = " ".join(m["directives"]).lower()
        assert "korean clinic" not in joined
        assert "korean medical" not in joined

    def test_module9_summary_has_medical_illustration_booster(self):
        boosters = " ".join(IMAGE_MODULES[9]["boosters"]).lower()
        assert "medical illustration accuracy" in boosters

    def test_module9_summary_has_no_dslr_booster(self):
        # 매트릭스: 인포그래픽이라 DSLR ❌
        boosters = " ".join(IMAGE_MODULES[9]["boosters"]).lower()
        assert "dslr" not in boosters

    def test_module10_posture_has_full_anatomy_boosters(self):
        boosters = " ".join(IMAGE_MODULES[10]["boosters"]).lower()
        assert "ultra-detailed" in boosters
        assert "anatomically accurate" in boosters
        assert "dslr" in boosters


# ── Midjourney 파라미터 제거 검증 ────────────────────────


class TestNoMidjourneyParams:
    """gpt-image-2가 무시하는 Midjourney 파라미터가 어떤 모듈에도 없어야 함."""

    BANNED = ["--ar", "--stylize", "--style raw", "--niji", "--w ", "--h "]

    def test_no_mj_params_in_any_module(self):
        for mid, m in IMAGE_MODULES.items():
            joined = " ".join(
                m.get("directives", [])
                + m.get("boosters", [])
                + [m.get("style_suffix", "")]
                + m.get("negatives", [])
            )
            for banned in self.BANNED:
                assert banned not in joined, (
                    f"module {mid} contains banned MJ param: {banned}"
                )
