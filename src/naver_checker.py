"""
naver_checker.py — 네이버 블로그 검색 API로 발행 포스트 색인 확인

폴링 주기:
  1회차:       1시간 후
  2~6회차:     2시간 간격  (최대 11시간)
  7~10회차:    6시간 간격  (최대 35시간)
  11회차 이후: 12시간 간격 (최대 7일)
  7일 경과:    포기 (expired) → 이메일 알림
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"

PENDING_PATH = Path(__file__).parent.parent / "data" / "pending_checks.json"
APP_SETTINGS_PATH = Path(__file__).parent.parent / "data" / "app_settings.json"
MAX_CHECK_DAYS = 7


def _get_naver_credentials() -> tuple:
    """data/app_settings.json 우선, 없으면 env 폴백."""
    try:
        if APP_SETTINGS_PATH.exists():
            with open(APP_SETTINGS_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            cid = cfg.get("naver_client_id", "").strip()
            csec = cfg.get("naver_client_secret", "").strip()
            if cid and csec:
                return cid, csec
    except Exception:
        pass
    return os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")


def _next_delay_minutes(check_count: int) -> int:
    if check_count == 0:
        return 60
    if check_count < 6:
        return 120
    if check_count < 10:
        return 360
    return 720


def is_naver_configured() -> bool:
    cid, csec = _get_naver_credentials()
    return bool(cid and csec)


def add_pending_check(
    blog_stat_id: int,
    keyword: str,
    title: str,
    naver_blog_id: str,
) -> dict:
    """발행 확인 대기 항목 추가. 이미 pending 중이면 기존 항목 반환."""
    items = _load()
    for item in items:
        if item["blog_stat_id"] == blog_stat_id and item["status"] == "pending":
            return item

    now = datetime.now()
    new_item = {
        "id": _next_id(items),
        "blog_stat_id": blog_stat_id,
        "keyword": keyword,
        "title": title,
        "naver_blog_id": naver_blog_id,
        "started_at": now.isoformat(),
        "next_check_at": (now + timedelta(minutes=_next_delay_minutes(0))).isoformat(),
        "status": "pending",
        "found_url": None,
        "check_count": 0,
        "notified": False,
    }
    items.append(new_item)
    _save(items)
    return new_item


def search_naver_blog(query: str, naver_blog_id: str, title: str) -> Optional[str]:
    """Naver 검색 API로 URL 탐색. 매칭되면 URL 반환, 없으면 None."""
    naver_client_id, naver_client_secret = _get_naver_credentials()
    if not naver_client_id or not naver_client_secret:
        return None
    try:
        import httpx
        headers = {
            "X-Naver-Client-Id": naver_client_id,
            "X-Naver-Client-Secret": naver_client_secret,
        }
        params = {"query": query, "display": 10, "sort": "date"}

        with httpx.Client(timeout=10) as client:
            resp = client.get(_SEARCH_URL, headers=headers, params=params)

        if resp.status_code != 200:
            return None

        title_key = title.replace(" ", "").lower()[:15]
        for item in resp.json().get("items", []):
            blogger_link = item.get("bloggerlink", "")
            if naver_blog_id.lower() not in blogger_link.lower():
                continue
            raw_title = item.get("title", "")
            item_title = raw_title.replace("<b>", "").replace("</b>", "").replace(" ", "").lower()
            if title_key[:8] in item_title:
                return item.get("link", "")
        return None
    except Exception:
        return None


def run_pending_checks() -> list:
    """next_check_at 경과한 pending 항목 폴링. 새로 found된 항목 반환."""
    now = datetime.now()
    items = _load()
    found_items: list = []
    changed = False

    for item in items:
        if item["status"] != "pending":
            continue
        if now < datetime.fromisoformat(item["next_check_at"]):
            continue

        started = datetime.fromisoformat(item["started_at"])
        if (now - started).days >= MAX_CHECK_DAYS:
            item["status"] = "expired"
            changed = True
            continue

        url = search_naver_blog(
            query=item["keyword"],
            naver_blog_id=item["naver_blog_id"],
            title=item["title"],
        )
        item["check_count"] += 1
        changed = True

        if url:
            item["status"] = "found"
            item["found_url"] = url
            found_items.append(item)
        else:
            delay = _next_delay_minutes(item["check_count"])
            item["next_check_at"] = (now + timedelta(minutes=delay)).isoformat()

    if changed:
        _save(items)
    return found_items


def get_unnotified(status: str) -> list:
    """특정 status 중 아직 알림 미발송 항목 반환."""
    return [i for i in _load() if i["status"] == status and not i.get("notified", False)]


def mark_notified(pending_id: int) -> None:
    items = _load()
    for item in items:
        if item["id"] == pending_id:
            item["notified"] = True
            break
    _save(items)


def get_dashboard_notifications() -> list:
    """대시보드 알림 — found + expired 중 notified=False"""
    return [
        i for i in _load()
        if i["status"] in ("found", "expired") and not i.get("notified", False)
    ]


def get_pending_by_stat_id(blog_stat_id: int) -> Optional[dict]:
    """특정 블로그 항목의 pending_check 조회."""
    for item in _load():
        if item["blog_stat_id"] == blog_stat_id:
            return item
    return None


# ── 내부 헬퍼 ──────────────────────────────────────────────────────────
def _next_id(items: list) -> int:
    return max((i["id"] for i in items), default=0) + 1


def _load() -> list:
    if not PENDING_PATH.exists():
        return []
    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: list) -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
