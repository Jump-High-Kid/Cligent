"""
src/routers/dashboard.py — 대시보드·도움말·피드백·공지(read) 라우터

라우트:
  HTML 페이지:
    GET  /dashboard                       대시보드 (인증 필수)
    GET  /help                            도움말 (인증 필수)
    GET  /announcements                   공지 목록 페이지
    GET  /announcements/{ann_id}          공지 상세 페이지
  피드백 API:
    POST /api/feedback                    피드백·오류 신고 저장 (개발자 전용 열람)
  공지 read API:
    GET  /api/announcements                       목록 (pinned 우선)
    GET  /api/announcements/unread-count          안 읽은 개수
    GET  /api/announcements/{ann_id}              상세
    POST /api/announcements/{ann_id}/read         읽음 처리

main.py 4,000줄 분할의 다섯 번째 라우터 (v0.9.0 / 2026-05-02).
auth.py · clinic.py · billing.py · blog.py 다음.

남은 admin 영역 라우트(/announcements/new, /announcements/{ann_id}/edit,
공지 작성/수정/삭제, upload-image)는 admin.py 분리 시 함께 이동.

피드백 헬퍼(_FEEDBACK_BATCH, _write_feedback_report, _persist_feedback)도
같이 이동. blog_chat_flow._save_blog_chat_feedback 가 routers.dashboard 에서
직접 import — fail-soft 정책 유지.
"""
from __future__ import annotations

import json as _json
import os
from datetime import datetime as _dt
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from auth_manager import COOKIE_NAME, get_current_user
from dependencies import NO_CACHE_HEADERS

# 프로젝트 루트 (src/routers/dashboard.py 기준 3단계 위)
ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# 피드백 — 헬퍼 + API
# ─────────────────────────────────────────────────────────────────

_FEEDBACK_BATCH = 5  # 이 개수마다 리포트 갱신


def _write_feedback_report() -> None:
    """feedback.jsonl 전체를 읽어 data/feedback_report.md 생성 (개발자 전용)."""
    log_path = ROOT / "data" / "feedback.jsonl"
    ack_path = ROOT / "data" / "feedback_ack.txt"
    rep_path = ROOT / "data" / "feedback_report.md"
    if not log_path.exists():
        return
    with open(log_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    total = len(lines)
    acked = int(ack_path.read_text().strip()) if ack_path.exists() else 0
    unread = lines[acked:]
    if not unread:
        return
    items = []
    for l in unread:
        try:
            items.append(_json.loads(l))
        except Exception:
            pass
    page_labels = {"blog": "블로그", "dashboard": "대시보드", "help": "도움말"}
    rows = "\n".join(
        f"- [{page_labels.get(i.get('page',''), i.get('page','?'))}] {i.get('ts','')} — {i.get('message','')}"
        for i in items
    )
    report = (
        f"# 피드백 리포트 (미확인 {len(unread)}건 / 전체 {total}건)\n\n"
        f"확인 후 `data/feedback_ack.txt`의 숫자를 {total}으로 변경하면 다음 리포트에서 제외됩니다.\n\n"
        f"## 미확인 피드백\n\n{rows}\n"
    )
    rep_path.write_text(report, encoding="utf-8")


def _persist_feedback(
    clinic_id: int,
    user_id: Optional[int],
    page: str,
    message: str,
    context: Optional[dict] = None,
    user_email: str = "",
) -> None:
    """피드백 1건을 DB(feedback) + jsonl(data/feedback.jsonl) 양쪽에 저장.

    /api/feedback 라우트와 blog_chat_flow의 FEEDBACK stage가 공용으로 사용.
    DB 실패는 RuntimeError로 호출자에게 위임 (라우트는 500, chat 흐름은 fail-soft).
    jsonl 기록·리포트 갱신은 fail-soft.
    """
    page = (page or "unknown").strip()[:100]
    message = (message or "").strip()
    if not message:
        raise ValueError("empty message")

    context_str: Optional[str] = None
    if isinstance(context, dict) and context:
        try:
            context_str = _json.dumps(context, ensure_ascii=False)[:4000]
        except (TypeError, ValueError):
            context_str = None

    now_str = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT INTO feedback (clinic_id, user_id, page, message, context_json) "
            "VALUES (?,?,?,?,?)",
            (clinic_id, user_id, page, message, context_str),
        )
        conn.commit()
    # jsonl 기록 + 배치 도달 시 리포트 갱신 (실패 흡수)
    try:
        log_path = ROOT / "data" / "feedback.jsonl"
        entry = _json.dumps({
            "ts": now_str, "page": page,
            "clinic_id": clinic_id,
            "user": user_email or "",
            "message": message,
            "context": context if isinstance(context, dict) else None,
        }, ensure_ascii=False)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        with open(log_path, encoding="utf-8") as f:
            total = sum(1 for l in f if l.strip())
        ack_path = ROOT / "data" / "feedback_ack.txt"
        acked = int(ack_path.read_text().strip()) if ack_path.exists() else 0
        if (total - acked) >= _FEEDBACK_BATCH:
            _write_feedback_report()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# 페이지 라우트
# ─────────────────────────────────────────────────────────────────


@router.get("/dashboard")
async def dashboard_page(request: Request):
    """대시보드 — app.html iframe 안에서 로드되는 직접 서빙 라우트.
    `/` 가 인증 시 `/app`으로 리다이렉트하므로, iframe 무한 재귀 방지를 위해 별도 경로 사용."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "dashboard.html", headers=NO_CACHE_HEADERS)


@router.get("/help")
async def help_page(request: Request):
    """도움말 페이지"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "help.html", headers=NO_CACHE_HEADERS)


# ─────────────────────────────────────────────────────────────────
# 피드백 API
# ─────────────────────────────────────────────────────────────────


@router.post("/api/feedback")
async def submit_feedback(request: Request, user: dict = Depends(get_current_user)):
    """피드백 / 오류 신고 저장 (개발자만 열람 — 사용자에게 미노출)

    Body: { message, page?, context?: dict }
    context는 blog_chat 등에서 발생 단계·session_id·error를 함께 전달 (선택).
    """
    body = await request.json()
    message = (body.get("message") or "").strip()
    page = (body.get("page") or "unknown").strip()[:100]
    if not message:
        return JSONResponse({"detail": "메시지를 입력해주세요."}, status_code=400)
    if len(message) > 2000:
        return JSONResponse({"detail": "2000자 이내로 입력해주세요."}, status_code=400)

    context_obj = body.get("context")
    context_dict = context_obj if isinstance(context_obj, dict) else None

    try:
        _persist_feedback(
            clinic_id=user["clinic_id"],
            user_id=user["id"],
            page=page,
            message=message,
            context=context_dict,
            user_email=user.get("email", ""),
        )
    except Exception as e:
        return JSONResponse({"detail": f"저장 실패: {e}"}, status_code=500)
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 공지사항 — read 라우트
# 작성/수정/삭제·upload-image 는 admin.py 로 분리 예정.
# ─────────────────────────────────────────────────────────────────


@router.get("/announcements")
async def announcements_page(request: Request):
    """공지사항 목록 페이지.

    iframe 안에서 401 raw 응답이 표시되지 않도록 token 직접 검사 후 RedirectResponse.
    Depends(get_current_user) 패턴은 HTTPException(401)을 raise하여
    iframe에 "로그인이 필요합니다" 텍스트만 표시되는 문제 발생 (2026-05-03 수정).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "announcements.html", headers=NO_CACHE_HEADERS)


@router.get("/announcements/{ann_id}")
async def announcement_detail_page(ann_id: int, request: Request):
    """공지 상세 페이지. /announcements 와 동일한 redirect 패턴."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "announcement_detail.html", headers=NO_CACHE_HEADERS)


@router.get("/api/announcements")
async def api_announcements_list(_user: dict = Depends(get_current_user)):
    """공지 목록 — pinned 우선, 그 다음 created_at desc."""
    from db_manager import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, title, category, is_pinned, author, created_at, updated_at "
            "FROM announcements "
            "ORDER BY is_pinned DESC, created_at DESC"
        ).fetchall()
    return JSONResponse({"announcements": [dict(r) for r in rows]})


@router.get("/api/announcements/unread-count")
async def api_announcements_unread_count(user: dict = Depends(get_current_user)):
    """안 읽은 공지 개수."""
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM announcements a "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM announcement_reads r "
            "  WHERE r.announcement_id = a.id AND r.user_id = ?"
            ")",
            (user["id"],),
        ).fetchone()
    return JSONResponse({"unread": int(row["cnt"]) if row else 0})


@router.get("/api/announcements/{ann_id}")
async def api_announcement_detail(ann_id: int, _user: dict = Depends(get_current_user)):
    """공지 상세."""
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, title, body_md, category, is_pinned, author, created_at, updated_at "
            "FROM announcements WHERE id = ?",
            (ann_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse(dict(row))


@router.post("/api/announcements/{ann_id}/read")
async def api_announcement_mark_read(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 읽음 처리."""
    from db_manager import get_db
    with get_db() as conn:
        # 존재 확인
        exists = conn.execute("SELECT 1 FROM announcements WHERE id = ?", (ann_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
        conn.execute(
            "INSERT OR IGNORE INTO announcement_reads (user_id, announcement_id) VALUES (?, ?)",
            (user["id"], ann_id),
        )
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 텔레메트리 — KPI 이벤트 기록 (Commit 4 / 2026-05-04)
# ─────────────────────────────────────────────────────────────────

@router.post("/api/telemetry/event")
async def api_telemetry_event(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """클라이언트가 stuck/cancel 등 KPI 이벤트를 fire-and-forget 으로 보고.

    payload 의 clinic_id 는 무시 (보안). 인증된 user.clinic_id 만 사용.
    실패해도 200 반환 — 텔레메트리는 본 흐름 차단 금지.
    """
    from telemetry import VALID_TELEMETRY_KINDS, record_event

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind not in VALID_TELEMETRY_KINDS:
        raise HTTPException(status_code=400, detail="kind 가 유효하지 않습니다.")

    record_event(
        kind=kind,
        clinic_id=user["clinic_id"],
        session_id=payload.get("session_id"),
        stage=payload.get("stage"),
        context=payload.get("context") if isinstance(payload.get("context"), dict) else None,
    )
    return JSONResponse({"ok": True})
