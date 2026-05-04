"""
exchange_rate.py — USD→KRW 환율 조회 (어드민 비용 KPI 전용, Commit 8a 2026-05-04)

조회 우선순위 (3중 안전망):
  1. 한국수출입은행 매매기준율 API
     - GET https://www.koreaexim.go.kr/site/program/financial/exchangeJSON
       ?authkey={KEY}&searchdate={YYYYMMDD}&data=AP01
     - USD row의 deal_bas_r 사용 (콤마 포함 문자열 — "1,400.50")
     - 영업일만 갱신 → 공휴일/주말은 직전 영업일까지 최대 7일 거슬러 시도
  2. 24h 디스크 캐시 (`data/exchange_rate_cache.json`)
     - API 응답 무관 마지막 성공값 보존
  3. config.yaml `pricing.usd_to_krw_fallback` (기본 1400원)

Fail-soft 원칙:
  - 외부 호출 실패·키 미설정·DNS 등 예외 모두 흡수 → fallback 값 리턴
  - 어드민 KPI 표시 외 비즈니스 로직 차단 금지

호출자: routers/admin.py (KPI cost 패널)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = ROOT / "data" / "exchange_rate_cache.json"
_CONFIG_PATH = ROOT / "config.yaml"

# 한국수출입은행 환율정보 API 호스트 — 2024년 이후 oapi.* 서브도메인으로 이전.
# 기존 www.* 는 deprecate. 변경 시 본 상수만 갱신.
_KOREAEXIM_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
_API_TIMEOUT_SEC = 5.0
_LOOKBACK_DAYS = 7   # 영업일 외 직전 영업일까지 최대 거슬러 시도


def _load_pricing_config() -> dict:
    """config.yaml pricing 섹션 로드. 실패 시 빈 dict."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("pricing", {}) or {}
    except Exception as exc:
        logger.warning("exchange_rate: config.yaml 로드 실패 (%s)", exc)
        return {}


def _fallback_rate() -> float:
    """config.yaml 또는 상수 fallback."""
    cfg = _load_pricing_config()
    try:
        return float(cfg.get("usd_to_krw_fallback", 1400))
    except (TypeError, ValueError):
        return 1400.0


def _cache_ttl_hours() -> int:
    cfg = _load_pricing_config()
    try:
        return max(1, int(cfg.get("exchange_rate_cache_hours", 24)))
    except (TypeError, ValueError):
        return 24


def _read_cache() -> Optional[dict]:
    """캐시 파일 읽기. 없거나 손상 시 None."""
    try:
        with open(_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    except Exception as exc:
        logger.warning("exchange_rate: 캐시 읽기 실패 (%s)", exc)
        return None


def _write_cache(payload: dict) -> None:
    """캐시 파일 쓰기 (data/ 디렉토리 자동 생성). 실패 시 silent."""
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("exchange_rate: 캐시 쓰기 실패 (%s)", exc)


def _is_cache_fresh(cached: dict) -> bool:
    """fetched_at 기준 TTL 이내인지."""
    fetched = cached.get("fetched_at")
    if not fetched:
        return False
    try:
        dt = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - dt
        return age < timedelta(hours=_cache_ttl_hours())
    except ValueError:
        return False


def _parse_deal_bas_r(raw: Any) -> Optional[float]:
    """한국수출입은행 응답 deal_bas_r 콤마 포함 문자열 → float."""
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_from_koreaexim(api_key: str, search_date: datetime) -> Optional[dict]:
    """한 영업일분 API 호출. USD row 발견 시 dict 리턴, 아니면 None.

    응답 형식 예 (영업일):
        [{"cur_unit": "USD", "deal_bas_r": "1,400.50", ...}, {...}]
    공휴일/주말:
        []  또는  [{"result": 4, ...}]
    """
    params = {
        "authkey": api_key,
        "searchdate": search_date.strftime("%Y%m%d"),
        "data": "AP01",
    }
    try:
        resp = httpx.get(_KOREAEXIM_URL, params=params, timeout=_API_TIMEOUT_SEC)
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("exchange_rate: 수출입은행 API 호출 실패 (%s)", exc)
        return None
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("exchange_rate: 수출입은행 API 응답 파싱 실패 (%s)", exc)
        return None

    if not isinstance(body, list) or not body:
        return None  # 영업일 외

    for row in body:
        if not isinstance(row, dict):
            continue
        if str(row.get("cur_unit", "")).strip() != "USD":
            continue
        rate = _parse_deal_bas_r(row.get("deal_bas_r"))
        if rate is None or rate <= 0:
            continue
        return {
            "rate": round(rate, 2),
            "date": search_date.strftime("%Y-%m-%d"),
            "source": "koreaexim",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    return None


def get_usd_to_krw(force_refresh: bool = False) -> dict:
    """USD→KRW 환율 조회.

    Args:
        force_refresh: True 면 캐시 무시하고 API 재호출.

    Returns:
        {
          "rate": 1400.5,           # KRW per 1 USD
          "date": "2026-05-04",     # 환율 기준일 (영업일)
          "source": "koreaexim" | "fallback",   # 원본 출처 (캐시 hit 시도 보존)
          "fetched_at": "ISO8601 UTC",          # 원본 받아온 시각
          "cached": bool,                       # True = 캐시에서 읽음 (외부 호출 안 함)
        }
    """
    # 1) 캐시 신선도 확인 (force_refresh=False 일 때만)
    if not force_refresh:
        cached = _read_cache()
        cached_dict = _cached_response(cached, fresh=True)
        if cached_dict:
            return cached_dict

    # 2) API 키 있으면 영업일 거슬러 시도
    api_key = os.getenv("KOREAEXIM_API_KEY", "").strip()
    if api_key:
        today = datetime.now(timezone.utc)
        for delta in range(_LOOKBACK_DAYS):
            target = today - timedelta(days=delta)
            result = _fetch_from_koreaexim(api_key, target)
            if result:
                _write_cache(result)
                result["cached"] = False
                return result

    # 3) 캐시 (만료됐어도) 마지막 성공값 우선
    cached_dict = _cached_response(_read_cache(), fresh=False)
    if cached_dict:
        return cached_dict

    # 4) 최종 fallback
    return {
        "rate": _fallback_rate(),
        "date": "",
        "source": "fallback",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
    }


def _cached_response(cached: Optional[dict], *, fresh: bool) -> Optional[dict]:
    """캐시 dict 를 응답 형태로 변환. 원본 source 보존 + cached=True 플래그."""
    if not cached:
        return None
    rate = cached.get("rate")
    try:
        rate_f = float(rate) if rate is not None else None
    except (TypeError, ValueError):
        rate_f = None
    if not rate_f or rate_f <= 0:
        return None
    return {
        "rate": rate_f,
        "date": str(cached.get("date") or ""),
        "source": str(cached.get("source") or "koreaexim"),
        "fetched_at": str(cached.get("fetched_at") or ""),
        "cached": True,
    }


def to_krw(usd: float, rate: Optional[float] = None) -> float:
    """USD → KRW 환산. rate 미지정 시 get_usd_to_krw() 호출.

    어드민 KPI 페이지에서 N개 행을 변환할 때는 rate 를 한 번 조회 후 인자로 전달
    (반복 캐시 hit 회피 + 일관 환율).
    """
    try:
        amount = float(usd or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if rate is None:
        rate = float(get_usd_to_krw().get("rate", _fallback_rate()))
    try:
        return round(amount * float(rate), 2)
    except (TypeError, ValueError):
        return 0.0
