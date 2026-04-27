"""
hook_selector.py — 블로그 도입부 hook 풀에서 1개 랜덤 선택
"""
import random
from typing import Optional

_HOOK_DESCRIPTIONS: dict[str, str] = {
    "statistic":     "통계형 — '한국인 ○명 중 1명이 ○○으로...' 형식으로 시작",
    "case":          "사례형 — '50대 남성 환자분이 진료실에 들어오시며...' (가명 표기)",
    "question":      "질문형 — '허리 통증, 정말 한의학으로 치료될 수 있을까요?'",
    "season":        "계절형 — '환절기에 들어서면서 ○○ 증상이...'",
    "classic_quote": "고전 인용형 — '《동의보감》에 이르기를...'",
}


def select_hook(
    diversity_config: dict,
    user_choice: Optional[str] = None,
    excluded_hooks: Optional[set] = None,
) -> str:
    """hook 풀에서 1개 선택 후 hook id 반환. excluded_hooks에 있는 hook은 제외."""
    hooks: list[str] = diversity_config.get("hooks", list(_HOOK_DESCRIPTIONS.keys()))
    if excluded_hooks:
        hooks = [h for h in hooks if h not in excluded_hooks]
    mode: str = diversity_config.get("hook_selection", "random")

    if mode == "user_choice" and user_choice and user_choice in hooks:
        return user_choice

    return random.choice(hooks) if hooks else "question"


def get_hook_instruction(hook_id: str) -> str:
    """hook id → Claude 프롬프트 지시문 반환."""
    return _HOOK_DESCRIPTIONS.get(hook_id, "질문형으로 시작")
