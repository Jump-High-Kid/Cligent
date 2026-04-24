"""
youtube_generator.py — YouTube 파이프라인 오케스트레이터

FastAPI StreamingResponse와 연결하여 pipeline_runner의 SSE 출력을 전달합니다.
blog_generator.py와 동일한 구조:
  - plan_guard.check_blog_limit() 적용 (YouTube 1회 = 블로그 1회 카운트)
  - API 키: DB에서 Fernet 복호화 후 내부 주입 (request body로 받지 않음)
  - usage_tracker 로깅
"""

import json
import logging
from typing import AsyncGenerator, Optional

from plan_guard import check_blog_limit
from pipeline_runner import run_youtube_pipeline

logger = logging.getLogger(__name__)


def _get_clinic_api_key(clinic_id: int) -> Optional[str]:
    """
    DB에서 Fernet 복호화된 API 키 반환.
    main.py의 /api/settings/clinic/ai GET 엔드포인트와 동일한 복호화 로직.
    """
    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key_enc FROM clinics WHERE id = ?", (clinic_id,)
            ).fetchone()
        if not row or not row["api_key_enc"]:
            import os
            return os.environ.get("ANTHROPIC_API_KEY") or None

        import os
        from cryptography.fernet import Fernet
        fernet_key = os.environ.get("SECRET_KEY", "").encode()
        if not fernet_key:
            return None

        # SECRET_KEY가 Fernet 키 형식이 아닐 경우 base64 변환
        import base64
        try:
            f = Fernet(fernet_key)
        except Exception:
            f = Fernet(base64.urlsafe_b64encode(fernet_key[:32].ljust(32, b"\x00")))

        return f.decrypt(row["api_key_enc"].encode()).decode()
    except Exception as exc:
        logger.error("youtube_generator: API 키 복호화 실패 (clinic_id=%s): %s", clinic_id, exc)

    # DB 키 없으면 .env 폴백
    import os
    return os.environ.get("ANTHROPIC_API_KEY") or None


def _log_usage(clinic_id: int, topic: str) -> None:
    """usage_logs에 youtube_generation 기록. 실패 시 서비스 영향 없음."""
    try:
        from db_manager import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO usage_logs (clinic_id, feature, metadata) VALUES (?, ?, ?)",
                (
                    clinic_id,
                    "youtube_generation",
                    json.dumps({"topic": topic[:100]}, ensure_ascii=False),
                ),
            )
    except Exception as exc:
        logger.error("youtube_generator: usage_logs 기록 실패: %s", exc)


async def generate_youtube_stream(
    topic: str,
    clinic_id: int,
    options: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """
    YouTube 파이프라인 SSE 스트림.

    1. plan_guard 한도 체크 (429 시 HTTPException)
    2. DB에서 API 키 조회
    3. pipeline_runner.run_youtube_pipeline() 실행
    4. usage_logs 기록

    호출 예:
        return StreamingResponse(
            generate_youtube_stream(topic, clinic_id, options),
            media_type="text/event-stream",
        )
    """
    # 1. 플랜 한도 체크 (YouTube는 블로그와 동일 카운터 사용)
    check_blog_limit(clinic_id)

    # 2. API 키 조회
    api_key = _get_clinic_api_key(clinic_id)
    if not api_key:
        error_payload = json.dumps(
            {"type": "error", "step": "init", "msg": "API 키가 설정되지 않았습니다. 설정 > AI 설정에서 Anthropic API 키를 등록해주세요."},
            ensure_ascii=False,
        )
        yield f"data: {error_payload}\n\n"
        return

    # 3. usage 로깅 (파이프라인 시작 시점에 기록 — fail-open)
    _log_usage(clinic_id, topic)

    # 4. 파이프라인 실행 + SSE 스트리밍
    async for chunk in run_youtube_pipeline(
        topic=topic,
        api_key=api_key,
        clinic_id=clinic_id,
        options=options,
    ):
        yield chunk
