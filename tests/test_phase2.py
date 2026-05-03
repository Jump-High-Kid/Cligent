"""
test_phase2.py — Phase 2 기능 단위 테스트

태스크 1: create_clinic() — trial_expires_at 1회 설정
태스크 2: plan_notify — 한도 80% 알림 로직
태스크 3: /api/settings/plan/usage 엔드포인트
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ═══════════════════════════════════════════════════════════════════
# 태스크 1: create_clinic() — trial_expires_at 훅
# ═══════════════════════════════════════════════════════════════════

class TestCreateClinic:
    """create_clinic() — trial_expires_at 1회 자동 설정"""

    def _make_mock_conn(self, lastrowid=42):
        """INSERT를 캡처하는 mock 커넥션 생성."""
        inserted_rows = []
        cur = MagicMock()
        cur.lastrowid = lastrowid

        conn = MagicMock()
        conn.execute = MagicMock(return_value=cur)
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        return conn, inserted_rows

    def test_create_clinic_sets_trial_expires_at(self):
        """create_clinic() 호출 시 trial_expires_at이 INSERT에 포함되어야 한다."""
        conn, _ = self._make_mock_conn()

        @contextmanager
        def mock_get_db():
            yield conn

        with patch("db_manager.get_db", mock_get_db):
            from db_manager import create_clinic
            clinic_id = create_clinic("테스트 한의원")

        # INSERT가 호출됐는지 확인
        assert conn.execute.called
        call_args = conn.execute.call_args
        sql, params = call_args[0]
        assert "trial_expires_at" in sql
        assert len(params) == 3  # name, max_slots, trial_expires_at

        # trial_expires_at 값이 config.yaml beta.trial_days 뒤인지 확인
        from plan_guard import _TRIAL_DAYS
        trial_val = params[2]
        trial_dt = datetime.fromisoformat(trial_val.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        diff = trial_dt - now
        # 시계 기준 1일 마진 허용 (테스트 실행 시점 ↔ create_clinic 호출 시점 차)
        assert _TRIAL_DAYS - 1 <= diff.days <= _TRIAL_DAYS, (
            f"trial_expires_at이 {_TRIAL_DAYS}일 범위 밖: {diff.days}일"
        )

    def test_create_clinic_returns_lastrowid(self):
        """create_clinic()이 clinic_id(lastrowid)를 반환해야 한다."""
        conn, _ = self._make_mock_conn(lastrowid=99)

        @contextmanager
        def mock_get_db():
            yield conn

        with patch("db_manager.get_db", mock_get_db):
            from db_manager import create_clinic
            clinic_id = create_clinic("테스트 한의원", max_slots=10)

        assert clinic_id == 99

    def test_trial_set_only_once_no_overwrite_in_source(self):
        """
        create_clinic 소스 코드에 trial_expires_at UPDATE 경로가 없어야 한다
        (trial abuse 방어: 재설정 불가).
        """
        import db_manager as dm
        source = Path(dm.__file__).read_text(encoding="utf-8")
        # UPDATE clinics SET trial_expires_at 형태의 SQL이 없어야 함
        assert "UPDATE clinics SET trial_expires_at" not in source, \
            "trial_expires_at을 UPDATE하는 코드가 발견됨 — trial abuse 위험"


# ═══════════════════════════════════════════════════════════════════
# 태스크 2: plan_notify — check_and_notify
# ═══════════════════════════════════════════════════════════════════

class TestPlanNotify:
    """plan_notify.py — 한도 80% 이메일 알림"""

    def setup_method(self):
        """각 테스트 전 알림 기록 초기화."""
        from plan_notify import _notified
        _notified.clear()

    def test_check_and_notify_spawns_thread(self):
        """check_and_notify()가 스레드를 실행해야 한다."""
        from plan_notify import check_and_notify
        with patch("plan_notify._notify_worker") as mock_worker:
            with patch("threading.Thread") as mock_thread_cls:
                mock_thread = MagicMock()
                mock_thread_cls.return_value = mock_thread
                check_and_notify(clinic_id=1)
                mock_thread.start.assert_called_once()

    def test_notify_worker_skips_when_already_notified(self):
        """이번 달 이미 알림 발송 시 _notify_worker가 조기 종료해야 한다."""
        from plan_notify import _notify_worker, _mark_notified
        _mark_notified(clinic_id=10)  # 이미 발송 기록

        with patch("plan_notify._get_usage_info") as mock_info:
            _notify_worker(clinic_id=10)
            mock_info.assert_not_called()  # 사용량 조회 자체가 없어야 함

    def test_notify_worker_skips_when_unlimited_plan(self):
        """무제한 플랜(limit=None)이면 이메일 발송 없이 종료."""
        from plan_notify import _notify_worker
        mock_info = {"plan_id": "standard", "limit": None, "used": 5}

        with patch("plan_notify._get_usage_info", return_value=mock_info), \
             patch("plan_notify._send_email") as mock_send:
            _notify_worker(clinic_id=20)
            mock_send.assert_not_called()

    def test_notify_worker_skips_when_below_threshold(self):
        """사용량이 79% 이하면 이메일 미발송."""
        from plan_notify import _notify_worker
        mock_info = {"plan_id": "free", "limit": 10, "used": 7}  # 70%

        with patch("plan_notify._get_usage_info", return_value=mock_info), \
             patch("plan_notify._send_email") as mock_send:
            _notify_worker(clinic_id=30)
            mock_send.assert_not_called()

    def test_notify_worker_sends_when_at_threshold(self):
        """사용량 80% 이상이면 이메일 발송 및 발송 기록 저장."""
        from plan_notify import _notify_worker, _already_notified
        mock_info = {"plan_id": "free", "limit": 10, "used": 8}  # 80%

        with patch("plan_notify._get_usage_info", return_value=mock_info), \
             patch("plan_notify._get_clinic_email", return_value="owner@test.com"), \
             patch("plan_notify._send_email") as mock_send:
            _notify_worker(clinic_id=40)

        mock_send.assert_called_once_with("owner@test.com", 40, 8, 10)
        assert _already_notified(40), "발송 후 중복 방지 기록이 없음"

    def test_notify_worker_skips_when_no_email(self):
        """수신자 이메일 없으면 이메일 미발송."""
        from plan_notify import _notify_worker
        mock_info = {"plan_id": "free", "limit": 3, "used": 3}  # 100%

        with patch("plan_notify._get_usage_info", return_value=mock_info), \
             patch("plan_notify._get_clinic_email", return_value=None), \
             patch("plan_notify._send_email") as mock_send:
            _notify_worker(clinic_id=50)
            mock_send.assert_not_called()

    def test_send_email_logs_only_when_smtp_not_configured(self):
        """SMTP_HOST 없으면 _send_email이 로그만 남기고 조용히 종료."""
        from plan_notify import _send_email
        with patch.dict("os.environ", {}, clear=True):
            # 예외 없이 종료되어야 함
            _send_email("owner@test.com", 1, 2, 3)

    def test_notify_worker_does_not_raise_on_unexpected_error(self):
        """예상치 못한 예외가 발생해도 스레드 크래시 없이 종료."""
        from plan_notify import _notify_worker
        with patch("plan_notify._get_usage_info", side_effect=RuntimeError("예기치 않은 오류")):
            # 예외가 올라오면 안 됨
            _notify_worker(clinic_id=60)


# ═══════════════════════════════════════════════════════════════════
# 태스크 3: /api/settings/plan/usage 엔드포인트
# (기존 프로젝트 패턴과 동일: TestClient + 실제 auth 쿠키 + db_manager.get_db 패치)
# ═══════════════════════════════════════════════════════════════════

from fastapi.testclient import TestClient
from src.main import app
import auth_manager as _auth_manager

# Depends(get_current_user) 오버라이드용 모의 사용자
_MOCK_USER = {"id": 1, "clinic_id": 1, "email": "owner@test.com", "role": "chief_director"}


def _override_get_current_user():
    return _MOCK_USER


# 인증 우회 TestClient
_api_client = TestClient(app)
app.dependency_overrides[_auth_manager.get_current_user] = _override_get_current_user


def _make_mock_conn_for_plan(clinic_row_dict, used_cnt: int):
    """
    plan/usage 엔드포인트가 호출하는 2번의 conn.execute()를 mock.
    첫 번째: 클리닉 정보, 두 번째: 사용량 카운트.
    """
    mock_clinic = MagicMock()
    mock_clinic.__getitem__ = lambda self, k: clinic_row_dict[k]

    mock_usage = MagicMock()
    mock_usage.__getitem__ = lambda self, k: {"cnt": used_cnt}[k]

    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=mock_clinic)),
        MagicMock(fetchone=MagicMock(return_value=mock_usage)),
    ]
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    return mock_conn


class TestPlanUsageEndpoint:
    """GET /api/settings/plan/usage 엔드포인트"""

    def test_plan_usage_requires_auth(self):
        """인증 없이 접근 시 401 반환 (dependency_overrides 미적용 fresh client)."""
        fresh_app_copy = app
        # 오버라이드 임시 제거
        saved = fresh_app_copy.dependency_overrides.pop(_auth_manager.get_current_user, None)
        try:
            fresh_client = TestClient(fresh_app_copy, raise_server_exceptions=False)
            res = fresh_client.get("/api/settings/plan/usage")
            assert res.status_code == 401
        finally:
            if saved is not None:
                fresh_app_copy.dependency_overrides[_auth_manager.get_current_user] = saved

    def test_plan_usage_returns_ok_with_auth(self):
        """인증된 사용자는 200 + 필수 필드를 반환해야 한다."""
        res = _api_client.get("/api/settings/plan/usage")
        assert res.status_code == 200
        data = res.json()
        assert "plan_id" in data
        assert "plan_name" in data
        assert "used_this_month" in data
        assert "usage_pct" in data
        assert data["plan_id"] in ("free", "trial", "standard", "pro")

    def test_plan_usage_free_plan_has_limit(self):
        """free 플랜이면 monthly_limit=3, used_this_month=1이 반환되어야 한다."""
        mock_clinic = MagicMock()
        mock_clinic.__getitem__ = lambda self, k: {
            "plan_id": "free", "plan_expires_at": None,
            "trial_expires_at": None, "plan_name": "무료",
            "monthly_blog_limit": 3,
        }[k]
        mock_usage = MagicMock()
        mock_usage.__getitem__ = lambda self, k: {"cnt": 1}[k]

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=mock_clinic)),
            MagicMock(fetchone=MagicMock(return_value=mock_usage)),
        ]
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_get_db():
            yield mock_conn

        with patch("db_manager.get_db", mock_get_db):
            res = _api_client.get("/api/settings/plan/usage")

        assert res.status_code == 200
        data = res.json()
        assert data["plan_id"] == "free"
        assert data["monthly_limit"] == 3
        assert data["used_this_month"] == 1

    def test_plan_usage_trial_has_days_left(self):
        """trial 플랜이면 trial_days_left가 양수여야 한다."""
        trial_exp = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

        mock_clinic = MagicMock()
        mock_clinic.__getitem__ = lambda self, k: {
            "plan_id": "free", "plan_expires_at": None,
            "trial_expires_at": trial_exp, "plan_name": "무료",
            "monthly_blog_limit": 3,
        }[k]
        mock_usage = MagicMock()
        mock_usage.__getitem__ = lambda self, k: {"cnt": 0}[k]

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=mock_clinic)),
            MagicMock(fetchone=MagicMock(return_value=mock_usage)),
        ]
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_get_db():
            yield mock_conn

        with patch("db_manager.get_db", mock_get_db):
            res = _api_client.get("/api/settings/plan/usage")

        assert res.status_code == 200
        data = res.json()
        assert data["plan_id"] == "trial"
        assert data["plan_name"] == "무료 체험"
        assert data["trial_days_left"] >= 6

    def test_plan_usage_standard_plan_unlimited(self):
        """standard 플랜이면 monthly_limit=None, usage_pct=0."""
        now_plus = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        mock_clinic = MagicMock()
        mock_clinic.__getitem__ = lambda self, k: {
            "plan_id": "standard", "plan_expires_at": now_plus,
            "trial_expires_at": None, "plan_name": "스탠다드",
            "monthly_blog_limit": None,
        }[k]
        mock_usage = MagicMock()
        mock_usage.__getitem__ = lambda self, k: {"cnt": 5}[k]

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=mock_clinic)),
            MagicMock(fetchone=MagicMock(return_value=mock_usage)),
        ]
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        @contextmanager
        def mock_get_db():
            yield mock_conn

        with patch("db_manager.get_db", mock_get_db):
            res = _api_client.get("/api/settings/plan/usage")

        assert res.status_code == 200
        data = res.json()
        assert data["plan_id"] == "standard"
        assert data["monthly_limit"] is None
        assert data["usage_pct"] == 0
