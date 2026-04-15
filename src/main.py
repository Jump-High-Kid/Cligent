"""
main.py — FastAPI 앱 진입점
엔드포인트:
  GET  /            → index.html (3단계 UI)
  POST /questions   → 주제에 맞는 질문 생성
  POST /generate    → 블로그 스트리밍 생성
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse

from config_loader import load_config
from question_generator import generate_questions
from blog_generator import generate_blog_stream

# 프로젝트 루트의 .env 파일에서 API 키 로드
ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

app = FastAPI(title="Cligent 블로그 생성기")


@app.get("/")
async def root():
    """메인 UI 페이지"""
    return FileResponse(ROOT / "templates" / "index.html")


@app.post("/questions")
async def get_questions(request: Request):
    """
    주제를 받아 Claude가 생성한 맞춤 질문 반환

    요청: {"keyword": "소화불량 한방 치료"}
    응답: {"questions": ["질문1", "질문2", "질문3"]}
    """
    body = await request.json()
    keyword = body.get("keyword", "").strip()

    if not keyword:
        return {"error": "주제를 입력해주세요."}

    # config.yaml에서 질문 단계 활성화 여부 확인
    config = load_config()
    if not config["flow"]["questions_enabled"]:
        return {"questions": []}  # 빈 배열 반환 → UI에서 바로 생성 단계로 이동

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": ".env 파일에 ANTHROPIC_API_KEY를 설정해주세요."}

    try:
        questions = generate_questions(keyword, api_key)
        return {"questions": questions}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/generate")
async def generate(request: Request):
    """
    주제 + Q&A 답변을 받아 블로그를 SSE 스트리밍으로 생성

    요청: {"keyword": "...", "answers": {"질문1": "답변1", ...}}
    응답: text/event-stream (SSE)
      - 생성 중: data: {"text": "..."}
      - 완료 시: data: {"done": true, "usage": {...}}
      - 오류 시: data: {"error": "..."}
    """
    body = await request.json()
    keyword = body.get("keyword", "").strip()
    answers = body.get("answers", {})

    if not keyword:
        # 오류도 SSE 형식으로 반환 (클라이언트가 동일하게 처리 가능)
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

    return StreamingResponse(
        generate_blog_stream(keyword, answers, api_key),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx 버퍼링 비활성화
        },
    )
