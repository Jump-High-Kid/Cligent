"""hook_selector 단위 테스트"""
import pytest
from hook_selector import select_hook, get_hook_instruction


DIVERSITY_CONFIG = {
    "enabled": True,
    "hooks": ["statistic", "case", "question", "season", "classic_quote"],
    "hook_selection": "random",
}

VALID_HOOKS = {"statistic", "case", "question", "season", "classic_quote"}


def test_random_returns_valid_hook():
    for _ in range(20):
        hook = select_hook(DIVERSITY_CONFIG)
        assert hook in VALID_HOOKS


def test_all_hooks_reachable():
    """100회 반복 시 5개 hook 모두 최소 1회 선택돼야 한다."""
    seen = set()
    for _ in range(100):
        seen.add(select_hook(DIVERSITY_CONFIG))
    assert seen == VALID_HOOKS


def test_user_choice_mode_valid():
    cfg = {**DIVERSITY_CONFIG, "hook_selection": "user_choice"}
    assert select_hook(cfg, user_choice="classic_quote") == "classic_quote"


def test_user_choice_mode_invalid_falls_back():
    cfg = {**DIVERSITY_CONFIG, "hook_selection": "user_choice"}
    result = select_hook(cfg, user_choice="nonexistent")
    assert result in VALID_HOOKS


def test_empty_hooks_returns_default():
    cfg = {"hooks": [], "hook_selection": "random"}
    result = select_hook(cfg)
    assert result == "question"


def test_get_hook_instruction_returns_string():
    for hook_id in VALID_HOOKS:
        instruction = get_hook_instruction(hook_id)
        assert isinstance(instruction, str)
        assert len(instruction) > 5


def test_get_hook_instruction_unknown_returns_fallback():
    instruction = get_hook_instruction("unknown_hook")
    assert isinstance(instruction, str)
