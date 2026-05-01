"""
citation_provider.py — 블로그 인용 풀 제공
정적 풀(한의학 고전/학술지) + 동적 검색 링크(RISS·KCI·Google Scholar·PubMed)
실제 API 호출 없음 → 응답 지연 0, 토큰 비용 증가 없음
"""
import random
from typing import List, Optional, TypedDict
from urllib.parse import quote


class Citation(TypedDict):
    label: str
    url: Optional[str]  # None이면 출처명만 (정적 인용)


_SASANG_CITATION_LABEL = "이제마 — 동의수세보원(東醫壽世保元) 신축본"


def get_static_citation(
    diversity_config: dict,
    explanation_types: Optional[List[str]] = None,
) -> Optional[Citation]:
    """정적 풀에서 1개 선택. 사상체질 선택 시 동의수세보원으로 고정."""
    # 사상체질 선택 시 정적 인용을 동의수세보원으로 고정
    if explanation_types and "사상체질" in explanation_types:
        return Citation(label=_SASANG_CITATION_LABEL, url=None)

    citations_cfg: dict = diversity_config.get("citations", {})
    pool: list[str] = citations_cfg.get("static_pool", [])
    if not pool:
        return None
    chosen = random.choice(pool)
    return Citation(label=chosen, url=None)


def get_dynamic_citations(keyword: str, diversity_config: dict) -> list[Citation]:
    """
    학술 검색 결과 URL 자동 생성 (RISS·KCI·Google Scholar·PubMed).
    mode=link_only — 실제 API 호출 없이 URL만 조립.
    """
    citations_cfg: dict = diversity_config.get("citations", {})
    dynamic_cfg: dict = citations_cfg.get("dynamic_search", {})

    if not dynamic_cfg.get("enabled", False):
        return []

    providers: list[str] = dynamic_cfg.get("providers", [])
    # URL 쿼리는 앞 3단어만 사용 — 전체 제목은 검색 결과 없음
    short_keyword = " ".join(keyword.split()[:3])
    encoded = quote(short_keyword)
    result: list[Citation] = []

    _url_templates: dict[str, tuple[str, str]] = {
        "riss": (
            f"RISS 학술자료 검색 — {short_keyword}",
            f"https://www.riss.kr/search/Search.do?query={encoded}",
        ),
        "kci": (
            f"KCI 한국학술지 검색 — {short_keyword}",
            f"https://www.kci.go.kr/kciportal/po/search/poSearchArtiList.kci?query={encoded}",
        ),
        "google_scholar": (
            f"Google Scholar — {short_keyword}",
            f"https://scholar.google.com/scholar?q={encoded}",
        ),
        "pubmed": (
            f"PubMed — {short_keyword}",
            f"https://pubmed.ncbi.nlm.nih.gov/?term={encoded}",
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
    explanation_types: Optional[List[str]] = None,
) -> str:
    """
    블로그 하단 '참고 문헌' 섹션 마크다운 블록 반환.
    의료법 안전: '효과 입증' 단정 표현 없음, '관련 학술자료' 안내 형식.
    사상체질 선택 시 원전(동의수세보원) + 현대 임상진료지침 2건 고정 인용.
    """
    static_citations: list[Citation] = []
    primary = get_static_citation(diversity_config, explanation_types)
    if primary:
        static_citations.append(primary)

    # 사상체질 선택 시 현대 임상진료지침 1건 추가 (원전 다음 줄)
    if explanation_types and "사상체질" in explanation_types:
        static_citations.append(Citation(
            label="사상체질의학회. 사상체질병증 임상진료지침.",
            url=None,
        ))

    dynamic = get_dynamic_citations(keyword, diversity_config)

    # 헤더는 ## (마크다운 H2)로 승격 — 본문 다른 섹션과 시각 통일.
    # 빈 라벨 ('**참고 문헌**' inline-bold)이 비어 보이던 문제 해결 (2026-05-01).
    lines: list[str] = ["---", "## 참고 문헌"]
    # RAG 학술 검색 결과 0건일 때 도달하는 경로이므로 사용자에게 명시적 안내.
    lines.append(
        "관련 학술 논문이 자동 검색되지 않아, 아래 원전 출처와 검색 링크로 대신 안내드립니다."
    )
    if static_citations:
        lines.append("")
        lines.append("**원전 / 가이드라인**")
        for cit in static_citations:
            lines.append(f"- {cit['label']}")
    if dynamic:
        lines.append("")
        lines.append("**추가 검색 링크 — 클릭하여 직접 확인하세요**")
        for cit in dynamic:
            if cit["url"]:
                lines.append(f"- [{cit['label']}]({cit['url']})")

    # 안내문구만 있는 경우(인용 0건)에는 빈 블록 반환
    if not static_citations and not dynamic:
        return ""

    lines.append(
        "\n*위 자료는 관련 학술 정보 탐색을 위한 안내입니다. "
        "특정 치료 효과를 보장하지 않으며, 본문의 사실 주장은 본문 내 [번호] 참고 문헌으로 검증해주세요.*"
    )
    return "\n".join(lines)
