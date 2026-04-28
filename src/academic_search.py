"""
academic_search.py — 학술 자료 검색 (대한한의학회지 + PubMed + Naver 전문자료)
generate_blog_stream() (동기 제너레이터) 내에서 ThreadPoolExecutor로 병렬 실행.
"""
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

_TIMEOUT = 8.0
_JKOM_TIMEOUT = 15  # jkom.org 검색이 느린 서버 (10s+)
_CACHE_TTL = 86400  # 24시간
_CACHE_PATH = Path(__file__).parent.parent / "data" / "academic_cache.json"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _cache_get(key: str) -> Optional[list]:
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            store = json.load(f)
        entry = store.get(key)
        if entry and time.time() - entry["ts"] < _CACHE_TTL:
            return entry["data"]
    except Exception:
        pass
    return None


def _cache_set(key: str, data: list) -> None:
    try:
        store: dict = {}
        if _CACHE_PATH.exists():
            with open(_CACHE_PATH, encoding="utf-8") as f:
                store = json.load(f)
        # 만료 항목 정리
        now = time.time()
        store = {k: v for k, v in store.items() if now - v.get("ts", 0) < _CACHE_TTL}
        store[key] = {"ts": now, "data": data}
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
    except Exception:
        pass

# 한국 의학 용어 → 영어 PubMed 검색어 매핑
_KO_EN: dict[str, str] = {
    "요추": "lumbar",
    "경추": "cervical",
    "추간판": "intervertebral disc",
    "디스크": "disc herniation",
    "탈출": "herniation",
    "팽윤": "bulging disc",
    "척추관": "spinal stenosis",
    "협착": "stenosis",
    "침": "acupuncture",
    "추나": "Chuna manipulation",
    "한약": "herbal medicine",
    "뜸": "moxibustion",
    "부항": "cupping therapy",
    "통증": "pain",
    "두통": "headache",
    "어깨": "shoulder",
    "무릎": "knee",
    "허리": "low back pain",
    "목": "neck pain",
    "불면": "insomnia",
    "비만": "obesity",
    "당뇨": "diabetes",
    "고혈압": "hypertension",
    "소화": "digestive",
    "면역": "immunity",
    "스트레스": "stress",
    "우울": "depression",
    "불안": "anxiety",
    "피로": "fatigue",
    "관절": "joint",
    "염증": "inflammation",
    "척추": "spine",
    "골반": "pelvis",
    "근육": "muscle",
    "신경": "nerve",
    "중풍": "stroke",
    "안면": "facial",
    "마비": "palsy",
    "교통사고": "traffic accident injury",
    "좌골": "sciatic",
    "대상포진": "herpes zoster",
    "소화불량": "dyspepsia",
    "생리통": "dysmenorrhea",
    "갱년기": "menopause",
    "면역력": "immune function",
}


def _to_english(keyword: str) -> str:
    """한국어 키워드 → 영어. 한글이 5자 이상 남으면 빈 문자열 반환."""
    result = keyword
    for ko, en in _KO_EN.items():
        result = result.replace(ko, en)
    if len(re.findall(r"[가-힣]", result)) > 5:
        return ""
    return result.strip()


# ── 대한한의학회지 ──────────────────────────────────────────────────────────

def _jkom_fetch(url: str, post_data: Optional[dict] = None) -> Optional[str]:
    """jkom.org 요청 — 브라우저 헤더 필요, 검색 페이지는 POST + 느린 응답."""
    if post_data:
        encoded = urllib.parse.urlencode(post_data).encode()
        req = urllib.request.Request(
            url, data=encoded,
            headers={
                "User-Agent": _BROWSER_UA,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.jkom.org/articles/search.php",
            },
        )
    else:
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=_JKOM_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _search_jkom(keyword: str, max_results: int = 3) -> list[dict]:
    cache_key = f"jkom_{keyword}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    html = _jkom_fetch(
        "https://www.jkom.org/articles/search_result.php",
        post_data={"key": keyword},
    )
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = soup.find_all("a", href=re.compile(r"/journal/view\.php\?number=\d+"))

    seen: set[str] = set()
    article_ids: list[str] = []
    for link in links:
        m = re.search(r"number=(\d+)", link["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            article_ids.append(m.group(1))
        if len(article_ids) >= max_results:
            break

    results: list[dict] = []
    for aid in article_ids:
        art = _fetch_jkom_article(aid)
        if art:
            results.append(art)

    _cache_set(cache_key, results)
    return results


def _fetch_jkom_article(article_id: str) -> Optional[dict]:
    url = f"https://www.jkom.org/journal/view.php?number={article_id}"
    html = _jkom_fetch(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 제목: 짧지 않은 첫 번째 h3
    title = ""
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if len(text) > 10 and not re.search(r"abstract", text, re.IGNORECASE):
            title = text
            break

    # 저자 (최대 4명)
    author_tags = soup.find_all("a", href=re.compile(r"term=author"))
    authors = ", ".join(a.get_text(strip=True) for a in author_tags[:4])

    # 초록
    abstract = ""
    for h3 in soup.find_all("h3"):
        if re.search(r"abstract", h3.get_text(), re.IGNORECASE):
            sibling = h3.find_next_sibling()
            if sibling:
                abstract = sibling.get_text(" ", strip=True)[:500]
            break

    if not title:
        return None

    return {
        "source": "대한한의학회지",
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "url": url,
    }


# ── PubMed ─────────────────────────────────────────────────────────────────

def _search_pubmed(keyword: str, max_results: int = 3) -> list[dict]:
    cache_key = f"pubmed_{keyword}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    en_keyword = _to_english(keyword)
    if not en_keyword:
        # 앞 3단어만으로 재시도
        en_keyword = _to_english(" ".join(keyword.split()[:3]))
    if not en_keyword:
        return []

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            search_resp = client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": f"{en_keyword}[Title/Abstract]",
                    "retmax": max_results,
                    "retmode": "json",
                    "sort": "relevance",
                },
            )
            search_resp.raise_for_status()
            pmids = search_resp.json().get("esearchresult", {}).get("idlist", [])
            if not pmids:
                return []

            fetch_resp = client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={
                    "db": "pubmed",
                    "id": ",".join(pmids),
                    "rettype": "abstract",
                    "retmode": "xml",
                },
            )
            fetch_resp.raise_for_status()
            results = _parse_pubmed_xml(fetch_resp.text)
            _cache_set(cache_key, results)
            return results
    except Exception:
        return []


def _parse_pubmed_xml(xml_text: str) -> list[dict]:
    results: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for article in root.findall(".//PubmedArticle"):
        try:
            title = (article.findtext(".//ArticleTitle") or "").strip()
            pmid = (article.findtext(".//PMID") or "").strip()
            journal = (article.findtext(".//Title") or "").strip()
            year = (article.findtext(".//PubDate/Year") or "").strip()

            authors_list: list[str] = []
            for a in article.findall(".//Author")[:3]:
                last = a.findtext("LastName") or ""
                fore = a.findtext("ForeName") or ""
                if last:
                    authors_list.append(f"{last} {fore}".strip())
            authors = ", ".join(authors_list)

            abstract_parts = article.findall(".//AbstractText")
            abstract = " ".join((p.text or "") for p in abstract_parts)[:500]

            if title and pmid:
                results.append({
                    "source": "PubMed",
                    "title": title,
                    "authors": authors,
                    "journal": f"{journal} ({year})" if journal else "",
                    "abstract": abstract,
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                })
        except Exception:
            continue
    return results


# ── Naver 전문자료 ──────────────────────────────────────────────────────────

def _search_naver_doc(keyword: str, max_results: int = 3) -> list[dict]:
    """Naver doc.json 검색. 권한 없거나 실패하면 빈 리스트 반환."""
    try:
        from naver_checker import _get_naver_credentials
        client_id, client_secret = _get_naver_credentials()
    except Exception:
        return []

    if not client_id or not client_secret:
        return []

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                "https://openapi.naver.com/v1/search/doc.json",
                params={"query": keyword, "display": max_results},
                headers={
                    "X-Naver-Client-Id": client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
            )
            if resp.status_code != 200:
                return []
            items = resp.json().get("items", [])
    except Exception:
        return []

    results: list[dict] = []
    for item in items:
        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
        desc = re.sub(r"<[^>]+>", "", item.get("description", "")).strip()
        link = item.get("link", "")
        if title:
            results.append({
                "source": "Naver 전문자료",
                "title": title,
                "authors": "",
                "abstract": desc[:400],
                "url": link,
            })
    return results


# ── 통합 API ────────────────────────────────────────────────────────────────

def search_all_academic(keyword: str) -> list[dict]:
    """3소스 병렬 검색. 실패한 소스는 자동 제외."""
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_search_jkom, keyword): "jkom",
            executor.submit(_search_pubmed, keyword): "pubmed",
            executor.submit(_search_naver_doc, keyword): "naver",
        }
        combined: list[dict] = []
        for future in as_completed(futures):
            try:
                combined.extend(future.result() or [])
            except Exception:
                pass
    return combined


def build_rag_context_for_prompt(results: list[dict]) -> str:
    """검색 결과 → 시스템 프롬프트 주입용 텍스트."""
    if not results:
        return ""

    lines = [
        "## 학술 참고 자료 (반드시 활용할 것)",
        "아래 논문·자료의 핵심 내용을 블로그 본문에 자연스럽게 반영하고, 글 말미 **참고 문헌** 섹션에",
        "각 자료의 제목·저자·핵심 인용구(1~2문장)·URL을 포함하세요.",
        "인용구는 반드시 아래 초록 원문에서 선택하세요. 없으면 요약으로 대체하세요.\n",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"[자료 {i}] 출처: {r['source']}")
        lines.append(f"제목: {r['title']}")
        if r.get("authors"):
            lines.append(f"저자: {r['authors']}")
        if r.get("journal"):
            lines.append(f"학술지: {r['journal']}")
        if r.get("abstract"):
            lines.append(f"초록: {r['abstract']}")
        lines.append(f"URL: {r['url']}")
        lines.append("")
    return "\n".join(lines)
