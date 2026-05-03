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


def _record_applicant_email(
    applicant_id: Optional[int],
    email_type: Optional[str],
    success: bool,
    error_msg: Optional[str],
) -> None:
    """applicant_emails 테이블에 발송 이력 1건 기록. 실패해도 본 흐름에 영향 없음."""
    if applicant_id is None or not email_type:
        return
    try:
        from db_manager import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO applicant_emails (applicant_id, email_type, success, error_msg) "
                "VALUES (?, ?, ?, ?)",
                (applicant_id, email_type, 1 if success else 0, error_msg),
            )
    except Exception as exc:
        logger.warning("plan_notify: applicant_emails 기록 실패: %s", exc)


def _send_smtp(
    to: str,
    subject: str,
    html_body: str,
    applicant_id: Optional[int] = None,
    email_type: Optional[str] = None,
) -> bool:
    """
    공통 SMTP 발송 헬퍼. 성공 True, 실패/미설정 False.
    SMTP 미설정 시 로그만 남기고 False 반환 (fail-soft).

    applicant_id + email_type가 주어지면 applicant_emails에 결과 자동 기록.
    """
    success = False
    error_msg: Optional[str] = None

    if not _EMAIL_RE.match(to):
        logger.warning("plan_notify: 유효하지 않은 수신자 이메일: %s", to)
        error_msg = "invalid_email"
        _record_applicant_email(applicant_id, email_type, success, error_msg)
        return False

    smtp_host = os.getenv("SMTP_HOST", "")
    if not smtp_host:
        logger.info("plan_notify: SMTP 미설정, 이메일 미발송 (to=%s, subject=%s)", to, subject)
        error_msg = "smtp_not_configured"
        _record_applicant_email(applicant_id, email_type, success, error_msg)
        return False

    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    from_addr = os.getenv("NOTIFY_FROM", "noreply@cligent.app")

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(from_addr, [to], msg.as_string())

        logger.info("plan_notify: 이메일 발송 완료 (to=%s)", to)
        success = True
    except Exception as exc:
        logger.warning("plan_notify: 이메일 발송 실패 (to=%s): %s", to, exc)
        error_msg = f"{type(exc).__name__}: {str(exc)[:200]}"

    _record_applicant_email(applicant_id, email_type, success, error_msg)
    return success


def _send_email(to_email: str, clinic_id: int, used: int, limit: int) -> None:
    """한도 80% 알림 이메일 발송. SMTP 실패 시 서비스에 영향 없음."""
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
    _send_smtp(to_email, subject, body_html)


# ── 베타 모집 이메일 (E1 / E2 / E4) ──────────────────────────────────

def send_beta_apply_confirm(to_email: str, name: str, applicant_id: Optional[int] = None) -> bool:
    """E1: 신청자에게 접수 확인 이메일 발송."""
    subject = "[Cligent] 베타 신청이 접수되었습니다"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">베타 신청 접수 완료</h2>
    <p>안녕하세요, <strong>{name}</strong> 선생님.</p>
    <p>Cligent 베타 신청이 정상적으로 접수되었습니다.<br>
       검토 후 초대 링크를 별도 이메일로 발송해 드리겠습니다.</p>
    <p>감사합니다.</p>
    <p style="margin-top:32px; color:#78716c; font-size:12px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""
    return _send_smtp(to_email, subject, body_html, applicant_id, "apply_confirm")


def send_beta_admin_notify(
    name: str, clinic_name: str, email: str, note: str,
    applicant_id: Optional[int] = None,
) -> bool:
    """E2: 관리자(ADMIN_NOTIFY_EMAIL)에게 신규 베타 신청 알림 발송."""
    admin_email = os.getenv("ADMIN_NOTIFY_EMAIL", "")
    if not admin_email:
        logger.info("plan_notify: ADMIN_NOTIFY_EMAIL 미설정, 관리자 알림 생략")
        return False

    subject = f"[Cligent] 베타 신청 접수 — {name} ({clinic_name})"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent 어드민</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">신규 베타 신청 접수</h2>
    <table style="border-collapse:collapse; width:100%;">
      <tr><td style="padding:8px; color:#78716c; width:100px;">이름</td><td style="padding:8px; font-weight:600;">{name}</td></tr>
      <tr><td style="padding:8px; color:#78716c;">한의원</td><td style="padding:8px; font-weight:600;">{clinic_name}</td></tr>
      <tr><td style="padding:8px; color:#78716c;">이메일</td><td style="padding:8px;">{email}</td></tr>
      <tr><td style="padding:8px; color:#78716c;">메모</td><td style="padding:8px;">{note or "(없음)"}</td></tr>
    </table>
    <p style="margin-top:24px;">
      <a href="https://cligent.kr/admin/applicants" style="background:#064e3b; color:#fff; padding:10px 20px; border-radius:8px; text-decoration:none;">어드민 패널에서 확인</a>
    </p>
  </div>
</body>
</html>
"""
    return _send_smtp(admin_email, subject, body_html, applicant_id, "admin_notify")


def send_beta_invite_email(
    to_email: str, name: str, invite_url: str,
    applicant_id: Optional[int] = None,
) -> bool:
    """E3: 신청자에게 초대 링크 발송 (invite-batch 시 호출)."""
    subject = "[Cligent] 1차 베타에 초대드립니다"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">1차 베타 초대장</h2>
    <p>안녕하세요, 원장님.</p>
    <p>원장님의 시간을 아껴드릴 medical AI agent,<br>
       <strong>Cligent</strong>입니다.</p>
    <p>1차 베타에 초대드립니다. 5인 한정으로 모집했고,<br>
       <strong>{name}</strong> 원장님께서 그 중 한 분이세요.</p>
    <div style="background:#ecfdf5; border:1px solid #6ee7b7; border-radius:12px; padding:16px 20px; margin:20px 0;">
      <p style="margin:0 0 8px 0; font-size:13px; color:#065f46; font-weight:600;">베타 조건</p>
      <ul style="margin:0; padding-left:18px; font-size:14px; line-height:1.8;">
        <li>기간: <strong>15일</strong></li>
        <li>블로그 생성 한도: <strong>25편</strong></li>
        <li>비용: 제한적 무료</li>
      </ul>
    </div>
    <p style="margin:24px 0;">
      <a href="{invite_url}" style="background:#064e3b; color:#fff; padding:14px 28px; border-radius:10px; text-decoration:none; font-size:16px;">초대 수락하기</a>
    </p>
    <p style="color:#78716c; font-size:13px;">이 링크는 72시간 후 만료됩니다.</p>
    <p style="font-size:14px; line-height:1.7; margin-top:24px;">
      사용 중 불편한 점이나 개선 제안은 언제든
      앱 안 도움말 우측 패널 또는
      <a href="mailto:cligent.ai@gmail.com" style="color:#064e3b;">cligent.ai@gmail.com</a>
      으로 알려주세요.
    </p>
    <p style="margin-top:24px; color:#1c1917; font-size:14px;">
      - Cligent 운영팀 -
    </p>
    <p style="margin-top:24px; color:#a8a29e; font-size:11px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""
    return _send_smtp(to_email, subject, body_html, applicant_id, "invite")


def send_beta_reminder(
    to_email: str, name: str, invite_url: str,
    applicant_id: Optional[int] = None,
) -> bool:
    """E4: 72h 미클릭 리마인더 이메일."""
    subject = "[Cligent] 초대 링크가 곧 만료됩니다"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">초대 링크 만료 임박</h2>
    <p>안녕하세요, <strong>{name}</strong> 선생님.</p>
    <p>발송된 초대 링크를 아직 사용하지 않으셨습니다.<br>
       링크는 <strong>24시간 후 만료</strong>됩니다.</p>
    <p style="margin:24px 0;">
      <a href="{invite_url}" style="background:#064e3b; color:#fff; padding:14px 28px; border-radius:10px; text-decoration:none; font-size:16px;">지금 수락하기</a>
    </p>
    <p style="color:#78716c; font-size:13px;">링크가 만료된 경우 관리자에게 재발송을 요청하세요.</p>
    <p style="margin-top:32px; color:#78716c; font-size:12px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""
    return _send_smtp(to_email, subject, body_html, applicant_id, "reminder")


def send_naver_found_email(to_email: str, title: str, url: str) -> None:
    """N1: 네이버 블로그 색인 확인 완료 알림."""
    subject = "[Cligent] 블로그 포스트가 네이버 검색에 등록되었습니다"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#064e3b; margin-top:0;">네이버 검색 등록 완료</h2>
    <p>작성하신 블로그 포스트가 네이버 검색에 등록된 것을 확인했습니다.</p>
    <div style="background:#ecfdf5; border:1px solid #6ee7b7; border-radius:12px; padding:16px 24px; margin:16px 0;">
      <p style="margin:0 0 8px 0; font-size:14px; color:#065f46; font-weight:600;">등록된 포스트</p>
      <p style="margin:0; font-size:15px;">{title}</p>
    </div>
    <p style="margin:24px 0;">
      <a href="{url}" style="background:#064e3b; color:#fff; padding:14px 28px; border-radius:10px; text-decoration:none; font-size:16px;">포스트 확인하기 ↗</a>
    </p>
    <p style="color:#78716c; font-size:13px;">
      이제 다음 블로그 작성 시 이 포스트가 관련 글로 자동 연결됩니다.
    </p>
    <p style="margin-top:32px; color:#78716c; font-size:12px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""
    _send_smtp(to_email, subject, body_html)


def send_naver_expired_email(to_email: str, title: str) -> None:
    """N2: 7일 내 색인 미확인 — 블로그 검색 노출 점검 권고."""
    subject = "[Cligent] 블로그 포스트 검색 노출을 확인해주세요"
    body_html = f"""
<html>
<body style="font-family: 'Pretendard', sans-serif; color: #1c1917; max-width: 600px;">
  <div style="background:#064e3b; padding:24px 32px; border-radius:16px 16px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">Cligent</h1>
  </div>
  <div style="padding:32px; background:#fafaf9; border:1px solid #e7e5e4; border-top:none; border-radius:0 0 16px 16px;">
    <h2 style="font-size:18px; color:#b45309; margin-top:0;">검색 등록 미확인 안내</h2>
    <p>아래 포스트가 7일이 지났음에도 네이버 검색에서 확인되지 않았습니다.</p>
    <div style="background:#fffbeb; border:1px solid #fcd34d; border-radius:12px; padding:16px 24px; margin:16px 0;">
      <p style="margin:0; font-size:15px;">{title}</p>
    </div>
    <p>검색 노출이 안 되는 경우 아래를 점검해보세요:</p>
    <ul style="color:#44403c; font-size:14px; line-height:1.8;">
      <li>블로그가 네이버 검색 제외 설정으로 되어 있지 않은지 확인</li>
      <li>네이버 서치어드바이저(searchadvisor.naver.com)에서 블로그 등록 여부 확인</li>
      <li>포스트 발행이 정상적으로 완료되었는지 확인</li>
    </ul>
    <p style="color:#78716c; font-size:13px;">
      7일 이상 검색에 노출되지 않는 블로그는 네이버 검색 알고리즘에 의해 비노출 처리되었을 가능성이 있습니다.
    </p>
    <p style="margin-top:32px; color:#78716c; font-size:12px;">
      이 메일은 Cligent 서비스에서 자동 발송되었습니다.
    </p>
  </div>
</body>
</html>
"""
    _send_smtp(to_email, subject, body_html)


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
