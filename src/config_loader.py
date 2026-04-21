"""
config_loader.py — config.yaml과 prompts/ 파일을 읽어오는 유틸리티
코드를 건드리지 않고 config.yaml만 수정해도 동작이 바뀝니다.
"""
from pathlib import Path
import yaml

# 프로젝트 루트 경로 (src/ 의 상위 폴더)
ROOT = Path(__file__).parent.parent


def load_config() -> dict:
    """config.yaml 전체를 딕셔너리로 반환"""
    config_path = ROOT / "config.yaml"
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(prompt_key: str) -> str:
    """config.yaml의 prompts 섹션에 정의된 파일을 읽어 반환

    예: load_prompt("blog") → prompts/blog.txt 내용 반환
    """
    config = load_config()
    prompt_path = ROOT / config["prompts"][prompt_key]
    with open(prompt_path, encoding="utf-8") as f:
        return f.read()


def save_blog_config(flow: dict, blog: dict) -> None:
    """config.yaml의 flow + blog 섹션만 업데이트하고 나머지 섹션은 보존"""
    config_path = ROOT / "config.yaml"
    current = load_config()
    current["flow"].update(flow)
    current["blog"].update(blog)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(current, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def save_prompt(prompt_key: str, content: str) -> None:
    """config.yaml에 정의된 프롬프트 파일 내용을 덮어씁니다"""
    config = load_config()
    prompt_path = ROOT / config["prompts"][prompt_key]
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(content)
