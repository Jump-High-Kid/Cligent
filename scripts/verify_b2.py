#!/usr/bin/env python3
"""
verify_b2.py — B2(모니터링) 작동 검증 스크립트

확인 항목:
  1. structlog가 data/cligent.log에 json line으로 기록하는가
  2. Sentry SDK 초기화 + 의도적 에러가 대시보드에 도착하는가
  3. PII 마스킹이 작동하는가 (SENTRY_DSN, ANTHROPIC_API_KEY 등 [REDACTED])

사용:
  python3 scripts/verify_b2.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from observability import init_observability  # noqa: E402

import structlog  # noqa: E402

init_observability()
log = structlog.get_logger("verify_b2")

# 1. structlog 검증
log.info("verify_b2_started", marker="structlog_test_kxYZ81")
log.warning("verify_b2_warning_sample", sample_field="check this in cligent.log")

# 2. Sentry 의도적 에러 (PII 마스킹도 동시 검증 — extra에 민감 키워드 포함)
try:
    import sentry_sdk

    # 마스킹 검증을 위해 의도적으로 민감 키워드 extra 포함
    sentry_sdk.set_context("verify_pii_check", {
        "api_key": "sk-ant-fake-this-must-be-redacted",
        "password": "must_be_redacted",
        "user_id": "12345",  # 해시되어야 함
        "harmless_field": "this should remain visible",
    })
    raise RuntimeError("B2 검증용 의도적 에러 — Sentry 대시보드에서 확인 후 무시")
except RuntimeError as e:
    sentry_sdk.capture_exception(e)
    print("[verify_b2] Sentry 에러 발송 완료")

# 비동기 전송 flush 대기 (최대 5초)
sentry_sdk.flush(timeout=5)

print()
print("=" * 60)
print("B2 검증 절차")
print("=" * 60)
print()
print("[1] structlog 작동 확인")
print(f"    cat data/cligent.log | tail -3")
print(f"    → 'verify_b2_started' / 'verify_b2_warning_sample' 라인이")
print(f"      json 형태로 보여야 함")
print()
print("[2] Sentry 도착 확인")
print(f"    sentry.io → 프로젝트 → Issues")
print(f"    → 'B2 검증용 의도적 에러' 1건 도착했는지")
print()
print("[3] PII 마스킹 검증 (가장 중요)")
print(f"    위 Sentry 이슈 클릭 → Additional Data 또는 Contexts 섹션")
print(f"    → 'verify_pii_check' 그룹에서:")
print(f"      ✅ api_key  → '[REDACTED]'")
print(f"      ✅ password → '[REDACTED]'")
print(f"      ✅ user_id  → 16자 해시 (raw '12345' 아니어야 함)")
print(f"      ✅ harmless_field → 그대로 보여야 함")
print()
print("[4] 그 외 stack trace에서:")
print(f"    ✅ ANTHROPIC_API_KEY, SECRET_KEY, FERNET_KEY 등 환경변수 값이")
print(f"      절대로 raw로 노출되면 안 됨")
print()
print("=" * 60)
print("모두 OK이면 B2 검증 완료. 이 스크립트는 다시 실행 안 해도 됨.")
print("=" * 60)
