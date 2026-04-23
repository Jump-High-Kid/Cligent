"""
blog_history.py — 블로그 생성 이력 저장 및 통계 조회

저장 구조 (두 파일 분리):
- data/blog_stats.json  : 영구 통계 (keyword, tone, char_count, cost_krw, seo_keywords, created_at)
- data/blog_texts.json  : 30일 만료 전문 (blog_text, expires_at)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

ROOT = Path(__file__).parent.parent
STATS_PATH = ROOT / "data" / "blog_stats.json"
TEXTS_PATH = ROOT / "data" / "blog_texts.json"

# 전문(全文) 보관 기간
TEXT_RETENTION_DAYS = 30


def save_blog_entry(
    keyword: str,
    tone: str,
    char_count: int,
    cost_krw: int,
    seo_keywords: Optional[List[str]] = None,
    blog_text: str = "",
) -> None:
    """블로그 생성 완료 시 통계와 전문을 분리 저장"""
    now = datetime.now()
    entry_id = _next_id()

    # 영구 통계 저장
    stats = _load_json(STATS_PATH, default=[])
    title = _extract_title(blog_text) if blog_text else keyword
    stats.append({
        "id": entry_id,
        "keyword": keyword,
        "title": title,
        "tone": tone,
        "char_count": char_count,
        "cost_krw": cost_krw,
        "seo_keywords": seo_keywords or [],
        "created_at": now.isoformat(),
    })
    _save_json(STATS_PATH, stats)

    # 30일 만료 전문 저장 (blog_text 있을 때만)
    if blog_text:
        texts = _load_json(TEXTS_PATH, default=[])
        texts.append({
            "id": entry_id,
            "blog_text": blog_text,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=TEXT_RETENTION_DAYS)).isoformat(),
        })
        _save_json(TEXTS_PATH, texts)


def get_blog_stats() -> dict:
    """
    대시보드 카드용 통계 반환

    반환 형식:
    {
        "total": 전체 생성 수,
        "this_month": 이번 달 생성 수,
        "recent_keywords": [최근 3개 주제],
        "last_created_at": "2026-04-16T14:30:00" | null
    }
    """
    stats = _load_json(STATS_PATH, default=[])
    if not stats:
        return {"total": 0, "this_month": 0, "recent_keywords": [], "last_created_at": None}

    now = datetime.now()
    this_month_count = sum(
        1 for e in stats
        if _parse_dt(e["created_at"]).month == now.month
        and _parse_dt(e["created_at"]).year == now.year
    )
    recent_keywords = [e["keyword"] for e in reversed(stats[-3:])]

    return {
        "total": len(stats),
        "this_month": this_month_count,
        "recent_keywords": recent_keywords,
        "last_created_at": stats[-1]["created_at"],
    }


def get_recent_posts(limit: int = 5) -> List[Dict]:
    """
    최근 블로그 이력 반환 (연관 글 링크용)

    반환 형식:
    [{"id": 1, "keyword": "소화불량", "title": "...", "created_at": "..."}]
    """
    stats = _load_json(STATS_PATH, default=[])
    recent = stats[-limit:] if len(stats) >= limit else stats
    return [
        {
            "id": e["id"],
            "keyword": e["keyword"],
            "title": e.get("title", e["keyword"]),
            "seo_keywords": e.get("seo_keywords", []),
            "created_at": e["created_at"],
        }
        for e in reversed(recent)
    ]


def purge_expired_texts() -> int:
    """만료된 전문 삭제. 삭제된 항목 수 반환."""
    texts = _load_json(TEXTS_PATH, default=[])
    if not texts:
        return 0

    now = datetime.now()
    active = [t for t in texts if _parse_dt(t["expires_at"]) > now]
    removed = len(texts) - len(active)

    if removed > 0:
        _save_json(TEXTS_PATH, active)

    return removed


def get_text_expiry_info(entry_id: int) -> Optional[str]:
    """특정 항목의 전문 만료 일시 반환 (없으면 None)"""
    texts = _load_json(TEXTS_PATH, default=[])
    for t in texts:
        if t.get("id") == entry_id:
            return t.get("expires_at")
    return None


# ── 내부 헬퍼 ──────────────────────────────────────────────────

def _next_id() -> int:
    """stats 파일 기준으로 다음 ID 생성"""
    stats = _load_json(STATS_PATH, default=[])
    return len(stats) + 1


def _extract_title(blog_text: str) -> str:
    """블로그 텍스트에서 첫 번째 제목(# 또는 ##) 추출"""
    for line in blog_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    for line in blog_text.splitlines():
        if line.strip():
            return line.strip()[:40]
    return ""


def _load_json(path: Path, default) -> list:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else default


def _save_json(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_dt(iso_str: str) -> datetime:
    return datetime.fromisoformat(iso_str)
