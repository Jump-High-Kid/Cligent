"""
format_selector.py — 블로그 형식 풀에서 1개 선택
config.diversity.format_selection 모드에 따라 작동합니다.
"""
import random
from pathlib import Path
from typing import Optional, TypedDict


class FormatChoice(TypedDict):
    id: str
    label: str
    template_path: str


def select_format(
    diversity_config: dict,
    user_choice: Optional[str] = None,
) -> FormatChoice:
    """
    형식 선택 모드:
    - "random_weighted": weight 기반 가중치 랜덤
    - "user_choice": user_choice 파라미터 사용 (검증 후)
    - "fixed:<id>": 해당 id 강제
    """
    formats: list[dict] = diversity_config.get("blog_formats", [])
    if not formats:
        return FormatChoice(id="information", label="정보 전달", template_path="prompts/formats/information.txt")

    mode: str = diversity_config.get("format_selection", "random_weighted")

    # 사용자가 명시적으로 형식을 선택한 경우 모드보다 우선 적용
    if user_choice:
        matched = next((f for f in formats if f["id"] == user_choice), None)
        if matched:
            return FormatChoice(
                id=matched["id"],
                label=matched["label"],
                template_path=matched["template"],
            )

    if mode.startswith("fixed:"):
        target_id = mode.split(":", 1)[1]
        matched = next((f for f in formats if f["id"] == target_id), None)
        if matched:
            return FormatChoice(
                id=matched["id"],
                label=matched["label"],
                template_path=matched["template"],
            )

    # random_weighted (기본) — weight 가중치 비복원 샘플
    weights = [f.get("weight", 1) for f in formats]
    chosen = random.choices(formats, weights=weights, k=1)[0]
    return FormatChoice(
        id=chosen["id"],
        label=chosen["label"],
        template_path=chosen["template"],
    )


def load_format_template(template_path: str) -> str:
    """formats/*.txt 파일 내용 반환. 없으면 빈 문자열."""
    root = Path(__file__).parent.parent
    path = root / template_path
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""
