"""
observability.py — 모니터링/로깅 통합 (B2, 2026-04-27)

3가지 기능:
  1. Sentry SDK 초기화 (FastAPI integration) — unhandled exception 자동 캐치
  2. structlog 설정 — JSON line 로그를 stdout + data/cligent.log에 기록
  3. RequestLoggingMiddleware — 모든 HTTP request에 request_id 부여 + 메트릭 기록

PII 마스킹 (PIPA 22조·26조 준수):
  - api_key / password / token / authorization 키워드 자동 [REDACTED]
  - 환경변수 (ANTHROPIC/OPENAI/GEMINI/SECRET_KEY/FERNET) 자동 [REDACTED]
  - user_id는 SHA-256(user_id + USER_ID_SALT)로 익명화 후에만 전송

사용:
  from observability import init_observability, RequestLoggingMiddleware
  init_observability()   # 앱 시작 시 1회
  app.add_middleware(RequestLoggingMiddleware)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / "data"
LOG_FILE = LOG_DIR / "cligent.log"
ERROR_LOG_DIR = LOG_DIR / "error_logs"

# PII 마스킹 대상 키워드 (대소문자 무관)
_SENSITIVE_KEYS = {
    "api_key", "apikey", "x-api-key",
    "password", "passwd",
    "token", "access_token", "refresh_token",
    "authorization", "cookie", "set-cookie",
    "secret", "secret_key",
    "fernet_key", "fernet",
    "anthropic_api_key", "openai_api_key", "gemini_api_key", "google_api_key",
    "sentry_dsn",
}

# 환경변수 키워드 (stack trace에서 노출되면 안 되는 것)
_SENSITIVE_ENV_PATTERNS = [
    re.compile(r"(ANTHROPIC|OPENAI|GEMINI|GOOGLE)_API_KEY", re.I),
    re.compile(r"SECRET_KEY", re.I),
    re.compile(r"FERNET", re.I),
    re.compile(r"SENTRY_DSN", re.I),
]


def hash_user_id(user_id: str | int | None) -> str:
    """user_id를 SHA-256(user_id + salt)로 익명화. None이면 'anon'."""
    if user_id is None:
        return "anon"
    salt = os.getenv("USER_ID_SALT", "")
    if not salt:
        # salt 미설정 시 안전장치: 그래도 raw user_id는 노출 금지
        return "no_salt"
    h = hashlib.sha256(f"{user_id}{salt}".encode("utf-8")).hexdigest()
    return h[:16]


_USER_ID_KEYS = {"user_id", "userid", "uid", "user"}
_EMAIL_KEYS = {"email", "user_email", "username"}


def _mask_value(key: str, value: Any) -> Any:
    """key 이름이 민감 키워드와 일치하면 [REDACTED] 또는 해시로 마스킹."""
    if not isinstance(key, str):
        return value
    key_lower = key.lower()
    if key_lower in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if key_lower in _USER_ID_KEYS and value is not None and not isinstance(value, dict):
        return hash_user_id(value)
    if key_lower in _EMAIL_KEYS:
        return "[REDACTED]"
    return value


def _scrub(obj: Any, depth: int = 0) -> Any:
    """재귀적으로 dict/list 안의 민감 데이터 마스킹. 깊이 제한 6."""
    if depth > 6:
        return obj
    if isinstance(obj, dict):
        return {k: (_mask_value(k, _scrub(v, depth + 1))) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(item, depth + 1) for item in obj]
    if isinstance(obj, str):
        # 환경변수 값으로 보이는 패턴 마스킹
        for pat in _SENSITIVE_ENV_PATTERNS:
            if pat.search(obj):
                return "[REDACTED]"
        return obj
    return obj


def _sentry_before_send(event: dict, hint: dict) -> dict | None:
    """Sentry 전송 전 PII 마스킹. None 반환 시 이벤트 폐기."""
    try:
        # request body / headers / cookies 마스킹
        if "request" in event:
            event["request"] = _scrub(event["request"])
        # extra / contexts 마스킹
        if "extra" in event:
            event["extra"] = _scrub(event["extra"])
        if "contexts" in event:
            event["contexts"] = _scrub(event["contexts"])
        # user 객체에서 raw user_id 제거
        if "user" in event and isinstance(event["user"], dict):
            user = event["user"]
            if "id" in user and not user.get("id_hashed"):
                user["id"] = hash_user_id(user["id"])
                user["id_hashed"] = True
            # 이메일/한의원명은 통째로 제거
            user.pop("email", None)
            user.pop("username", None)
        # breadcrumbs 마스킹
        for crumb in event.get("breadcrumbs", {}).get("values", []):
            if isinstance(crumb, dict) and "data" in crumb:
                crumb["data"] = _scrub(crumb["data"])
        return event
    except Exception:
        # 마스킹 자체가 실패하면 안전하게 이벤트 폐기
        return None


def init_observability() -> None:
    """앱 시작 시 1회 호출. Sentry + structlog 동시 초기화."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── structlog ────────────────────────────────────────────────
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)

    # 표준 logging이 파일 + stdout으로 흐르게
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # 중복 핸들러 제거
    root_logger.handlers.clear()

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(stream_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── Sentry ───────────────────────────────────────────────────
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        structlog.get_logger().info("sentry_disabled", reason="SENTRY_DSN not set")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                StarletteIntegration(transaction_style="endpoint"),
                FastApiIntegration(transaction_style="endpoint"),
            ],
            traces_sample_rate=0.1,  # 10% 샘플링
            send_default_pii=False,  # 절대 PII 자동 전송 X
            before_send=_sentry_before_send,
            environment=os.getenv("ENV", "dev"),
            release=os.getenv("CLIGENT_VERSION", "unknown"),
        )
        structlog.get_logger().info("sentry_enabled", dsn_host=dsn.split("@")[-1].split("/")[0] if "@" in dsn else "?")
    except ImportError:
        structlog.get_logger().warning("sentry_skipped", reason="sentry_sdk not installed")


def _extract_user_context(request: Request) -> dict[str, str]:
    """JWT 쿠키에서 user_id, clinic_id 추출 (PII는 해시로만)."""
    try:
        from auth_manager import COOKIE_NAME, decode_token
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return {"user_hash": "anon", "clinic_id": "none"}
        payload = decode_token(token)
        return {
            "user_hash": hash_user_id(payload.get("sub")),
            "clinic_id": str(payload.get("clinic_id", "none")),
            "role": str(payload.get("role", "unknown")),
        }
    except Exception:
        return {"user_hash": "anon", "clinic_id": "none"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    모든 HTTP request에 대해:
      - request_id 부여 (uuid)
      - structlog 컨텍스트 변수 설정
      - method/path/status/duration_ms 기록
      - 5xx 발생 시 error_logs/{date}.jsonl 에 추가 기록
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        user_ctx = _extract_user_context(request)

        # structlog 컨텍스트 변수 (해당 request 처리 동안 모든 로그에 자동 포함)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            **user_ctx,
        )

        log = structlog.get_logger("http")
        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            response.headers["X-Request-ID"] = request_id

            log.info(
                "http_request",
                status=response.status_code,
                duration_ms=duration_ms,
            )

            # 5xx → error_logs 추가 기록 (daily_report.py가 자동으로 읽음)
            if response.status_code >= 500:
                _write_error_log(
                    request=request,
                    status=response.status_code,
                    user_hash=user_ctx["user_hash"],
                    clinic_id=user_ctx["clinic_id"],
                    error_type="HTTP_5XX",
                    error_msg=f"Status {response.status_code}",
                    request_id=request_id,
                )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.error(
                "http_exception",
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_msg=str(exc)[:300],
            )
            _write_error_log(
                request=request,
                status=500,
                user_hash=user_ctx["user_hash"],
                clinic_id=user_ctx["clinic_id"],
                error_type=type(exc).__name__,
                error_msg=str(exc)[:500],
                request_id=request_id,
            )
            raise  # 기존 _global_exc_handler가 처리


def _write_error_log(
    *,
    request: Request,
    status: int,
    user_hash: str,
    clinic_id: str,
    error_type: str,
    error_msg: str,
    request_id: str,
) -> None:
    """daily_report.py가 읽는 파일 형식 그대로 추가 기록."""
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = ERROR_LOG_DIR / f"{date_str}.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "user_hash": user_hash,
            "clinic_id": clinic_id,
            "error_type": error_type,
            "error_msg": error_msg,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 로깅 실패가 본 요청을 깨면 안 됨
        pass
