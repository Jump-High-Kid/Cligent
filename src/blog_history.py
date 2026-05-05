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
    clinic_id: Optional[int] = None,
    is_partial: bool = False,
) -> int:
    """블로그 생성 완료 시 통계와 전문을 분리 저장. 생성된 entry_id 반환.
    clinic_id가 None이면 어드민 통합 조회에서 '미상'으로 표시(하위 호환).
    is_partial=True는 SSE 도중 끊긴 부분 본문임을 표시 — 어드민 KPI에서 분리 집계."""
    now = datetime.now()
    entry_id = _next_id()

    # 영구 통계 저장
    stats = _load_json(STATS_PATH, default=[])
    title = _extract_title(blog_text) if blog_text else keyword
    entry = {
        "id": entry_id,
        "clinic_id": clinic_id,
        "keyword": keyword,
        "title": title,
        "tone": tone,
        "char_count": char_count,
        "cost_krw": cost_krw,
        "seo_keywords": seo_keywords or [],
        "naver_url": "",
        "created_at": now.isoformat(),
    }
    if is_partial:
        entry["is_partial"] = True
    stats.append(entry)
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

    return entry_id


def get_blog_stats(
    clinic_id: Optional[int] = None,
    since: Optional[str] = None,
) -> dict:
    """
    대시보드 카드용 통계 반환.

    Args:
        clinic_id: 지정 시 해당 클리닉의 entry만 카운트. None이면 전체 (어드민용).
        since: ISO datetime. 지정 시 이 시점 이후 created_at만 카운트 (베타 가입일 등).

    반환 형식:
    {
        "total": 누적 생성 수,
        "this_month": 이번 달 생성 수,
        "recent_keywords": [최근 3개 주제],
        "last_created_at": "2026-04-16T14:30:00" | null
    }
    """
    stats = _load_json(STATS_PATH, default=[])
    if not stats:
        return {"total": 0, "this_month": 0, "recent_keywords": [], "last_created_at": None}

    # 클리닉 필터 (clinic_id None인 옛 entry는 본인 클리닉으로 안 잡힘)
    if clinic_id is not None:
        stats = [e for e in stats if e.get("clinic_id") == clinic_id]
    # since 필터 (베타 가입일 이후만)
    if since:
        try:
            since_dt = _parse_dt(since)
            stats = [e for e in stats if _parse_dt(e["created_at"]) >= since_dt]
        except Exception:
            pass

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
    [{"id": 1, "keyword": "소화불량", "title": "...", "naver_url": "https://...", "created_at": "..."}]
    """
    stats = _load_json(STATS_PATH, default=[])
    recent = stats[-limit:] if len(stats) >= limit else stats
    return [
        {
            "id": e["id"],
            "keyword": e["keyword"],
            "title": e.get("title", e["keyword"]),
            "seo_keywords": e.get("seo_keywords", []),
            "naver_url": e.get("naver_url", ""),
            "created_at": e["created_at"],
        }
        for e in reversed(recent)
    ]


def update_naver_url(entry_id: int, url: str) -> bool:
    """특정 블로그 항목에 네이버 발행 URL 저장. 성공 True, 없으면 False."""
    stats = _load_json(STATS_PATH, default=[])
    for entry in stats:
        if entry["id"] == entry_id:
            entry["naver_url"] = url
            _save_json(STATS_PATH, stats)
            return True
    return False


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


def get_history_list(clinic_id: int, page: int = 1, per_page: int = 20) -> dict:
    """
    설정 페이지용 블로그 생성 이력 반환 (최신순, 페이지네이션)

    K-6 보안: clinic_id 필터 강제 — 본인 클리닉 entry만 노출.
    레거시 entry(clinic_id=None)는 어떤 일반 사용자에게도 보이지 않음.

    반환 형식:
    {
        "total": 전체 수,
        "items": [{"id", "keyword", "title", "tone", "char_count",
                   "seo_keywords", "created_at", "has_text", "expires_at"}]
    }
    """
    stats = _load_json(STATS_PATH, default=[])
    # 본인 클리닉만 — 레거시 None은 자동 제외
    stats = [e for e in stats if e.get("clinic_id") == clinic_id]
    texts = _load_json(TEXTS_PATH, default=[])
    text_map = {t["id"]: t for t in texts}

    now = datetime.now()
    sorted_stats = list(reversed(stats))
    total = len(sorted_stats)
    start = (page - 1) * per_page
    items = []
    for e in sorted_stats[start: start + per_page]:
        entry_id = e["id"]
        text_entry = text_map.get(entry_id)
        has_text = bool(
            text_entry
            and _parse_dt(text_entry["expires_at"]) > now
        )
        items.append({
            "id": entry_id,
            "keyword": e["keyword"],
            "title": e.get("title", e["keyword"]),
            "tone": e.get("tone", ""),
            "char_count": e.get("char_count", 0),
            "seo_keywords": e.get("seo_keywords", []),
            "created_at": e["created_at"],
            "has_text": has_text,
            "expires_at": text_entry["expires_at"] if has_text else None,
        })
    return {"total": total, "items": items}


def get_blog_text(entry_id: int, clinic_id: int) -> Optional[str]:
    """특정 항목의 전문 반환 (만료됐거나 없거나 타 클리닉이면 None).

    K-6 보안: stats를 거쳐 ownership 검증. blog_texts.json에는 clinic_id가
    없어 stats가 단일 진실원. 레거시 entry(clinic_id=None)는 차단.
    """
    if not _entry_owned_by(entry_id, clinic_id):
        return None
    texts = _load_json(TEXTS_PATH, default=[])
    now = datetime.now()
    for t in texts:
        if t.get("id") == entry_id and _parse_dt(t["expires_at"]) > now:
            return t.get("blog_text")
    return None


def get_text_expiry_info(entry_id: int, clinic_id: int) -> Optional[str]:
    """특정 항목의 전문 만료 일시 반환 (없거나 타 클리닉이면 None)."""
    if not _entry_owned_by(entry_id, clinic_id):
        return None
    texts = _load_json(TEXTS_PATH, default=[])
    for t in texts:
        if t.get("id") == entry_id:
            return t.get("expires_at")
    return None


def _entry_owned_by(entry_id: int, clinic_id: int) -> bool:
    """K-6 ownership 검증 헬퍼. 레거시 clinic_id=None은 어떤 비교에서도 False."""
    stats = _load_json(STATS_PATH, default=[])
    return any(
        e.get("id") == entry_id and e.get("clinic_id") == clinic_id
        for e in stats
    )


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
