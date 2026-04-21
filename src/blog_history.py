"""
blog_history.py — 블로그 생성 이력 저장 및 통계 조회

저장 위치: data/blog_history.json
통계 용도: 대시보드 Blog Generator 카드에 동적 데이터 표시
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

ROOT = Path(__file__).parent.parent
HISTORY_PATH = ROOT / "data" / "blog_history.json"


def save_blog_entry(
    keyword: str,
    tone: str,
    char_count: int,
    cost_krw: int,
    seo_keywords: Optional[List[str]] = None,
    blog_text: str = "",
) -> None:
    """블로그 생성 완료 시 이력 저장"""
    history = _load_history()
    title = _extract_title(blog_text) if blog_text else keyword
    entry = {
        "id": len(history) + 1,
        "keyword": keyword,
        "title": title,
        "tone": tone,
        "char_count": char_count,
        "cost_krw": cost_krw,
        "seo_keywords": seo_keywords or [],
        "created_at": datetime.now().isoformat(),
    }
    history.append(entry)
    _save_history(history)


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
    history = _load_history()
    if not history:
        return {
            "total": 0,
            "this_month": 0,
            "recent_keywords": [],
            "last_created_at": None,
        }

    now = datetime.now()

    this_month_count = sum(
        1 for e in history
        if _parse_dt(e["created_at"]).month == now.month
        and _parse_dt(e["created_at"]).year == now.year
    )

    recent_keywords = [e["keyword"] for e in reversed(history[-3:])]

    return {
        "total": len(history),
        "this_month": this_month_count,
        "recent_keywords": recent_keywords,
        "last_created_at": history[-1]["created_at"],
    }


def get_recent_posts(limit: int = 5) -> List[Dict]:
    """
    최근 블로그 이력 반환 (연관 글 링크용)

    반환 형식:
    [{"id": 1, "keyword": "소화불량", "title": "소화불량 한방으로 잡는 법", "created_at": "..."}]
    """
    history = _load_history()
    recent = history[-limit:] if len(history) >= limit else history
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


# ── 내부 헬퍼 ──────────────────────────────────────────────────

def _extract_title(blog_text: str) -> str:
    """블로그 텍스트에서 첫 번째 제목(# 또는 ##) 추출"""
    for line in blog_text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    # 제목 없으면 첫 비어있지 않은 줄 반환
    for line in blog_text.splitlines():
        if line.strip():
            return line.strip()[:40]
    return ""


def _load_history() -> List[Dict]:
    """JSON 파일에서 이력 로드. 파일 없으면 빈 리스트 반환"""
    if not HISTORY_PATH.exists():
        return []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # 구 형식(리스트) 지원
    if isinstance(data, list):
        return data
    return []


def _save_history(history: List[Dict]) -> None:
    """이력을 JSON 파일에 저장"""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _parse_dt(iso_str: str) -> datetime:
    """ISO 형식 문자열을 datetime으로 변환"""
    return datetime.fromisoformat(iso_str)
