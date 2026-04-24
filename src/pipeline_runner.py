"""
pipeline_runner.py — YouTube 콘텐츠 생성 6단계 파이프라인 엔진

각 단계를 순서대로 실행하며 SSE 이벤트로 진행 상황을 전달합니다.
blog_generator.py의 SSE 패턴을 멀티 스텝 파이프라인으로 확장합니다.

에러 핸들링 정책:
  - 비필수 단계: 1회 재시도 → 실패 시 스킵 (step_skipped 이벤트)
  - 필수 단계(script-writer): 실패 시 파이프라인 중단 (error 이벤트)

컨텍스트 전달:
  - 각 단계는 topic + 직전 단계 summary(최대 500자)만 받음
  - 누적 전체 출력 전달 금지 (토큰 예산 관리)

JSON 응답 포맷:
  - 각 단계 모델은 {"content": "...", "summary": "..."} JSON으로 응답
  - 파싱 실패 시 원문 텍스트 그대로 fallback
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncGenerator, Optional

import anthropic

logger = logging.getLogger(__name__)

# 프로젝트 루트
ROOT = Path(__file__).parent.parent

# 단계별 설정
_STEP_TIMEOUT = 90.0       # 초: 단계별 API 호출 타임아웃
_SUMMARY_MAX_CHARS = 500   # 컨텍스트 전달 최대 요약 길이

# 파이프라인 단계 정의: (step_name, status_msg, required)
_PIPELINE_STEPS = [
    ("content-manager",   "콘텐츠 전략 수립 중...",         False),
    ("news-researcher",   "참고 자료 리서치 중...",          False),
    ("script-writer",     "대본 작성 중...",                 True),   # 필수
    ("seo-blog-writer",   "SEO 최적화 중...",                False),
    ("video-design",      "영상 디자인 브리프 작성 중...",   False),
    ("marketing-advisor", "마케팅 전략 수립 중...",          False),
]

# JSON 응답 강제 지시문 (각 단계 프롬프트 끝에 추가)
_JSON_INSTRUCTION = (
    "\n\n---\n"
    "반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트를 포함하지 마세요:\n"
    '{"content": "여기에 전체 내용 작성", "summary": "핵심 요약 100단어 이내"}'
)


def _sse(event_type: str, **kwargs) -> str:
    """SSE 이벤트 포맷 생성"""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _load_step_prompt(step_name: str) -> str:
    """prompts/youtube/{step_name}.txt 로드 + JSON 응답 지시 추가"""
    prompt_path = ROOT / "prompts" / "youtube" / f"{step_name}.txt"
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("YouTube 프롬프트 파일 없음: %s", prompt_path)
        text = f"당신은 {step_name} 역할의 전문 에이전트입니다."
    return text + _JSON_INSTRUCTION


async def _call_step_agent(
    step_name: str,
    system_prompt: str,
    user_msg: str,
    api_key: str,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """
    단일 단계 API 호출 (non-streaming).

    Returns:
        {"content": str, "summary": str}

    JSON 파싱 실패 시 원문 텍스트로 fallback.
    30초 타임아웃 — asyncio.wait_for로 wrap.
    """
    async def _invoke() -> str:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text if response.content else ""

    raw = await asyncio.wait_for(_invoke(), timeout=_STEP_TIMEOUT)

    # 마크다운 코드블록 제거 (```json ... ``` 또는 ``` ... ```)
    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        end = next((i for i in range(len(lines) - 1, 0, -1) if lines[i].strip() == "```"), -1)
        stripped = "\n".join(lines[1:end]) if end > 0 else "\n".join(lines[1:])

    # JSON 파싱 + fallback
    try:
        parsed = json.loads(stripped)
        content = str(parsed.get("content", raw))
        summary = str(parsed.get("summary", content[:_SUMMARY_MAX_CHARS]))
        return {"content": content, "summary": summary[:_SUMMARY_MAX_CHARS]}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {"content": stripped, "summary": stripped[:_SUMMARY_MAX_CHARS]}


async def run_youtube_pipeline(
    topic: str,
    api_key: str,
    clinic_id: int,
    options: Optional[dict] = None,
) -> AsyncGenerator[str, None]:
    """
    YouTube 콘텐츠 생성 6단계 파이프라인 (AsyncGenerator).

    options:
        length: "short" (5분) | "long" (10분+)  default="long"
        style:  "educational" | "marketing"      default="educational"

    SSE 이벤트 타입:
        status      — 단계 시작 알림 {"type": "status", "step": ..., "msg": ...}
        step_done   — 단계 완료    {"type": "step_done", "step": ..., "content": ...}
        step_skipped — 단계 스킵  {"type": "step_skipped", "step": ..., "msg": ...}
        error       — 필수 단계 실패 {"type": "error", "step": ..., "msg": ...}
        done        — 파이프라인 완료 {"type": "done", "step_count": ...}
    """
    opts = options or {}
    length = opts.get("length", "long")
    style = opts.get("style", "educational")

    # 옵션을 사용자 메시지에 포함
    option_context = f"영상 길이: {'5분 내외 (숏폼)' if length == 'short' else '10분 이상 (롱폼)'}, 스타일: {'교육 콘텐츠' if style == 'educational' else '마케팅 콘텐츠'}"

    step_outputs: dict = {}
    prev_summary = ""
    completed_count = 0

    for step_name, status_msg, required in _PIPELINE_STEPS:
        yield _sse("status", step=step_name, msg=status_msg)

        user_msg = (
            f"주제: {topic}\n"
            f"{option_context}\n"
            f"\n이전 단계 요약:\n{prev_summary}" if prev_summary
            else f"주제: {topic}\n{option_context}"
        )

        # 1회 재시도 포함
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                result = await _call_step_agent(
                    step_name=step_name,
                    system_prompt=_load_step_prompt(step_name),
                    user_msg=user_msg,
                    api_key=api_key,
                )
                prev_summary = result["summary"]
                step_outputs[step_name] = result["content"]
                completed_count += 1
                yield _sse("step_done", step=step_name, content=result["content"])
                last_error = None
                break
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"{step_name} 30초 타임아웃")
                logger.warning("YouTube pipeline: %s 타임아웃 (attempt %d)", step_name, attempt + 1)
            except Exception as exc:
                last_error = exc
                logger.warning("YouTube pipeline: %s 실패 (attempt %d): %s", step_name, attempt + 1, exc)

        if last_error is not None:
            if required:
                yield _sse("error", step=step_name, msg=str(last_error))
                return
            yield _sse("step_skipped", step=step_name, msg=f"단계 건너뜀: {last_error}")

    # DB 저장 (fail-open: 실패해도 파이프라인 응답 유지)
    try:
        _save_to_db(
            clinic_id=clinic_id,
            topic=topic,
            options=opts,
            step_outputs=step_outputs,
        )
    except Exception as exc:
        logger.error("pipeline: DB 저장 실패 (clinic_id=%s): %s", clinic_id, exc)

    yield _sse("done", step_count=completed_count)
    yield "data: [DONE]\n\n"


def _save_to_db(clinic_id: int, topic: str, options: dict, step_outputs: dict) -> None:
    """youtube_results 테이블에 파이프라인 결과 저장. 실패 시 로그만 남김."""
    try:
        from db_manager import get_db
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO youtube_results
                    (clinic_id, topic, options_json, step_outputs_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    clinic_id,
                    topic,
                    json.dumps(options, ensure_ascii=False),
                    json.dumps(step_outputs, ensure_ascii=False),
                ),
            )
    except Exception as exc:
        logger.error("YouTube pipeline: DB 저장 실패 (clinic_id=%s): %s", clinic_id, exc)
