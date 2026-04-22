"""
test_p0_p1.py — P0/P1 항목 단위 테스트

- resolve_effective_plan() 순수 함수 동작 검증
- plan_notify: trial/paid 무제한 알림 skip
- plan_notify: 유효하지 않은 이메일 skip
- /api/admin/clinic 엔드포인트 (ADMIN_SECRET 게이트)
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _future(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ═══════════════════════════════════════════════════════════════════
# resolve_effective_plan() — 순수 함수 단위 테스트
# ═══════════════════════════════════════════════════════════════════

class TestResolveEffectivePlan:
    """plan_guard.resolve_effective_plan() — 플랜 우선순위 로직"""

    def _call(self, plan_id=None, plan_expires_at=None, trial_expires_at=None):
        from plan_guard import resolve_effective_plan
        return resolve_effective_plan(plan_id, plan_expires_at, trial_expires_at)

    def test_paid_plan_active(self):
        """plan_expires_at이 미래면 유료 플랜, has_unlimited=True."""
        result = self._call("standard", _future(30), None)
        assert result["plan_id"] == "standard"
        assert result["is_paid"] is True
        assert result["is_trial"] is False
        assert result["has_unlimited"] is True
        assert result["trial_days_left"] is None

    def test_paid_plan_expired_falls_to_trial(self):
        """plan_expires_at 만료 + trial 유효 → trial 플랜."""
        result = self._call("standard", _past(1), _future(7))
        assert result["plan_id"] == "trial"
        assert result["is_trial"] is True
        assert result["has_unlimited"] is True
        assert result["trial_days_left"] is not None
        assert result["trial_days_left"] >= 6

    def test_trial_active(self):
        """plan_expires_at 없음 + trial 유효 → trial, has_unlimited=True."""
        result = self._call(None, None, _future(10))
        assert result["plan_id"] == "trial"
        assert result["is_trial"] is True
        assert result["has_unlimited"] is True

    def test_trial_expired_falls_to_free(self):
        """trial 만료 → free, has_unlimited=False."""
        result = self._call("free", None, _past(1))
        assert result["plan_id"] == "free"
        assert result["is_paid"] is False
        assert result["is_trial"] is False
        assert result["has_unlimited"] is False

    def test_no_plans_returns_free(self):
        """모두 None → free."""
        result = self._call(None, None, None)
        assert result["plan_id"] == "free"
        assert result["has_unlimited"] is False

    def test_paid_takes_priority_over_trial(self):
        """plan_expires_at과 trial_expires_at 둘 다 유효 → 유료 우선."""
        result = self._call("pro", _future(30), _future(10))
        assert result["plan_id"] == "pro"
        assert result["is_paid"] is True

    def test_trial_days_left_calculated(self):
        """trial_days_left이 올바른 일수를 반환해야 한다."""
        result = self._call(None, None, _future(5))
        assert result["trial_days_left"] in (4, 5)

    def test_trial_days_left_zero_when_expires_today(self):
        """만료 당일 trial_days_left=0이어야 한다."""
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = self._call(None, None, expires)
        assert result["trial_days_left"] == 0


# ═══════════════════════════════════════════════════════════════════
# plan_notify: trial/paid 무제한 skip 테스트
# ═══════════════════════════════════════════════════════════════════

class TestNotifySkipsUnlimitedPlans:
    """trial/paid 플랜 클리닉에는 80% 알림을 보내지 않아야 한다."""

    def setup_method(self):
        from plan_notify import _notified
        _notified.clear()

    def _make_usage_info(self, plan_id="free", plan_expires_at=None, trial_expires_at=None, limit=3, used=3):
        return {
            "plan_id": plan_id,
            "plan_expires_at": plan_expires_at,
            "trial_expires_at": trial_expires_at,
            "limit": limit,
            "used": used,
        }

    def test_trial_plan_does_not_send_email(self):
        """체험 플랜 클리닉은 한도에 도달해도 이메일을 보내지 않아야 한다."""
        info = self._make_usage_info(
            trial_expires_at=_future(10),
            used=3, limit=3,
        )
        with patch("plan_notify._get_usage_info", return_value=info):
            with patch("plan_notify._send_email") as mock_send:
                from plan_notify import _notify_worker
                _notify_worker(clinic_id=1)
        mock_send.assert_not_called()

    def test_paid_plan_does_not_send_email(self):
        """유료 플랜 클리닉은 알림 대상이 아니어야 한다."""
        info = self._make_usage_info(
            plan_id="standard",
            plan_expires_at=_future(30),
            limit=None, used=100,
        )
        with patch("plan_notify._get_usage_info", return_value=info):
            with patch("plan_notify._send_email") as mock_send:
                from plan_notify import _notify_worker
                _notify_worker(clinic_id=2)
        mock_send.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# plan_notify: 이메일 형식 검증
# ═══════════════════════════════════════════════════════════════════

class TestEmailValidation:
    """_send_email(): 유효하지 않은 이메일 주소 차단"""

    def test_invalid_email_blocked(self):
        """개행·공백 포함 이메일은 발송되지 않아야 한다."""
        from plan_notify import _send_email
        with patch("plan_notify.smtplib.SMTP") as mock_smtp:
            _send_email("bad\nemail@x.com", clinic_id=1, used=3, limit=3)
        mock_smtp.assert_not_called()

    def test_valid_email_proceeds(self):
        """유효한 이메일은 SMTP 미설정 시 로그만 남겨야 한다 (smtplib 호출 없음)."""
        import os
        from plan_notify import _send_email
        with patch.dict(os.environ, {"SMTP_HOST": ""}, clear=False):
            with patch("plan_notify.smtplib.SMTP") as mock_smtp:
                _send_email("valid@example.com", clinic_id=1, used=3, limit=3)
        mock_smtp.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# /api/admin/clinic 엔드포인트
# ═══════════════════════════════════════════════════════════════════

class TestAdminCreateClinic:
    """/api/admin/clinic — ADMIN_SECRET 게이트 검증"""

    def _get_client(self):
        from fastapi.testclient import TestClient
        import os
        os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-minimum!")
        from main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_no_admin_secret_returns_403(self):
        """ADMIN_SECRET 미설정 시 403 반환."""
        import os
        with patch.dict(os.environ, {"ADMIN_SECRET": ""}, clear=False):
            client = self._get_client()
            resp = client.post(
                "/api/admin/clinic",
                json={"name": "테스트 한의원"},
                headers={"Authorization": "Bearer anything"},
            )
        assert resp.status_code == 403

    def test_wrong_secret_returns_401(self):
        """잘못된 시크릿 → 401."""
        import os
        with patch.dict(os.environ, {"ADMIN_SECRET": "correct-secret"}, clear=False):
            client = self._get_client()
            resp = client.post(
                "/api/admin/clinic",
                json={"name": "테스트 한의원"},
                headers={"Authorization": "Bearer wrong-secret"},
            )
        assert resp.status_code == 401

    def test_correct_secret_creates_clinic(self):
        """올바른 시크릿 + 유효한 name → 201/200, clinic_id 반환."""
        import os
        with patch.dict(os.environ, {"ADMIN_SECRET": "my-secret"}, clear=False):
            with patch("main.create_clinic", return_value=7) as mock_cc:
                client = self._get_client()
                resp = client.post(
                    "/api/admin/clinic",
                    json={"name": "강남 한의원", "max_slots": 5},
                    headers={"Authorization": "Bearer my-secret"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data["clinic_id"] == 7
        assert "trial_expires_at" in data
        mock_cc.assert_called_once_with("강남 한의원", 5)

    def test_missing_name_returns_400(self):
        """name 누락 → 400."""
        import os
        with patch.dict(os.environ, {"ADMIN_SECRET": "my-secret"}, clear=False):
            client = self._get_client()
            resp = client.post(
                "/api/admin/clinic",
                json={},
                headers={"Authorization": "Bearer my-secret"},
            )
        assert resp.status_code == 400
