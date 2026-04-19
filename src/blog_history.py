"""
blog_history.py — 블로그 생성 이력 저장 및 통계 조회

저장 위치: data/blog_history.json
통계 용도: 대시보드 Blog Generator 카드에 동적 데이터 표시
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

ROOT = Path(__file__).parent.parent
HISTORY_PATH = ROOT / "data" / "blog_history.json"


def save_blog_entry(keyword: str, tone: str, char_count: int, cost_krw: int) -> None:
    """블로그 생성 완료 시 이력 저장"""
    history = _load_history()
    entry = {
        "id": len(history) + 1,
        "keyword": keyword,
        "tone": tone,
        "char_count": char_count,
        "cost_krw": cost_krw,
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

    # 이번 달 생성 수 계산
    this_month_count = sum(
        1 for e in history
        if _parse_dt(e["created_at"]).month == now.month
        and _parse_dt(e["created_at"]).year == now.year
    )

    # 최근 3개 주제 (최신 순)
    recent_keywords = [e["keyword"] for e in reversed(history[-3:])]

    return {
        "total": len(history),
        "this_month": this_month_count,
        "recent_keywords": recent_keywords,
        "last_created_at": history[-1]["created_at"],
    }


# ── 내부 헬퍼 ──────────────────────────────────────────────────

def _load_history() -> List[Dict]:
    """JSON 파일에서 이력 로드. 파일 없으면 빈 리스트 반환"""
    if not HISTORY_PATH.exists():
        return []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_history(history: List[Dict]) -> None:
    """이력을 JSON 파일에 저장"""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _parse_dt(iso_str: str) -> datetime:
    """ISO 형식 문자열을 datetime으로 변환"""
    return datetime.fromisoformat(iso_str)
