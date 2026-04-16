"""
main.py — FastAPI 앱 진입점
엔드포인트:
  GET  /                        → dashboard.html (대시보드)
  GET  /blog                    → index.html (블로그 생성기)
  GET  /api/modules/my          → 내 역할에 허용된 모듈 목록
  GET  /api/modules/info        → 전체 모듈 정보 (원장용 설정 화면)
  POST /api/modules/config      → 직원 모듈 권한 저장 (원장 전용)
  GET  /api/blog/stats          → 블로그 생성 통계 (대시보드 카드용)
  POST /conversation-flow       → 주제에 맞는 대화 흐름 생성 (질문+선택지)
  POST /generate                → 블로그 SSE 스트리밍 생성 (이력 자동 저장)
  POST /generate-image-prompts  → 이미지 프롬프트 SSE 스트리밍 생성
"""
import json as _json
import os
from pathlib import Path
from typing import Generator, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse

from config_loader import load_config
from conversation_flow import generate_conversation_flow
from blog_generator import generate_blog_stream
from image_prompt_generator import generate_image_prompts_stream
from module_manager import get_allowed_modules, get_module_info, save_staff_permissions
from blog_history import save_blog_entry, get_blog_stats

# 프로젝트 루트의 .env 파일에서 API 키 로드
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

app = FastAPI(title="Cligent")


@app.get("/")
async def root():
    """대시보드 메인 페이지"""
    return FileResponse(ROOT / "templates" / "dashboard.html")


@app.get("/blog")
async def blog():
    """블로그 생성기 페이지"""
    return FileResponse(ROOT / "templates" / "index.html")


# ── 모듈 권한 API ──────────────────────────────────────────────

@app.get("/api/modules/my")
async def my_modules(
    role: str = Query(default="owner", description="owner 또는 staff"),
    staff_id: Optional[str] = Query(default=None, description="직원 ID (role=staff 시 필요)")
):
    """
    현재 역할에 허용된 모듈 ID 목록 반환

    쿼리 파라미터:
      role     : "owner" | "staff" (기본값: owner)
      staff_id : 직원 ID (예: staff_001)

    응답: {"role": "staff", "modules": ["stats_patients", ...]}
    """
    allowed = get_allowed_modules(role=role, staff_id=staff_id)
    return JSONResponse({"role": role, "modules": allowed})


@app.get("/api/modules/info")
async def modules_info():
    """
    전체 모듈 정보 반환 (원장 설정 화면용)
    응답: {"stats_patients": {"name": "환자 수", "default_roles": [...]}, ...}
    """
    return JSONResponse(get_module_info())


@app.post("/api/modules/config")
async def save_module_config(request: Request):
    """
    직원 모듈 권한 저장 (원장 전용)

    요청: {"staff_id": "staff_001", "name": "이간호사", "modules": ["stats_patients", ...]}
    응답: {"ok": true, "staff_id": "staff_001", "modules": [...]}
    """
    body = await request.json()
    staff_id = body.get("staff_id", "").strip()
    name = body.get("name", "").strip()
    modules = body.get("modules", [])

    if not staff_id or not name:
        return JSONResponse({"error": "staff_id와 name은 필수입니다."}, status_code=400)

    result = save_staff_permissions(staff_id, name, modules)
    return JSONResponse({"ok": True, "staff_id": staff_id, **result})


@app.get("/api/blog/stats")
async def blog_stats():
    """
    블로그 생성 통계 반환 (대시보드 카드용)

    응답: {
      "total": 전체 생성 수,
      "this_month": 이번 달 생성 수,
      "recent_keywords": [최근 3개 주제],
      "last_created_at": "2026-04-16T14:30:00" | null
    }
    """
    return JSONResponse(get_blog_stats())


# ── 래퍼: 블로그 생성 완료 시 이력 자동 저장 ──────────────────────

def _stream_and_save(
    base_gen: Generator, keyword: str, tone: str
) -> Generator:
    """
    SSE 스트림을 통과시키면서 done 이벤트 감지 시 이력 저장

    원본 스트림을 그대로 yield하므로 프론트엔드 동작에 영향 없음
    """
    collected_text: list = []

    for chunk in base_gen:
        yield chunk
        raw = chunk.removeprefix("data: ").strip()
        try:
            data = _json.loads(raw)
            if "text" in data:
                collected_text.append(data["text"])
            elif data.get("done"):
                char_count = len("".join(collected_text))
                cost_krw = data.get("usage", {}).get("cost_krw", 0)
                save_blog_entry(keyword, tone, char_count, cost_krw)
        except Exception:
            pass  # 파싱 실패는 무시 — 스트림 중단 없음


@app.post("/conversation-flow")
async def get_conversation_flow(request: Request):
    """
    주제를 받아 Claude가 생성한 대화형 설정 흐름 반환

    요청: {"keyword": "소화불량 한방 치료"}
    응답: {"questions": [{"id": "tone", "message": "...", "options": [...]}, ...]}
    """
    body = await request.json()
    keyword = body.get("keyword", "").strip()

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    config = load_config()
    if not config["flow"].get("questions_enabled", True):
        return {"questions": []}  # 빈 배열 → 바로 생성 단계로

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": ".env 파일에 ANTHROPIC_API_KEY를 설정해주세요."}

    try:
        questions = generate_conversation_flow(keyword, api_key)
        return {"questions": questions}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/generate")
async def generate(request: Request):
    """
    주제 + Q&A 답변을 받아 블로그를 SSE 스트리밍으로 생성

    요청: {
      "keyword": "...",
      "answers": {"tone": "친근한", "audience": "만성 환자", ...}
    }
    응답: text/event-stream (SSE)
      - 생성 중: data: {"text": "..."}
      - 완료 시: data: {"done": true, "usage": {...}}
      - 오류 시: data: {"error": "..."}
    """
    body = await request.json()
    keyword   = body.get("keyword", "").strip()
    answers   = body.get("answers", {})
    materials = body.get("materials", {})

    if not keyword:
        async def error_stream():
            import json
            yield f"data: {json.dumps({'error': '주제를 입력해주세요.'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def error_stream():
            import json
            yield f"data: {json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    tone = answers.get("tone", "전문적") if answers else "전문적"
    return StreamingResponse(
        # 래퍼로 감싸서 done 이벤트 시 이력 자동 저장
        _stream_and_save(
            generate_blog_stream(keyword, answers, api_key, materials),
            keyword=keyword,
            tone=tone,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/generate-image-prompts")
async def generate_image_prompts(request: Request):
    """
    블로그 본문을 받아 이미지 프롬프트 5개를 SSE 스트리밍으로 생성

    요청: {
      "keyword": "소화불량 한방 치료",
      "blog_content": "생성된 블로그 본문 (마크다운)"
    }
    응답: text/event-stream (SSE)
      - 생성 중: data: {"text": "..."}
      - 완료 시: data: {"done": true, "usage": {...}}
      - 오류 시: data: {"error": "..."}
    """
    body = await request.json()
    keyword     = body.get("keyword", "").strip()
    blog_content = body.get("blog_content", "").strip()

    if not keyword or not blog_content:
        async def error_stream():
            import json
            yield f"data: {json.dumps({'error': '주제와 블로그 본문이 필요합니다.'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        async def error_stream():
            import json
            yield f"data: {json.dumps({'error': '.env 파일에 ANTHROPIC_API_KEY를 설정해주세요.'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    return StreamingResponse(
        generate_image_prompts_stream(keyword, blog_content, api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
