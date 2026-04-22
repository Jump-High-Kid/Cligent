"""
plan_notify.py — 한도 80% 도달 시 이메일 알림 발송

동작 원칙:
  - 블로그 생성 후 check_and_notify(clinic_id) 를 비동기 스레드로 호출
  - 이번 달 사용량이 플랜 한도의 80% 이상이면 이메일 발송
  - 같은 클리닉, 같은 달에 한 번만 발송 (중복 방지)
  - SMTP 설정 없으면 로그만 남기고 조용히 실패
  - 응답 경로 바깥에서 실행 — 서비스 지연 없음

환경 변수:
  SMTP_HOST     — SMTP 서버 (없으면 이메일 발송 비활성화)
  SMTP_PORT     — 기본값 587
  SMTP_USER     — SMTP 계정
  SMTP_PASSWORD — SMTP 비밀번호
  NOTIFY_FROM   — 발신자 주소 (기본값: noreply@cligent.app)
"""

import logging
import os
import re
import smtplib
import threading
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Set, Tuple

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

logger = logging.getLogger(__name__)

# 중복 발송 방지: {(clinic_id, "YYYY-MM")} — 프로세스 재시작 시 초기화됨
# 영속성이 필요하면 DB 또는 Redis로 교체
_notified: Set[Tuple[int, str]] = set()
_notified_lock = threading.Lock()

# 한도 대비 알림 임계값 (80%)
_NOTIFY_THRESHOLD = 0.80


def _current_month_key() -> str:
    """현재 UTC 연월을 "YYYY-MM" 문자열로 반환."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _already_notified(clinic_id: int) -> bool:
    """이번 달에 이미 알림을 보낸 클리닉인지 확인."""
    key = (clinic_id, _current_month_key())
    with _notified_lock:
        return key in _notified


def _mark_notified(clinic_id: int) -> None:
    """이번 달 알림 발송 완료로 기록."""
    key = (clinic_id, _current_month_key())
    with _notified_lock:
        _notified.add(key)


def _get_clinic_email(clinic_id: int) -> Optional[str]:
    """
    클리닉 대표원장(chief_director) 이메일 조회.
    DB 오류 시 None 반환.
    """
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT u.email FROM users u
                WHERE u.clinic_id = ?
                  AND u.role = 'chief_director'
                  AND u.is_active = 1
                LIMIT 1
                """,
                (clinic_id,),
            ).fetchone()
        return row["email"] if row else None
    except Exception as exc:
        logger.warning("plan_notify: 이메일 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return None


def _get_usage_info(clinic_id: int) -> Optional[dict]:
    """
    클리닉의 현재 플랜 한도 및 이번 달 사용량 반환.
    반환값: {"plan_id", "plan_expires_at", "trial_expires_at", "limit", "used"}
    None 반환 시 알림 취소.
    """
    try:
        from db_manager import get_db
        month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")
        with get_db() as conn:
            clinic_row = conn.execute(
                """
                SELECT c.plan_id, c.plan_expires_at, c.trial_expires_at,
                       p.monthly_blog_limit
                FROM clinics c
                LEFT JOIN plans p ON c.plan_id = p.id
                WHERE c.id = ?
                """,
                (clinic_id,),
            ).fetchone()
            if clinic_row is None:
                return None

            usage_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM usage_logs
                WHERE clinic_id = ?
                  AND feature = 'blog_generation'
                  AND used_at >= ?
                """,
                (clinic_id, month_start),
            ).fetchone()

        return {
            "plan_id": clinic_row["plan_id"] or "free",
            "plan_expires_at": clinic_row["plan_expires_at"],
            "trial_expires_at": clinic_row["trial_expires_at"],
            "limit": clinic_row["monthly_blog_limit"],
            "used": usage_row["cnt"] if usage_row else 0,
        }
    except Exception as exc:
        logger.warning("plan_notify: 사용량 조회 실패 (clinic_id=%s): %s", clinic_id, exc)
        return None


def _send_email(to_email: str, clinic_id: int, used: int, limit: int) -> None:
    """
    SMTP로 이메일 발송.
    SMTP 설정 없거나 발송 실패 시 로그만 남기고 조용히 종료.
    """
    if not _EMAIL_RE.match(to_email):
        logger.warning("plan_notify: 유효하지 않은 수신자 이메일 (clinic_id=%s)", clinic_id)
        return

    smtp_host = os.getenv("SMTP_HOST", "")
    if not smtp_host:
        # SMTP 미설정 → 로그만 남김
        logger.info(
            "plan_notify: SMTP 미설정, 이메일 미발송 (clinic_id=%s, to=%s, used=%s/%s)",
            clinic_id, to_email, used, limit,
        )
        return

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("NOTIFY_FROM", "noreply@cligent.app")

    # 이메일 본문 구성
    subject = f"[Cligent] 블로그 생성 한도 {int(_NOTIFY_THRESHOLD * 100)}% 도달 안내"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">블로그 생성 한도 알림</h2>
    <p>이번 달 블로그 생성 횟수가 <strong>{int(_NOTIFY_THRESHOLD * 100)}%</strong>에 도달했습니다.</p>
    <div style="background:#ecfdf5; border:1px solid #6ee7b7; border-radius:12px; padding:16px 24px; margin:16px 0;">
      <p style="margin:0; font-size:16px;">
        현재 사용량: <strong style="color:#064e3b;">{used} / {limit}편</strong>
      </p>
    </div>
    <p>무제한 생성을 원하시면 스탠다드 플랜으로 업그레이드해 보세요.</p>
    <p style="margin-top:32px; color:#78716c; font-size:12px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_email
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, [to_email], msg.as_string())

        logger.info(
            "plan_notify: 이메일 발송 완료 (clinic_id=%s, to=%s, used=%s/%s)",
            clinic_id, to_email, used, limit,
        )
    except Exception as exc:
        # 발송 실패는 서비스에 영향 없음
        logger.warning(
            "plan_notify: 이메일 발송 실패 (clinic_id=%s): %s", clinic_id, exc,
        )


def _notify_worker(clinic_id: int) -> None:
    """
    스레드에서 실행되는 알림 로직.
    모든 예외를 내부에서 처리하여 스레드 크래시 방지.
    """
    try:
        # 중복 발송 방지: 이번 달에 이미 보냈으면 종료
        if _already_notified(clinic_id):
            return

        info = _get_usage_info(clinic_id)
        if info is None:
            return

        # 실효 플랜 확인 — 유료/체험 무제한 플랜은 알림 불필요
        from plan_guard import resolve_effective_plan
        effective = resolve_effective_plan(
            info.get("plan_id"),
            info.get("plan_expires_at"),
            info.get("trial_expires_at"),
        )
        if effective["has_unlimited"]:
            return

        limit = info["limit"]
        used = info["used"]

        # 무제한 플랜(limit=None) 또는 한도 미달이면 알림 불필요
        if limit is None or limit == 0:
            return
        if used < limit * _NOTIFY_THRESHOLD:
            return

        # 80% 이상 도달 — 이메일 발송
        email = _get_clinic_email(clinic_id)
        if not email:
            logger.warning(
                "plan_notify: 수신자 이메일 없음, 알림 취소 (clinic_id=%s)", clinic_id
            )
            return

        _send_email(email, clinic_id, used, limit)
        _mark_notified(clinic_id)

    except Exception as exc:
        # 예외가 스레드 밖으로 나가지 않도록 캐치
        logger.error(
            "plan_notify: 예상치 못한 오류 (clinic_id=%s): %s", clinic_id, exc,
        )


def check_and_notify(clinic_id: int) -> None:
    """
    블로그 생성 직후 호출 — 비동기 스레드로 알림 처리.

    사용 예 (main.py /generate 엔드포인트):
        log_usage(user["clinic_id"], "blog_generation", ...)
        check_and_notify(user["clinic_id"])   # 응답 지연 없음
    """
    t = threading.Thread(
        target=_notify_worker,
        args=(clinic_id,),
        daemon=True,   # 메인 프로세스 종료 시 함께 종료
        name=f"plan-notify-{clinic_id}",
    )
    t.start()
