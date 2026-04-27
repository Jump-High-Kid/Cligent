"""format_selector 단위 테스트"""
import pytest
from format_selector import select_format, load_format_template


DIVERSITY_CONFIG = {
    "enabled": True,
    "blog_formats": [
        {"id": "information", "label": "정보 전달", "weight": 30, "template": "prompts/formats/information.txt"},
        {"id": "case_study",  "label": "케이스 스터디", "weight": 20, "template": "prompts/formats/case_study.txt"},
        {"id": "qna",         "label": "Q&A", "weight": 20, "template": "prompts/formats/qna.txt"},
        {"id": "comparison",  "label": "비교형", "weight": 10, "template": "prompts/formats/comparison.txt"},
        {"id": "seasonal",    "label": "계절·시기형", "weight": 10, "template": "prompts/formats/seasonal.txt"},
        {"id": "lifestyle",   "label": "라이프스타일", "weight": 10, "template": "prompts/formats/lifestyle.txt"},
    ],
    "format_selection": "random_weighted",
}

VALID_IDS = {"information", "case_study", "qna", "comparison", "seasonal", "lifestyle"}


def test_random_weighted_returns_valid_id():
    for _ in range(20):
        choice = select_format(DIVERSITY_CONFIG)
        assert choice["id"] in VALID_IDS


def test_random_weighted_all_ids_reachable():
    """100회 반복 시 6개 형식 모두 최소 1회 선택돼야 한다."""
    seen = set()
    for _ in range(200):
        seen.add(select_format(DIVERSITY_CONFIG)["id"])
    assert seen == VALID_IDS


def test_user_choice_mode():
    cfg = {**DIVERSITY_CONFIG, "format_selection": "user_choice"}
    choice = select_format(cfg, user_choice="qna")
    assert choice["id"] == "qna"


def test_user_choice_invalid_falls_back_to_weighted():
    cfg = {**DIVERSITY_CONFIG, "format_selection": "user_choice"}
    choice = select_format(cfg, user_choice="nonexistent")
    assert choice["id"] in VALID_IDS


def test_fixed_mode():
    cfg = {**DIVERSITY_CONFIG, "format_selection": "fixed:comparison"}
    choice = select_format(cfg)
    assert choice["id"] == "comparison"


def test_fixed_mode_invalid_falls_back_to_weighted():
    cfg = {**DIVERSITY_CONFIG, "format_selection": "fixed:xyz"}
    choice = select_format(cfg)
    assert choice["id"] in VALID_IDS


def test_empty_formats_returns_default():
    choice = select_format({"enabled": True, "blog_formats": []})
    assert choice["id"] == "information"


def test_load_format_template_returns_content():
    content = load_format_template("prompts/formats/information.txt")
    assert len(content) > 0
    assert "의료법" in content


def test_load_format_template_missing_file_returns_empty():
    content = load_format_template("prompts/formats/nonexistent.txt")
    assert content == ""
