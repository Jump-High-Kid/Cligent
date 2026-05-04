"""routers/blog.py — 블로그·이미지·에이전트·공용 페이지 라우터 (v0.9.0).

라우트 (24건, SSE 3건은 main.py 잔존 — C2에서 이관):
  HTML 페이지:   /blog, /blog/chat, /app, /youtube, /chat
  /api/blog/*:   track-prompt-copy, stats, history, history/{id}/text,
                 history/{id}/publish-check, notifications, publish-status,
                 notifications/{id}/dismiss
  /api/blog-chat: session/{session_id}
  Legacy:         /conversation-flow, /build-prompt, /generate-youtube
  Agents:         /api/agents/available, /api/agent/chat
  Image:          /api/image/stats, /api/image/generate-initial,
                  /api/image/regenerate, /api/image/edit,
                  /api/image/session/{session_id}

main.py 4,021 → 2,885줄 분할의 4번째 라우터 (v0.9.0 / 2026-05-02).
"""
from __future__ import annotations

import json as _json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import time as _time_now
from typing import Generator, Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    FileResponse, JSONResponse, RedirectResponse, StreamingResponse,
)

from auth_manager import COOKIE_NAME, get_current_user
from dependencies import (
    NO_CACHE_HEADERS as _NO_CACHE,
    is_admin_clinic as _is_admin_clinic,
)
from blog_generator import build_prompt_text, generate_blog_stream
from input_limits import (
    validate_int as _vi,
    validate_str as _vs,
    validate_str_list as _vsl,
    validate_uuid as _vuuid,
)
from blog_history import get_blog_stats, get_history_list, get_blog_text, save_blog_entry
from conversation_flow import generate_conversation_flow
from image_prompt_generator import generate_image_prompts_stream
from youtube_generator import generate_youtube_stream
from plan_guard import (
    check_blog_limit,
    check_image_session_limit,
    check_prompt_copy_limit,
)
from plan_notify import check_and_notify
from usage_tracker import log_usage
from agent_router import AgentRouter
from agent_middleware import AgentMiddleware
from config_loader import load_config
from sse_utils import with_keepalive

# 프로젝트 루트 (src/routers/blog.py 기준 3단계 위)
ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()

# ─────────────────────────────────────────────────────────────
# 모듈 인스턴스 + Rate limit 상태 (main.py 128~180에서 이동)
# ─────────────────────────────────────────────────────────────

agent_router = AgentRouter()
agent_middleware = AgentMiddleware()

# 분당 요청 수 추적 (clinic_id → [timestamp, ...])
_rate_buckets: dict = defaultdict(list)
_RATE_LIMIT = 60  # 분당 최대 요청 수


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _create_anthropic_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    return anthropic.Anthropic(api_key=api_key)

def _check_rate_limit(clinic_id: str) -> bool:
    """True = 허용, False = 초과 (60 req/min per clinic)"""
    now = _time_now()
    bucket = _rate_buckets[clinic_id]
    _rate_buckets[clinic_id] = [t for t in bucket if now - t < 60]
    if len(_rate_buckets[clinic_id]) >= _RATE_LIMIT:
        return False
    _rate_buckets[clinic_id].append(now)
    return True

# K-7 보안 감사 (2026-05-04): 블로그 생성 라우트 입력 한도.
# /generate 와 /build-prompt 가 공유. 어뷰저의 거대 입력으로 Claude API 비용
# 트리거 차단 + 프롬프트 인젝션 부수 방어.
def _validate_blog_inputs(body: dict) -> dict:
    """블로그 본문 생성 입력 검증 — /generate, /build-prompt 공용.

    각 필드의 길이/개수 한도를 부과하고 정규화된 값을 dict 로 반환한다.
    실패 시 HTTPException(400) — 어떤 필드인지 노출하지 않음 (어뷰저 정보 비노출).
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")

    # 단일 문자열 필드
    keyword      = _vs(body.get("keyword"), "blog.keyword", max_len=200)
    mode         = _vs(body.get("mode"), "blog.mode", max_len=20) or "정보"
    reader_level = _vs(body.get("reader_level"), "blog.reader_level", max_len=20) or "일반인"
    clinic_info  = _vs(body.get("clinic_info"), "blog.clinic_info", max_len=2000)
    format_id_raw = body.get("format_id")
    format_id    = _vs(format_id_raw, "blog.format_id", max_len=50) if format_id_raw else None

    # 리스트 필드 — seo_keywords 는 문자열로 들어올 수도 있음 (쉼표 구분)
    seo_raw = body.get("seo_keywords", [])
    if isinstance(seo_raw, str):
        # 길이 cap 후 분할 (문자열로 들어오는 경로에서도 길이 제한)
        seo_str = _vs(seo_raw, "blog.seo_keywords_str", max_len=1000)
        seo_keywords = [k.strip() for k in seo_str.split(",") if k.strip()]
        if len(seo_keywords) > 20:
            raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
        for kw in seo_keywords:
            if len(kw) > 50:
                raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
    else:
        seo_keywords = _vsl(seo_raw, "blog.seo_keywords", max_items=20, max_each=50)

    explanation_types = _vsl(
        body.get("explanation_types"), "blog.explanation_types",
        max_items=10, max_each=50,
    )

    # answers — dict 의 모든 value 길이 제한 (모든 값이 user_message 에 concat 됨)
    answers_raw = body.get("answers") or {}
    if not isinstance(answers_raw, dict):
        raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
    if len(answers_raw) > 20:
        raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
    answers: dict = {}
    for k, v in answers_raw.items():
        if not isinstance(k, str) or len(k) > 50:
            raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
        if v is None:
            continue
        if not isinstance(v, str):
            raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
        if len(v) > 500:
            raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
        answers[k] = v

    # materials — text / webLinks / youtubeLinks 3 필드 dict
    mat_raw = body.get("materials") or {}
    if not isinstance(mat_raw, dict):
        raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
    materials = {
        "text": _vs(mat_raw.get("text"), "materials.text", max_len=5000),
        "webLinks": _vsl(
            mat_raw.get("webLinks"), "materials.webLinks",
            max_items=20, max_each=500,
        ),
        "youtubeLinks": _vsl(
            mat_raw.get("youtubeLinks"), "materials.youtubeLinks",
            max_items=20, max_each=200,
        ),
    }

    # char_count {min, max} 또는 None
    cc_raw = body.get("char_count")
    char_count = None
    if cc_raw is not None:
        if not isinstance(cc_raw, dict):
            raise HTTPException(status_code=400, detail="입력 형식이 올바르지 않습니다.")
        cmin = _vi(cc_raw.get("min"), "char_count.min", min_val=100, max_val=9999)
        cmax = _vi(cc_raw.get("max"), "char_count.max", min_val=100, max_val=9999)
        char_count = {"min": cmin, "max": cmax}

    return {
        "keyword": keyword,
        "answers": answers,
        "materials": materials,
        "mode": mode,
        "reader_level": reader_level,
        "seo_keywords": seo_keywords,
        "clinic_info": clinic_info,
        "format_id": format_id,
        "explanation_types": explanation_types,
        "char_count": char_count,
    }


def _ai_error_to_http(exc) -> HTTPException:
    """ai_client.AIClientError → HTTPException 변환."""
    kind_to_status = {
        "auth": 502,           # 관리자 키 문제
        "rate_limit": 429,     # OpenAI rate limit
        "bad_request": 400,
        "timeout": 504,
        "server": 502,
        "unknown": 500,
    }
    status_code = kind_to_status.get(exc.kind, 500)
    return HTTPException(
        status_code=status_code,
        detail={"kind": exc.kind, "message": exc.message},
    )


# ─────────────────────────────────────────────────────────────
# HTML 페이지
# ─────────────────────────────────────────────────────────────

@router.get("/blog")
async def blog_page(request: Request):
    """블로그 생성기 — 인증 필요. 2026-05-01부터 챗 UI로 통합."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "blog_chat.html", headers=_NO_CACHE)

@router.get("/app")
async def app_shell(request: Request):
    """앱 쉘 — 사이드바 고정 레이아웃 (iframe으로 콘텐츠 로드)"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "app.html", headers=_NO_CACHE)

@router.get("/blog/chat")
async def blog_chat_page(request: Request):
    """블로그 챗 인터페이스 — 인증 + Cohort 1 베타 플래그 필요 (Phase 1F).

    `clinics.chat_beta_enabled = 1` 인 클리닉만 진입.
    미허용 클리닉은 기존 `/blog` 폼으로 fallback.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    # 베타 플래그 검사 — 인증 토큰에서 clinic_id 추출 후 DB lookup
    try:
        from auth_manager import decode_token
        payload = decode_token(token)
        clinic_id = payload.get("clinic_id") if payload else None
    except Exception:
        clinic_id = None
    if clinic_id:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT chat_beta_enabled FROM clinics WHERE id = ?", (clinic_id,),
            ).fetchone()
        if not row or not row["chat_beta_enabled"]:
            return RedirectResponse("/blog")
    return FileResponse(ROOT / "templates" / "blog_chat.html", headers=_NO_CACHE)

@router.get("/youtube")
async def youtube_page(request: Request, current_user: dict = Depends(get_current_user)):
    """YouTube 생성기 페이지"""
    return FileResponse(ROOT / "templates" / "youtube.html")

@router.get("/chat")
async def chat_page(request: Request, current_user: dict = Depends(get_current_user)):
    """AI 도우미 — 베타 이후 자연어 라우팅 어시스턴트로 재구현 예정 (현재 비활성).
    URL 직접 접근 시 대시보드로 리다이렉트."""
    return RedirectResponse("/dashboard")


# ─────────────────────────────────────────────────────────────
# /api/blog 일반 API
# ─────────────────────────────────────────────────────────────

@router.post("/api/blog/track-prompt-copy")
async def track_prompt_copy(user: dict = Depends(get_current_user)):
    """프롬프트 복사 횟수 기록 — 한도 초과 시 429 반환"""
    clinic_id = user["clinic_id"]
    check_prompt_copy_limit(clinic_id)
    log_usage(clinic_id, "prompt_copy", {})
    return JSONResponse({"ok": True})

@router.get("/api/blog/stats")
async def blog_stats(user: dict = Depends(get_current_user)):
    """대시보드 글 카드용 통계.

    - 본인 클리닉 카운트만 (베타 가입일 이후)
    - 플랜 한도 포함 (코호트 1: standard 30/월)
    """
    clinic_id = user["clinic_id"]
    since = None
    plan_id = "free"
    plan_limit_month = 3
    try:
        with __import__('db_manager').get_db() as conn:
            row = conn.execute(
                "SELECT created_at, plan_id FROM clinics WHERE id = ?",
                (clinic_id,),
            ).fetchone()
        if row:
            since = row["created_at"]
            plan_id = (row["plan_id"] or "free").strip().lower() or "free"
    except Exception:
        pass

    # 코호트 1 베타: standard 30/월 강제. 추후 plan_guard 연동 시 분기.
    if plan_id == "pro":
        plan_limit_month = 80
    elif plan_id == "standard" or plan_id == "trial":
        plan_limit_month = 30
    else:
        plan_limit_month = 30  # 베타 코호트 1 기본 standard 30 (사용자 결정)

    stats = get_blog_stats(clinic_id=clinic_id, since=since)
    stats["plan_id"] = plan_id
    stats["plan_limit_month"] = plan_limit_month
    return JSONResponse(stats)

@router.post("/api/blog/history/{entry_id}/publish-check")
async def publish_check(entry_id: int, user: dict = Depends(get_current_user)):
    """발행 확인 대기 등록 — 네이버 블로그 아이디 필요"""
    from naver_checker import add_pending_check, get_pending_by_stat_id, is_naver_configured
    from blog_history import get_history_list

    if not is_naver_configured():
        raise HTTPException(status_code=400, detail="네이버 API 키가 설정되지 않았습니다. (.env NAVER_CLIENT_ID/SECRET 필요)")

    # 네이버 블로그 아이디 조회
    with __import__('db_manager').get_db() as conn:
        row = conn.execute(
            "SELECT naver_blog_id FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    naver_blog_id = (row["naver_blog_id"] or "").strip() if row else ""
    if not naver_blog_id:
        raise HTTPException(status_code=400, detail="네이버 블로그 아이디가 설정되지 않았습니다. 설정 > 한의원 프로필에서 등록하세요.")

    # 블로그 항목 조회 (본인 클리닉만)
    history = get_history_list(clinic_id=user["clinic_id"], page=1, per_page=9999)
    target = next((e for e in history["items"] if e["id"] == entry_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="블로그 항목을 찾을 수 없습니다.")

    pending = add_pending_check(
        blog_stat_id=entry_id,
        keyword=target["keyword"],
        title=target.get("title") or target["keyword"],
        naver_blog_id=naver_blog_id,
    )
    return JSONResponse({"status": "ok", "pending": pending})

@router.get("/api/blog/notifications")
async def blog_notifications(user: dict = Depends(get_current_user)):
    """대시보드 알림 조회 — found + expired 미확인 항목"""
    from naver_checker import get_dashboard_notifications
    items = get_dashboard_notifications()
    return JSONResponse({"items": items})

@router.get("/api/blog/publish-status")
async def blog_publish_status(user: dict = Depends(get_current_user)):
    """블로그 항목별 발행 확인 상태 일괄 반환 — by_id={stat_id: {status, found_url, ...}}"""
    from naver_checker import _load as _load_pending
    items = _load_pending()
    by_id: dict = {}
    for it in items:
        by_id[int(it.get("blog_stat_id", 0))] = {
            "status": it.get("status"),
            "found_url": it.get("found_url"),
            "started_at": it.get("started_at"),
            "check_count": it.get("check_count", 0),
        }
    return JSONResponse({"by_id": by_id})

@router.post("/api/blog/notifications/{pending_id}/dismiss")
async def dismiss_notification(pending_id: int, user: dict = Depends(get_current_user)):
    """알림 dismiss"""
    from naver_checker import mark_notified
    mark_notified(pending_id)
    return JSONResponse({"status": "ok"})

@router.get("/api/blog/history")
async def blog_history(
    page: int = 1,
    per_page: int = 20,
    user: dict = Depends(get_current_user),
):
    return JSONResponse(get_history_list(clinic_id=user["clinic_id"], page=page, per_page=per_page))

@router.get("/api/blog/history/{entry_id}/text")
async def blog_history_text(entry_id: int, user: dict = Depends(get_current_user)):
    text = get_blog_text(entry_id, clinic_id=user["clinic_id"])
    if text is None:
        raise HTTPException(status_code=404, detail="전문을 찾을 수 없거나 만료되었습니다.")
    return JSONResponse({"text": text})


# ─────────────────────────────────────────────────────────────
# /api/blog-chat
# ─────────────────────────────────────────────────────────────

@router.get("/api/blog-chat/session/{session_id}")
async def api_blog_chat_session_get(
    session_id: str, user: dict = Depends(get_current_user)
):
    """세션 전체 state 조회 — 다중 탭/새로고침 복구용."""
    from blog_chat_state import get_session, serialize_message, stage_text

    try:
        state = get_session(session_id, clinic_id=user["clinic_id"])
    except LookupError:
        return JSONResponse({"detail": "세션을 찾을 수 없습니다."}, status_code=404)
    except PermissionError:
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    return JSONResponse({
        "session_id": state.session_id,
        "stage": state.stage.value,
        "stage_text": stage_text(state.stage),
        "messages": [serialize_message(m) for m in state.messages],
        "topic": state.topic,
        "length_chars": state.length_chars,
        "seo_keywords": state.seo_keywords,
        "quota": state.quota,
        "is_admin": _is_admin_clinic(user) and user.get("role") == "chief_director",
    })


# ─────────────────────────────────────────────────────────────
# Legacy generators (SSE 제외)
# ─────────────────────────────────────────────────────────────

@router.post("/conversation-flow")
async def get_conversation_flow(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    keyword = body.get("keyword", "").strip()

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    config = load_config()
    if not config["flow"].get("questions_enabled", True):
        return {"questions": []}

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": ".env 파일에 ANTHROPIC_API_KEY를 설정해주세요."}

    try:
        questions = generate_conversation_flow(keyword, api_key)
        return {"questions": questions}
    except ValueError as e:
        return {"error": str(e)}

@router.post("/build-prompt")
async def build_prompt_endpoint(request: Request, user: dict = Depends(get_current_user)):
    """
    Claude API 호출 없이 프롬프트 텍스트만 조립해서 반환한다.
    T1(프롬프트 복사) 기능 — plan_guard 한도 차감 없음.
    """
    body = await request.json()
    # K-7 입력 길이 제한 — 어뷰저의 거대 입력으로 Claude API 비용 트리거 차단.
    fields = _validate_blog_inputs(body)
    if not fields["keyword"]:
        return {"error": "주제를 입력해주세요."}

    try:
        result = build_prompt_text(
            keyword=fields["keyword"],
            answers=fields["answers"],
            materials=fields["materials"],
            mode=fields["mode"],
            reader_level=fields["reader_level"],
            seo_keywords=fields["seo_keywords"],
            clinic_info=fields["clinic_info"],
            format_id=fields["format_id"],
            explanation_types=fields["explanation_types"],
            clinic_id=user["clinic_id"],
        )
        return {
            "system_prompt": result["system_prompt"],
            "user_message": result["user_message"],
            "format_id": result.get("format_id"),
            "hook_id": result.get("hook_id"),
        }
    except Exception as exc:
        return {"error": str(exc)}

@router.post("/generate-youtube")
async def generate_youtube(request: Request, user: dict = Depends(get_current_user)):
    """
    YouTube 6단계 파이프라인 실행 — SSE 스트리밍.

    Request body:
        topic:   str (필수) — 영상 주제
        length:  "short" | "long"          (기본: "long")
        style:   "educational" | "marketing" (기본: "educational")
    """
    body = await request.json()
    topic = body.get("topic", "").strip()
    if not topic:
        async def _err():
            import json as _j
            yield f"data: {_j.dumps({'type': 'error', 'step': 'init', 'msg': '영상 주제를 입력해주세요.'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    options = {
        "length": body.get("length", "long"),
        "style": body.get("style", "educational"),
    }

    return StreamingResponse(
        with_keepalive(generate_youtube_stream(
            topic=topic,
            clinic_id=user["clinic_id"],
            options=options,
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────

@router.get("/api/agents/available")
async def get_available_agents(current_user: dict = Depends(get_current_user)):
    agents = agent_router.get_available_agents(role=current_user["role"])
    return {"agents": agents}

@router.post("/api/agent/chat")
async def agent_chat(request: Request, current_user: dict = Depends(get_current_user)):
    # K-7 보안 감사 (2026-05-04): 베타 미사용 라우트 비활성화.
    # body parse / rate_limit / Anthropic 호출 모두 차단 — 어뷰저의 입력 길이 폭주 +
    # Claude API 비용 트리거 진입점 봉인. 재도입 시 아래 410 응답 제거 + body
    # validate_str(message, "message", 2000) 추가.
    return JSONResponse(
        {"agent_name": None, "response": "이 기능은 현재 비활성화되어 있습니다.", "error": True},
        status_code=410,
    )
    body = await request.json()
    message = body.get("message", "").strip()
    requested_agent = body.get("agent")

    # Rate limit 확인 (clinic 단위)
    clinic_id = str(current_user.get("clinic_id", "default"))
    if not _check_rate_limit(clinic_id):
        return {"agent_name": None, "response": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.", "error": True}

    # 에이전트 결정
    if requested_agent:
        try:
            agent_router.get_agent_config(requested_agent)  # 화이트리스트 검증 (Path Traversal 방지)
        except ValueError:
            return {"agent_name": None, "response": "유효하지 않은 에이전트입니다.", "error": True}
        available = [a["name"] for a in agent_router.get_available_agents(current_user["role"])]
        if requested_agent not in available:
            return {"agent_name": requested_agent, "response": "접근 권한이 없습니다.", "error": True}
        agent_name = requested_agent
    else:
        agent_name = agent_router.classify_intent(message)

    if not agent_name:
        return {"agent_name": None, "response": "매칭되는 에이전트가 없습니다. 더 구체적으로 질문해 주세요."}

    system_prompt = agent_router.get_system_prompt(agent_name)
    config = agent_router.get_agent_config(agent_name)

    # Claude API 호출 (최대 3회 시도 — timeout/429 대응)
    client = _create_anthropic_client()
    response_msg = None
    for attempt in range(3):
        try:
            response_msg = client.messages.create(
                model=config.get("model", "claude-sonnet-4-6"),
                max_tokens=config.get("max_tokens", 2000),
                system=system_prompt,
                messages=[{"role": "user", "content": message}],
            )
            break
        except (anthropic.APITimeoutError, anthropic.RateLimitError):
            if attempt == 2:
                return {"agent_name": agent_name, "response": "현재 AI 서비스에 일시적인 문제가 있습니다. 잠시 후 다시 시도해 주세요.", "error": True}
            import time as _time
            _time.sleep(1)
        except anthropic.APIError:
            return {"agent_name": agent_name, "response": "AI 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.", "error": True}

    response_text = response_msg.content[0].text

    # 할루시네이션 감지 + 경고 주석 추가
    hallucination_risk = agent_middleware.check_hallucination_risk(response_text)
    if hallucination_risk:
        response_text += "\n\n⚠️ 의료 정보는 반드시 담당 원장의 확인을 거치시기 바랍니다."

    # 로깅 + 비용 추적 (메시지 원문 비저장)
    agent_middleware.log_request(
        user_id=str(current_user.get("id", "unknown")),
        agent_name=agent_name,
        message=message,
        input_tokens=response_msg.usage.input_tokens,
        output_tokens=response_msg.usage.output_tokens,
    )

    return {
        "agent_name": agent_name,
        "response": response_text,
        "hallucination_warning": hallucination_risk,
    }


# ─────────────────────────────────────────────────────────────
# Image API
# ─────────────────────────────────────────────────────────────

@router.get("/api/image/stats")
async def image_stats(user: dict = Depends(get_current_user)):
    """대시보드 이미지 카드용 통계.

    - 본인 클리닉 카운트만 (베타 가입일 이후)
    - 1 세트 = 5장. 카드에 세트·이미지 둘 다 표시 가능.
    - 플랜 한도: 코호트 1 standard 30 세트/월
    """
    from image_session_manager import get_user_image_stats
    clinic_id = user["clinic_id"]
    since = None
    plan_id = "free"
    plan_limit_month = 30
    try:
        with __import__('db_manager').get_db() as conn:
            row = conn.execute(
                "SELECT created_at, plan_id FROM clinics WHERE id = ?",
                (clinic_id,),
            ).fetchone()
        if row:
            since = row["created_at"]
            plan_id = (row["plan_id"] or "free").strip().lower() or "free"
    except Exception:
        pass
    if plan_id == "pro":
        plan_limit_month = 80
    elif plan_id == "standard" or plan_id == "trial":
        plan_limit_month = 30
    else:
        plan_limit_month = 30  # 베타 코호트 1 기본

    stats = get_user_image_stats(clinic_id=clinic_id, since=since)
    stats["plan_id"] = plan_id
    stats["plan_limit_month"] = plan_limit_month
    return JSONResponse(stats)

@router.post("/api/image/generate-initial")
async def api_image_generate_initial(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """블로그 1편당 첫 5장 생성 + 세션 생성.

    Body: {"prompt": str, "keyword": str (선택)}
    Response: {"session_id": str, "images": [b64...], "size": str, "quality": str,
               "quota": {regen: {...}, edit: {...}}}
    """
    body = await request.json()
    # K-7 입력 길이 제한 — 어뷰저의 거대 prompt 로 OpenAI API 비용 트리거 차단.
    prompt = _vs(body.get("prompt"), "image.prompt", max_len=2000)
    keyword = _vs(body.get("keyword"), "image.keyword", max_len=200)
    if not prompt:
        raise HTTPException(status_code=400, detail="이미지 프롬프트가 비어 있습니다.")

    # K-8 (2026-05-04) 누적 이미지 세션 한도 체크 — 어뷰저의 generate-initial
    # 무한 호출 차단. free/trial 은 누적 30 (= 베타 25블로그 + 5 buffer), 유료 무제한.
    check_image_session_limit(user["clinic_id"])

    from plan_guard import get_effective_plan
    from image_generator import (
        generate_initial_set, get_quota_status, get_plan_dimensions,
        AIClientError as IGError,  # noqa: F401
    )
    from ai_client import AIClientError
    from image_session_manager import create_session
    from cost_logger import record_cost
    from pricing import calculate_openai_image_cost

    plan = get_effective_plan(user["clinic_id"])
    plan_id = plan.get("plan_id", "free")

    # session_id 를 호출 전에 발급 — cost_logs.image_session_id 매핑용 (Commit 5b).
    session_id = create_session(
        clinic_id=user["clinic_id"],
        user_id=user["id"],
        blog_keyword=keyword,
        plan_id_at_start=plan_id,
    )

    try:
        result = generate_initial_set(prompt=prompt, plan_id=plan_id)
    except AIClientError as exc:
        # input_blocked (moderation 400) 만 비용 기록 — 그 외는 API 미호출 가정.
        if exc.kind == "bad_request":
            try:
                size_b, quality_b = get_plan_dimensions(plan_id)
                _cost = calculate_openai_image_cost(
                    "gpt-image-2", size_b, quality_b, count=0, outcome="input_blocked",
                )
                record_cost(
                    kind="openai_image_init", clinic_id=user["clinic_id"],
                    cost_usd=_cost, model="gpt-image-2",
                    image_session_id=session_id,
                    metadata={"outcome": "input_blocked", "keyword": keyword},
                )
            except Exception:
                pass
        raise _ai_error_to_http(exc)

    # 정상: per-image × len(images). 단일 호출 = 1 row.
    try:
        _cost = calculate_openai_image_cost(
            "gpt-image-2", result.size, result.quality,
            count=len(result.images), outcome="success",
        )
        record_cost(
            kind="openai_image_init", clinic_id=user["clinic_id"],
            cost_usd=_cost, model="gpt-image-2",
            image_session_id=session_id,
            metadata={
                "outcome": "success",
                "image_count": len(result.images),
                "plan_id": result.plan_id,
                "keyword": keyword,
            },
        )
    except Exception:
        pass  # fail-soft

    # 한의원 로고 합성 (콘텐츠 개인화 — 베타 게이트 ④)
    from logo_compositor import apply_logo_to_b64_images
    composed_images = apply_logo_to_b64_images(result.images, user["clinic_id"])

    return JSONResponse({
        "session_id": session_id,
        "images": composed_images,
        "size": result.size,
        "quality": result.quality,
        "plan_id": result.plan_id,
        "quota": get_quota_status(plan_id, regen_used=0, edit_used=0),
    })

@router.post("/api/image/regenerate")
async def api_image_regenerate(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """같은 프롬프트로 n장 재생성. plan별 무료 한도 적용.

    Body: {"session_id": str, "prompt": str, "n"?: int (1~5, 기본 5)}
        n=1은 카드별 [↺] 단일 재생성 (1장도 한도 1회 차감).
    """
    body = await request.json()
    session_id = (body.get("session_id") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    try:
        n = int(body.get("n") or 5)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(5, n))
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id가 필요합니다.")
    if not prompt:
        raise HTTPException(status_code=400, detail="이미지 프롬프트가 비어 있습니다.")

    from image_session_manager import get_session, increment_regen
    from image_generator import (
        regenerate_set,
        get_quota_status,
        ImageQuotaExceeded,
    )
    from ai_client import AIClientError

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="이미지 세션을 찾을 수 없습니다.")
    if sess["clinic_id"] != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    plan_id = sess["plan_id_at_start"] or "free"
    regen_used = sess["regen_count"]
    edit_used = sess["edit_count"]

    from cost_logger import record_cost
    from pricing import calculate_openai_image_cost
    from image_generator import get_plan_dimensions

    try:
        result = regenerate_set(prompt=prompt, plan_id=plan_id, regen_used=regen_used, n=n)
    except ImageQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "kind": "quota_exceeded",
                "type": "regen",
                "plan_id": exc.plan_id,
                "used": exc.used,
                "limit": exc.limit,
                "message": (
                    f"이 블로그의 이미지 재생성 한도({exc.limit}회)에 도달했어요."
                ),
            },
        )
    except AIClientError as exc:
        if exc.kind == "bad_request":
            try:
                size_b, quality_b = get_plan_dimensions(plan_id)
                _cost = calculate_openai_image_cost(
                    "gpt-image-2", size_b, quality_b, count=0, outcome="input_blocked",
                )
                record_cost(
                    kind="openai_image_regen", clinic_id=user["clinic_id"],
                    cost_usd=_cost, model="gpt-image-2",
                    image_session_id=session_id,
                    metadata={"outcome": "input_blocked", "n": n},
                )
            except Exception:
                pass
        raise _ai_error_to_http(exc)

    # 정상: 단일 호출 = 1 row, count=len(images).
    try:
        _cost = calculate_openai_image_cost(
            "gpt-image-2", result.size, result.quality,
            count=len(result.images), outcome="success",
        )
        record_cost(
            kind="openai_image_regen", clinic_id=user["clinic_id"],
            cost_usd=_cost, model="gpt-image-2",
            image_session_id=session_id,
            metadata={
                "outcome": "success",
                "image_count": len(result.images),
                "plan_id": result.plan_id,
            },
        )
    except Exception:
        pass

    new_regen_count = increment_regen(session_id, user["clinic_id"])

    # 한의원 로고 합성 (콘텐츠 개인화 — 베타 게이트 ④)
    from logo_compositor import apply_logo_to_b64_images
    composed_images = apply_logo_to_b64_images(result.images, user["clinic_id"])

    return JSONResponse({
        "session_id": session_id,
        "images": composed_images,
        "size": result.size,
        "quality": result.quality,
        "plan_id": result.plan_id,
        "quota": get_quota_status(
            plan_id, regen_used=new_regen_count, edit_used=edit_used
        ),
    })

@router.post("/api/image/edit")
async def api_image_edit(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """1장 부분 수정. multipart/form-data — image (file) + prompt + session_id + mask(선택).

    Form fields:
      - session_id: str
      - prompt: str
      - image: file (PNG bytes)
      - mask: file (선택, alpha channel PNG)
    """
    form = await request.form()
    session_id = (form.get("session_id") or "").strip()
    prompt = (form.get("prompt") or "").strip()
    image_file = form.get("image")
    mask_file = form.get("mask")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id가 필요합니다.")
    if not prompt:
        raise HTTPException(status_code=400, detail="수정 프롬프트가 비어 있습니다.")
    if image_file is None or not hasattr(image_file, "read"):
        raise HTTPException(status_code=400, detail="image 파일이 필요합니다.")

    image_bytes = await image_file.read()
    mask_bytes = await mask_file.read() if mask_file is not None and hasattr(mask_file, "read") else None

    from image_session_manager import get_session, increment_edit
    from image_generator import edit_image, get_quota_status, ImageQuotaExceeded, get_plan_dimensions
    from ai_client import AIClientError
    from cost_logger import record_cost
    from pricing import calculate_openai_image_cost

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="이미지 세션을 찾을 수 없습니다.")
    if sess["clinic_id"] != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    plan_id = sess["plan_id_at_start"] or "free"
    regen_used = sess["regen_count"]
    edit_used = sess["edit_count"]

    # edit 모델 = gpt-image-1.5 (ai_client 내부 고정).
    EDIT_MODEL = "gpt-image-1.5"

    try:
        result = edit_image(
            image_bytes=image_bytes,
            prompt=prompt,
            plan_id=plan_id,
            edit_used=edit_used,
            mask_bytes=mask_bytes,
        )
    except ImageQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "kind": "quota_exceeded",
                "type": "edit",
                "plan_id": exc.plan_id,
                "used": exc.used,
                "limit": exc.limit,
                "message": (
                    f"이 블로그의 이미지 수정 한도({exc.limit}회)에 도달했어요."
                ),
            },
        )
    except AIClientError as exc:
        if exc.kind == "bad_request":
            try:
                size_b, quality_b = get_plan_dimensions(plan_id)
                _cost = calculate_openai_image_cost(
                    EDIT_MODEL, size_b, quality_b, count=0, outcome="input_blocked",
                )
                record_cost(
                    kind="openai_image_edit", clinic_id=user["clinic_id"],
                    cost_usd=_cost, model=EDIT_MODEL,
                    image_session_id=session_id,
                    metadata={"outcome": "input_blocked", "tokens_unmeasured": True},
                )
            except Exception:
                pass
        raise _ai_error_to_http(exc)

    # edits image_input 토큰은 SDK 응답에서 분리 보고 안 함 → 0 으로 기록.
    # 정확한 토큰 회계는 Phase 2 (메타에 표식).
    try:
        _cost = calculate_openai_image_cost(
            EDIT_MODEL, result.size, result.quality,
            count=len(result.images), outcome="success",
        )
        record_cost(
            kind="openai_image_edit", clinic_id=user["clinic_id"],
            cost_usd=_cost, model=EDIT_MODEL,
            image_session_id=session_id,
            metadata={
                "outcome": "success",
                "image_count": len(result.images),
                "plan_id": result.plan_id,
                "tokens_unmeasured": True,
            },
        )
    except Exception:
        pass

    new_edit_count = increment_edit(session_id, user["clinic_id"])

    # 한의원 로고 합성 (콘텐츠 개인화 — 베타 게이트 ④)
    from logo_compositor import apply_logo_to_b64_images
    composed_images = apply_logo_to_b64_images(result.images, user["clinic_id"])

    return JSONResponse({
        "session_id": session_id,
        "images": composed_images,
        "size": result.size,
        "quality": result.quality,
        "plan_id": result.plan_id,
        "quota": get_quota_status(
            plan_id, regen_used=regen_used, edit_used=new_edit_count
        ),
    })

@router.get("/api/image/session/{session_id}")
async def api_image_session_status(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """세션 상태·한도 조회 (UI 카운터 동기화용)."""
    from image_session_manager import get_session
    from image_generator import get_quota_status

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="이미지 세션을 찾을 수 없습니다.")
    if sess["clinic_id"] != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    plan_id = sess["plan_id_at_start"] or "free"
    return JSONResponse({
        "session_id": session_id,
        "plan_id": plan_id,
        "blog_keyword": sess["blog_keyword"],
        "regen_count": sess["regen_count"],
        "edit_count": sess["edit_count"],
        "created_at": sess["created_at"],
        "last_active_at": sess["last_active_at"],
        "quota": get_quota_status(
            plan_id,
            regen_used=sess["regen_count"],
            edit_used=sess["edit_count"],
        ),
    })


@router.post("/api/image/session/{session_id}/cancel")
async def api_image_session_cancel(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    """이미지 세션 ID로 직접 취소 (image_session_started frame 수신 후)."""
    from image_session_manager import get_session
    from blog_chat_flow import cancel_image_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="이미지 세션을 찾을 수 없습니다.")
    if sess["clinic_id"] != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    cancel_image_session(session_id)
    return JSONResponse({"ok": True, "session_id": session_id})


@router.post("/api/blog-chat/{chat_session_id}/cancel-image")
async def api_blog_chat_cancel_image(
    chat_session_id: str,
    user: dict = Depends(get_current_user),
):
    """블로그 챗 세션 ID로 이미지 생성 취소 (2026-05-02 추가, image_session_id 없이도 동작).

    클라이언트는 stage가 'image'에 진입하면 즉시 취소 버튼을 표시하고
    이 엔드포인트로 호출한다. 서버가 state.image_session_id를 조회해서
    실제 cancel set에 추가한다. 아직 image_session이 생성되기 전이면
    'pending' cancel mark로 저장 → 생성 직후 즉시 취소 처리.
    """
    from blog_chat_state import load_session
    from blog_chat_flow import cancel_image_session

    state = load_session(chat_session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="블로그 챗 세션을 찾을 수 없습니다.")
    if state.clinic_id != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    image_sid = state.image_session_id or ""
    if image_sid:
        cancel_image_session(image_sid)
        return JSONResponse({"ok": True, "image_session_id": image_sid, "mode": "direct"})

    # image_session이 아직 생성되기 전 — 챗 session_id로 pending 마크
    # (서버 _stream_generator_for_image에서 _create_image_session 직후 추가 체크)
    cancel_image_session(f"pending:{chat_session_id}")
    return JSONResponse({"ok": True, "image_session_id": None, "mode": "pending"})


# ─────────────────────────────────────────────────────────────
# C2 — SSE 3건 (main.py 753~1032에서 이동, v0.9.0 / 2026-05-02)
# ─────────────────────────────────────────────────────────────


@router.post("/api/blog-chat/turn")
async def api_blog_chat_turn(request: Request, user: dict = Depends(get_current_user)):
    """블로그 챗 1턴.

    Body: { session_id?: str, user_input?: str }
    Response (1D-1): JSON { session_id, stage, stage_text, messages, quota }
      - 결정론적 옵션 매칭 (번호/정확 라벨). 모호한 입력은 ambiguous 메시지.
    Phase 1D-2: 자연어 해석 fallback (짧은 LLM 호출).
    Phase 1D-3: generating stage 진입 시 text/event-stream으로 분기.
    """
    from blog_chat_flow import process_turn
    from blog_chat_state import (
        append_message,
        create_session,
        get_session,
        save_session,
        serialize_message,
        stage_text,
    )

    body = await request.json()
    session_id = (body.get("session_id") or "").strip()
    user_input = (body.get("user_input") or "").strip()

    if user_input and len(user_input) > 2000:
        return JSONResponse(
            {"detail": "입력은 2,000자 이내로 작성해주세요."}, status_code=400
        )

    # K-7 입력 형식 검증 — session_id 가 비어있지 않으면 UUID4 정규식 강제.
    # 어뷰저가 거대 문자열로 DB lookup 부담 트리거하던 진입점 차단.
    if session_id:
        session_id = _vuuid(session_id, "blog_chat.session_id")

    # 신규 세션 — UUID4 발급 + 첫 인사
    # user_input이 있으면 그 입력으로 process_turn까지 1회 진행 (사용자 첫 액션 흐름)
    if not session_id:
        state = create_session(clinic_id=user["clinic_id"], user_id=user["id"])
        append_message(
            state, "assistant",
            "안녕하세요, 원장님. 오늘은 어떤 주제로 글을 써볼까요?\n"
            "버튼을 클릭하시거나 직접 입력해주세요. "
            "여러 개 선택은 해당 번호를 입력해주세요.",
            options=[],
            meta={"stage_text": stage_text(state.stage)},
        )
        save_session(state)
        is_chat_admin = _is_admin_clinic(user) and user.get("role") == "chief_director"
        # 빈 입력 — 인사만 반환 (현재 클라는 빈 화면을 정적으로 사용하므로 호출되지 않음)
        if not user_input:
            return JSONResponse({
                "session_id": state.session_id,
                "stage": state.stage.value,
                "stage_text": stage_text(state.stage),
                "messages": [serialize_message(m) for m in state.messages],
                "quota": state.quota,
                "is_admin": is_chat_admin,
            })
        # 첫 입력 — 인사 + user 메시지 + 다음 stage 메시지 모두 한 번에
        from blog_chat_flow import process_turn
        response = process_turn(state, user_input)
        # 신규 세션의 첫 응답엔 인사도 포함되도록 latest_n 보정
        response["messages"] = [
            serialize_message(m) for m in state.messages[-3:]
        ]
        response["is_admin"] = is_chat_admin
        return JSONResponse(response)

    # 기존 세션 복구
    try:
        state = get_session(session_id, clinic_id=user["clinic_id"])
    except LookupError:
        return JSONResponse({"detail": "세션을 찾을 수 없습니다."}, status_code=404)
    except PermissionError:
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    # CONFIRM_IMAGE 응답(예/아니오) 진입 → 본문 SSE streaming (2026-05-01)
    # SEO 단계는 turn JSON 응답으로 CONFIRM_IMAGE 옵션 메시지만 발송.
    # 사용자가 CONFIRM_IMAGE에 응답하면 SSE 시작.
    # 한도 체크는 streaming 시작 전에 수행 (사용자에게 즉시 차단 응답)
    from blog_chat_state import Stage as _Stage
    if state.stage == _Stage.CONFIRM_IMAGE:
        from blog_chat_flow import (
            CONFIRM_IMAGE_OPTIONS, match_option, process_turn, process_turn_streaming,
        )
        opt = match_option(CONFIRM_IMAGE_OPTIONS, user_input)
        # 옵션 매칭 실패 → 결정론·LLM fallback은 process_turn에 위임 (JSON 응답)
        if opt is None:
            response = process_turn(state, user_input)
            response["is_admin"] = _is_admin_clinic(user) and user.get("role") == "chief_director"
            return JSONResponse(response)
        # 매칭 성공 → state.auto_image 설정 + 본문 SSE 시작
        try:
            check_blog_limit(user["clinic_id"])
        except HTTPException as e:
            return JSONResponse(
                {"detail": e.detail, "kind": "quota_exceeded"}, status_code=e.status_code,
            )
        log_usage(user["clinic_id"], "blog_generation",
                  {"keyword": state.topic, "via": "blog_chat"})
        check_and_notify(user["clinic_id"])
        return StreamingResponse(
            with_keepalive(process_turn_streaming(state, user_input)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # IMAGE 단계 + "all" 옵션 매칭 → 5단계 텍스트 SSE (1F, M0 게이트)
    # "none"(이미지 없이 종료)은 process_turn JSON 분기로 가야 정상.
    if state.stage == _Stage.IMAGE:
        from blog_chat_flow import IMAGE_OPTIONS, match_option, process_turn_streaming
        opt = match_option(IMAGE_OPTIONS, user_input)
        if opt and opt.get("id") == "all":
            return StreamingResponse(
                with_keepalive(process_turn_streaming(state, user_input)),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    # 그 외 stage는 결정론 + Haiku fallback JSON 응답
    response = process_turn(state, user_input)
    response["is_admin"] = _is_admin_clinic(user) and user.get("role") == "chief_director"
    return JSONResponse(response)


def _stream_and_save(
    base_gen: Generator, keyword: str, tone: str, seo_keywords: list,
    clinic_id: Optional[int] = None,
) -> Generator:
    """SSE 스트림 통과 + done 이벤트 감지 시 이력 저장 및 첫 블로그 시각 기록"""
    collected: list = []
    for chunk in base_gen:
        yield chunk
        raw = chunk.removeprefix("data: ").strip()
        try:
            data = _json.loads(raw)
            if "text" in data:
                collected.append(data["text"])
            elif "replace" in data:
                # 키워드 보강 후처리로 전체 텍스트 교체
                collected = [data["replace"]]
            elif data.get("done"):
                blog_text = "".join(collected)
                char_count = len(blog_text)
                cost_krw = data.get("usage", {}).get("cost_krw", 0)
                entry_id = save_blog_entry(
                    keyword, tone, char_count, cost_krw, seo_keywords, blog_text,
                    clinic_id=clinic_id,
                )
                # done 직후 entry_id 이벤트를 별도로 발송 (프론트에서 발행 확인 버튼 활성화)
                yield f"data: {_json.dumps({'entry_id': entry_id}, ensure_ascii=False)}\n\n"
                # 첫 블로그 생성 완료 시각 기록 (COALESCE — 이후 호출은 무시)
                if clinic_id:
                    try:
                        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                        with __import__('db_manager').get_db() as conn:
                            conn.execute(
                                "UPDATE clinics SET first_blog_at = COALESCE(first_blog_at, ?) WHERE id = ?",
                                (now_iso, clinic_id),
                            )
                    except Exception:
                        pass  # 통계 기록 실패 시 생성 서비스에 영향 없음
        except Exception:
            pass


@router.post("/generate")
async def generate(request: Request, user: dict = Depends(get_current_user)):
    # 플랜 한도 체크 (무료 월 3편, 초과 시 429 반환)
    check_blog_limit(user["clinic_id"])

    body = await request.json()
    # K-7 입력 길이 제한 — 어뷰저의 거대 입력으로 Claude API 비용 트리거 차단.
    fields       = _validate_blog_inputs(body)
    keyword      = fields["keyword"]
    answers      = fields["answers"]
    materials    = fields["materials"]
    mode         = fields["mode"]
    reader_level = fields["reader_level"]
    seo_keywords = fields["seo_keywords"]
    clinic_info       = fields["clinic_info"]
    explanation_types = fields["explanation_types"]
    char_count        = fields["char_count"]
    format_id         = fields["format_id"]

    if not keyword:
        async def _err():
            yield f"data: {_json.dumps({'error': '주제를 입력해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def _err():
            yield f"data: {_json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    tone = answers.get("tone", "전문적") if answers else "전문적"

    # DB에서 원장 소개글 + 클리닉 특징/장점 자동 조회
    with __import__('db_manager').get_db() as _conn:
        _clinic_row = _conn.execute(
            "SELECT intro, blog_features FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    _db_intro = (_clinic_row["intro"] or "").strip() if _clinic_row else ""
    _db_features = (_clinic_row["blog_features"] or "").strip() if _clinic_row else ""

    # 블로그 생성기 입력값이 있으면 DB의 blog_features에 저장 (다음 접속 시 유지)
    if clinic_info:
        with __import__('db_manager').get_db() as _conn:
            _conn.execute(
                "UPDATE clinics SET blog_features = ? WHERE id = ?",
                (clinic_info, user["clinic_id"]),
            )
        _db_features = clinic_info  # 방금 저장한 값을 이번 생성에도 즉시 반영

    # intro + blog_features 합쳐서 주입 (둘 다 있을 때만)
    _parts = [p for p in [_db_intro, _db_features] if p]
    clinic_info = "\n\n".join(_parts)

    # 사용량 기록 (실패해도 서비스 계속)
    log_usage(user["clinic_id"], "blog_generation", {"keyword": keyword, "mode": mode})
    # 한도 80% 알림 — 비동기 스레드로 실행, 응답 경로에 영향 없음
    check_and_notify(user["clinic_id"])

    return StreamingResponse(
        with_keepalive(_stream_and_save(
            generate_blog_stream(
                keyword, answers, api_key, materials, mode, reader_level,
                seo_keywords=seo_keywords, clinic_info=clinic_info,
                explanation_types=explanation_types,
                char_count=char_count,
                format_id=format_id,
                clinic_id=user["clinic_id"],
            ),
            keyword, tone, seo_keywords,
            clinic_id=user["clinic_id"],
        )),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generate-image-prompts")
async def generate_image_prompts(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    keyword     = body.get("keyword", "").strip()
    blog_content = body.get("blog_content", "").strip()
    style       = body.get("style", "photorealistic")
    tone        = body.get("tone", "warm")

    if not keyword or not blog_content:
        async def _err():
            yield f"data: {_json.dumps({'error': '주제와 블로그 본문이 필요합니다.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def _err():
            yield f"data: {_json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    return StreamingResponse(
        with_keepalive(
            generate_image_prompts_stream(keyword, blog_content, api_key, style, tone)
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

