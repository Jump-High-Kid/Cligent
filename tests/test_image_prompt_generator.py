"""
test_image_prompt_generator.py — Stage 2 모듈 fragment 결합 검증 (2026-05-01)

검증:
  - _build_module_section: 분석 JSON의 scene별 module 필드를 읽어 addendum 결합
  - 5 scene이면 5개 addendum 모두 user message에 포함
  - module 필드 누락 → 11번 fallback (기타) addendum 사용
  - 글로벌 디렉티브 (single image, global negatives) 항상 포함
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


class TestBuildModuleSection:
    def test_includes_global_directives_always(self):
        from image_prompt_generator import _build_module_section
        from image_modules import SINGLE_IMAGE_DIRECTIVE

        section = _build_module_section({"scenes": []})
        assert SINGLE_IMAGE_DIRECTIVE in section

    def test_per_scene_addendums_appended(self):
        from image_prompt_generator import _build_module_section

        analysis = {
            "scenes": [
                {"position": 1, "module": 1, "anatomical_region": "leg"},
                {"position": 2, "module": 4, "anatomical_region": "none"},
                {"position": 3, "module": 8, "anatomical_region": "none"},
            ]
        }
        section = _build_module_section(analysis)
        # 모듈 1 (해부학) — medical illustration accuracy
        assert "medical illustration accuracy" in section
        # 모듈 4 (한약·음식) — Korean herbal medicine
        assert "Korean herbal medicine" in section
        # 모듈 8 (상담) — consultation room
        assert "consultation room" in section

    def test_unknown_module_falls_back_to_eleven(self):
        from image_prompt_generator import _build_module_section

        analysis = {"scenes": [{"position": 1, "module": 999}]}
        section = _build_module_section(analysis)
        # 모듈 11 (기타) — calm professional aesthetic
        assert "calm professional aesthetic" in section

    def test_hand_anatomical_region_adds_finger_directive(self):
        from image_prompt_generator import _build_module_section

        analysis = {
            "scenes": [
                {"position": 1, "module": 2, "anatomical_region": "hand"},
            ]
        }
        section = _build_module_section(analysis)
        assert "exactly five fingers and five toes" in section

    def test_back_region_no_finger_directive(self):
        from image_prompt_generator import _build_module_section

        analysis = {
            "scenes": [
                {"position": 1, "module": 2, "anatomical_region": "back"},
            ]
        }
        section = _build_module_section(analysis)
        assert "exactly five fingers and five toes" not in section

    def test_module_label_includes_scene_position(self):
        from image_prompt_generator import _build_module_section

        analysis = {
            "scenes": [
                {"position": 3, "module": 2, "anatomical_region": "leg"},
            ]
        }
        section = _build_module_section(analysis)
        assert "Scene 3 addendum" in section
        assert "Module: 2" in section
