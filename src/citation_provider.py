"""
citation_provider.py — 블로그 인용 풀 제공
정적 풀(한의학 고전/학술지) + 동적 검색 링크(RISS·KCI)
실제 API 호출 없음 → 응답 지연 0, 토큰 비용 증가 없음
"""
import random
from typing import Optional, TypedDict
from urllib.parse import quote


class Citation(TypedDict):
    label: str
    url: Optional[str]  # None이면 출처명만 (정적 인용)


def get_static_citation(diversity_config: dict) -> Optional[Citation]:
    """정적 풀에서 1개 랜덤 선택."""
    citations_cfg: dict = diversity_config.get("citations", {})
    pool: list[str] = citations_cfg.get("static_pool", [])
    if not pool:
        return None
    chosen = random.choice(pool)
    return Citation(label=chosen, url=None)


def get_dynamic_citations(keyword: str, diversity_config: dict) -> list[Citation]:
    """
    RISS·KCI 검색 결과 URL 자동 생성.
    mode=link_only — 실제 API 호출 없이 URL만 조립.
    """
    citations_cfg: dict = diversity_config.get("citations", {})
    dynamic_cfg: dict = citations_cfg.get("dynamic_search", {})

    if not dynamic_cfg.get("enabled", False):
        return []

    providers: list[str] = dynamic_cfg.get("providers", [])
    encoded = quote(keyword)
    result: list[Citation] = []

    _url_templates: dict[str, tuple[str, str]] = {
        "riss": (
            f"RISS 학술자료 — {keyword}",
            f"https://www.riss.kr/search/Search.do?query={encoded}",
        ),
        "kci": (
            f"KCI 한국학술지 — {keyword}",
            f"https://www.kci.go.kr/kciportal/po/search/poSearchArtiList.kci?query={encoded}",
        ),
    }

    for provider in providers:
        if provider in _url_templates:
            label, url = _url_templates[provider]
            result.append(Citation(label=label, url=url))

    return result


def build_citation_block(
    keyword: str,
    diversity_config: dict,
) -> str:
    """
    블로그 하단 '참고 자료' 섹션 마크다운 블록 반환.
    의료법 안전: '효과 입증' 단정 표현 없음, '관련 학술자료' 안내 형식.
    """
    static = get_static_citation(diversity_config)
    dynamic = get_dynamic_citations(keyword, diversity_config)

    lines: list[str] = ["---", "**관련 학술자료 검색**"]

    if static:
        lines.append(f"- {static['label']}")

    for cit in dynamic:
        if cit["url"]:
            lines.append(f"- [{cit['label']}]({cit['url']})")

    if len(lines) <= 2:
        return ""

    lines.append(
        "\n*위 자료는 관련 학술 정보 탐색을 위한 안내이며, "
        "특정 치료 효과를 보장하지 않습니다.*"
    )
    return "\n".join(lines)
