"""
test_plan_guard.py — plan_guard.py 단위 테스트

테스트 시나리오:
  1. 유료 플랜 활성 → 통과
  2. 체험 플랜 활성 → 통과
  3. 무료 한도 미달 → 통과
  4. 무료 한도 초과 → 429 발생
  5. plan_expires_at 만료 → free 처리 (한도 체크)
  6. trial_expires_at 만료 → free 처리 (한도 체크)
  7. DB 장애 + 캐시 hit → 통과
  8. DB 장애 + 캐시 miss → fail open 통과
  9. trial 재활성화 경로 없음 확인
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from plan_guard import (
    _plan_cache,
    _set_cache,
    check_blog_limit,
    invalidate_plan_cache,
)


def _iso(delta_hours: int = 0) -> str:
    """현재 UTC 시각 + delta_hours 를 ISO8601 문자열로 반환."""
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


@pytest.fixture(autouse=True)
def clear_cache():
    """각 테스트 전후 캐시 초기화."""
    _plan_cache.clear()
    yield
    _plan_cache.clear()


# ── Helpers ───────────────────────────────────────────────────────────

def _mock_plan(plan_expires_at=None, trial_expires_at=None):
    return {
        "plan_id": "free",
        "plan_expires_at": plan_expires_at,
        "trial_expires_at": trial_expires_at,
    }


# ── 테스트 ────────────────────────────────────────────────────────────

class TestPaidPlan:
    def test_active_paid_plan_allows_access(self):
        """유료 플랜 만료 전 → 한도 체크 없이 통과."""
        data = _mock_plan(plan_expires_at=_iso(+24))
        with patch("plan_guard._fetch_plan_data", return_value=data):
            check_blog_limit(clinic_id=1)  # 예외 없음


class TestTrialPlan:
    def test_active_trial_allows_access(self):
        """체험 플랜 만료 전 → 통과."""
        data = _mock_plan(trial_expires_at=_iso(+72))
        with patch("plan_guard._fetch_plan_data", return_value=data):
            check_blog_limit(clinic_id=2)  # 예외 없음

    def test_trial_cannot_be_reactivated(self):
        """plan_guard 내에 DB에 trial_expires_at을 쓰는 SQL이 없어야 한다."""
        import plan_guard as pg
        source = Path(pg.__file__).read_text(encoding="utf-8")
        # DB UPDATE/INSERT로 trial을 재설정하는 패턴만 금지
        forbidden_sql = [
            "UPDATE clinics SET trial_expires_at",
            "INSERT INTO clinics",
        ]
        for pattern in forbidden_sql:
            assert pattern not in source, f"trial 재설정 SQL 발견: {pattern!r}"


class TestFreePlan:
    def test_under_limit_allows_access(self):
        """베타 무료 플랜, 누적 9편 생성 → 통과 (한도 10편)."""
        data = _mock_plan()
        with patch("plan_guard._fetch_plan_data", return_value=data), \
             patch("plan_guard._count_total_blogs", return_value=9):
            check_blog_limit(clinic_id=3)  # 예외 없음

    def test_over_limit_raises_429(self):
        """베타 무료 플랜, 누적 10편 생성 → 429 발생."""
        data = _mock_plan()
        with patch("plan_guard._fetch_plan_data", return_value=data), \
             patch("plan_guard._count_total_blogs", return_value=10):
            with pytest.raises(HTTPException) as exc_info:
                check_blog_limit(clinic_id=4)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["error"] == "plan_limit_exceeded"
        assert exc_info.value.detail["current"] == 10
        assert exc_info.value.detail["limit"] == 10


class TestExpiredPlans:
    def test_expired_paid_plan_falls_to_free(self):
        """유료 플랜 만료 후 free 처리 → 누적 한도 초과 시 429."""
        data = _mock_plan(plan_expires_at=_iso(-1))
        with patch("plan_guard._fetch_plan_data", return_value=data), \
             patch("plan_guard._count_total_blogs", return_value=10):
            with pytest.raises(HTTPException) as exc_info:
                check_blog_limit(clinic_id=5)
        assert exc_info.value.status_code == 429

    def test_expired_trial_falls_to_free(self):
        """체험 플랜 만료 후 free 처리 → 누적 한도 초과 시 429."""
        data = _mock_plan(trial_expires_at=_iso(-1))
        with patch("plan_guard._fetch_plan_data", return_value=data), \
             patch("plan_guard._count_total_blogs", return_value=10):
            with pytest.raises(HTTPException) as exc_info:
                check_blog_limit(clinic_id=6)
        assert exc_info.value.status_code == 429


class TestFailOpen:
    def test_db_failure_with_cache_hit_allows(self):
        """DB 장애 + 캐시 hit → 캐시에서 응답, 유료 플랜이면 통과."""
        # 캐시에 유료 플랜 정보 직접 삽입
        _set_cache(7, _mock_plan(plan_expires_at=_iso(+24)))
        # get_db는 lazy import라 db_manager 모듈에서 패치해야 함
        with patch("db_manager.get_db", side_effect=Exception("DB 연결 실패")):
            check_blog_limit(clinic_id=7)  # 캐시 hit → 예외 없음

    def test_db_failure_cache_miss_fail_open(self):
        """DB 장애 + 캐시 miss → fail open (예외 없이 통과)."""
        with patch("plan_guard._fetch_plan_data", return_value=None):
            check_blog_limit(clinic_id=8)  # fail open → 예외 없음

    def test_usage_count_failure_fail_open(self):
        """사용량 조회 실패 → fail open (예외 없이 통과)."""
        data = _mock_plan()  # 무료 플랜
        with patch("plan_guard._fetch_plan_data", return_value=data), \
             patch("plan_guard._count_monthly_blogs", return_value=-1):
            check_blog_limit(clinic_id=9)  # fail open → 예외 없음
