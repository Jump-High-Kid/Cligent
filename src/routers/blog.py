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

import os
from collections import defaultdict
from pathlib import Path
from time import time as _time_now

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
from blog_generator import build_prompt_text
from blog_history import get_blog_stats, get_history_list, get_blog_text
from conversation_flow import generate_conversation_flow
from youtube_generator import generate_youtube_stream
from plan_guard import check_prompt_copy_limit
from usage_tracker import log_usage
from agent_router import AgentRouter
from agent_middleware import AgentMiddleware
from config_loader import load_config

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

    # 블로그 항목 조회
    history = get_history_list(page=1, per_page=9999)
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
    return JSONResponse(get_history_list(page=page, per_page=per_page))

@router.get("/api/blog/history/{entry_id}/text")
async def blog_history_text(entry_id: int, user: dict = Depends(get_current_user)):
    text = get_blog_text(entry_id)
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
    keyword      = body.get("keyword", "").strip()
    answers      = body.get("answers", {})
    materials    = body.get("materials", {})
    mode         = body.get("mode", "정보")
    reader_level = body.get("reader_level", "일반인")
    seo_keywords = body.get("seo_keywords", [])
    clinic_info       = body.get("clinic_info", "")
    format_id         = body.get("format_id", None)
    explanation_types = body.get("explanation_types", [])

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    try:
        result = build_prompt_text(
            keyword=keyword,
            answers=answers,
            materials=materials,
            mode=mode,
            reader_level=reader_level,
            seo_keywords=seo_keywords,
            clinic_info=clinic_info,
            format_id=format_id,
            explanation_types=explanation_types,
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
        generate_youtube_stream(
            topic=topic,
            clinic_id=user["clinic_id"],
            options=options,
        ),
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
    prompt = (body.get("prompt") or "").strip()
    keyword = (body.get("keyword") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="이미지 프롬프트가 비어 있습니다.")

    from plan_guard import get_effective_plan
    from image_generator import generate_initial_set, get_quota_status, AIClientError as IGError  # noqa: F401
    from ai_client import AIClientError
    from image_session_manager import create_session

    plan = get_effective_plan(user["clinic_id"])
    plan_id = plan.get("plan_id", "free")

    try:
        result = generate_initial_set(prompt=prompt, plan_id=plan_id)
    except AIClientError as exc:
        raise _ai_error_to_http(exc)

    session_id = create_session(
        clinic_id=user["clinic_id"],
        user_id=user["id"],
        blog_keyword=keyword,
        plan_id_at_start=plan_id,
    )

    return JSONResponse({
        "session_id": session_id,
        "images": result.images,
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
                    f"{exc.plan_id} 플랜 재생성 무료 한도({exc.limit}회)에 도달했습니다. "
                    "초과 분은 정식 출시 후 종량제로 제공됩니다."
                ),
            },
        )
    except AIClientError as exc:
        raise _ai_error_to_http(exc)

    new_regen_count = increment_regen(session_id, user["clinic_id"])

    return JSONResponse({
        "session_id": session_id,
        "images": result.images,
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
    from image_generator import edit_image, get_quota_status, ImageQuotaExceeded
    from ai_client import AIClientError

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="이미지 세션을 찾을 수 없습니다.")
    if sess["clinic_id"] != user["clinic_id"]:
        raise HTTPException(status_code=403, detail="다른 한의원의 세션입니다.")

    plan_id = sess["plan_id_at_start"] or "free"
    regen_used = sess["regen_count"]
    edit_used = sess["edit_count"]

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
                    f"{exc.plan_id} 플랜 수정 무료 한도({exc.limit}회)에 도달했습니다. "
                    "초과 분은 정식 출시 후 종량제로 제공됩니다."
                ),
            },
        )
    except AIClientError as exc:
        raise _ai_error_to_http(exc)

    new_edit_count = increment_edit(session_id, user["clinic_id"])

    return JSONResponse({
        "session_id": session_id,
        "images": result.images,
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

