"""
src/routers/admin.py — 어드민 페이지·API + 공지 작성·OpenAI 키 라우터

라우트:
  HTML 페이지 (8):
    GET  /admin                 어드민 인덱스
    GET  /admin/clinics         한의원 관리
    GET  /admin/usage           사용량
    GET  /admin/feedback        피드백 (개발자 전용)
    GET  /admin/login-history   로그인 이력
    GET  /admin/errors          에러 로그
    GET  /admin/blogs           전체 블로그
    GET  /admin/applicants      베타 신청자 관리
    GET  /admin/settings        시스템 설정 (Naver 등)

  공지 작성 페이지 (admin only — dashboard 의 /announcements/{ann_id} 보다 먼저 등록):
    GET  /announcements/new
    GET  /announcements/{ann_id}/edit

  어드민 API:
    POST /api/admin/daily-report
    POST /api/admin/clinic
    GET  /api/admin/clinics
    PATCH /api/admin/clinic/{clinic_id}
    GET  /api/admin/login-history
    GET  /api/admin/blogs
    GET  /api/admin/errors/dates
    GET  /api/admin/errors/summary
    GET  /api/admin/errors
    GET  /api/admin/usage
    GET  /api/admin/feedback
    POST /api/admin/feedback/{fid}/viewed
    POST /api/admin/feedback/{fid}/unview
    GET  /api/admin/naver-config
    POST /api/admin/naver-config
    GET  /api/admin/applicants
    GET  /api/admin/applicants/{applicant_id}/emails
    PATCH /api/admin/applicants/{applicant_id}
    POST /api/admin/applicants/{applicant_id}/reject
    POST /api/admin/applicants/{applicant_id}/resend
    POST /api/admin/invite-batch

  공지 작성/수정/삭제 API (admin only):
    POST   /api/announcements
    PATCH  /api/announcements/{ann_id}
    DELETE /api/announcements/{ann_id}
    POST   /api/announcements/upload-image

  OpenAI 키 (admin only):
    GET    /api/admin/openai-key
    POST   /api/admin/openai-key
    DELETE /api/admin/openai-key

main.py 4,000줄 분할의 여섯 번째(마지막) 라우터 (v0.9.0 / 2026-05-02).
auth.py · clinic.py · billing.py · blog.py · dashboard.py 다음.

main.py 의 backward-compat alias (_get_fernet / _encrypt_key / _decrypt_key /
_mask_key) 는 tests/test_onboarding monkeypatch 호환을 위해 main.py 에 잔존.
admin OpenAI 키 라우트는 secret_manager 를 직접 호출 (자체 Fernet 사용).

라우트 등록 순서: main.py 에서 dashboard 라우터 include 보다 먼저 admin 라우터를
include 해야 /announcements/new 가 dashboard /announcements/{ann_id} 보다 먼저
매칭된다.
"""
from __future__ import annotations

import asyncio
import hmac
import json as _json
import logging as _logging
import os
import re as _re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from auth_manager import COOKIE_NAME, create_invite, decode_token, get_current_user
from db_manager import create_clinic, get_db as _get_db
from dependencies import (
    require_admin_or_session as _require_admin_or_session,
    require_announce_admin as _require_announce_admin,
)

# 프로젝트 루트 (src/routers/admin.py 기준 3단계 위)
ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()

_error_logger = _logging.getLogger("cligent.errors")

# error_logs 디렉토리 (observability.py와 동일 위치)
_ERROR_LOG_DIR = ROOT / "data" / "error_logs"
_DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 공지 첨부 이미지 정책
_ANNOUNCE_CATEGORIES = {"update", "maintenance", "general"}
_ANNOUNCE_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_ANNOUNCE_MAX_UPLOAD = 5 * 1024 * 1024  # 5MB


# ─────────────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────────────

def _read_error_log_file(date_str: str) -> list:
    """단일 일자 jsonl 파일 읽기 — 손상된 줄은 skip."""
    if not _DATE_RE.match(date_str):
        return []
    path = _ERROR_LOG_DIR / f"{date_str}.jsonl"
    if not path.exists():
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(_json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def _resolve_user_id_from_session(request: Request) -> Optional[int]:
    """세션 쿠키에서 user_id 추출 (감사 로그용). 실패 시 None."""
    try:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        payload = decode_token(token)
        if not payload:
            return None
        uid = payload.get("user_id") or payload.get("sub")
        if isinstance(uid, str) and uid.isdigit():
            return int(uid)
        if isinstance(uid, int):
            return uid
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────
# 어드민 페이지 (HTML)
# ─────────────────────────────────────────────────────────────────

@router.get("/admin")
async def admin_index_page(request: Request):
    """어드민 메인 — 하위 페이지 카드 인덱스. 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_index.html")


@router.get("/admin/clinics")
async def admin_clinics_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_clinics.html")


@router.get("/admin/usage")
async def admin_usage_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_usage.html")


@router.get("/admin/feedback")
async def admin_feedback_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_feedback.html")


@router.get("/admin/login-history")
async def admin_login_history_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_login_history.html")


@router.get("/admin/errors")
async def admin_errors_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_errors.html")


@router.get("/admin/blogs")
async def admin_blogs_page(request: Request):
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_blogs.html")


@router.get("/admin/settings")
async def admin_settings_page(request: Request):
    """어드민 시스템 설정 페이지 (네이버 API). 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_settings.html")


@router.get("/admin/applicants")
async def admin_applicants_page(request: Request):
    """어드민 신청자 관리 페이지. 세션 + is_admin 필요."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_applicants.html")


@router.get("/admin/image-test")
async def admin_image_test_page(request: Request):
    """어드민 이미지 생성 테스트 페이지. 베타 한도 미반영, 어드민 전용."""
    _require_admin_or_session(request)
    return FileResponse(ROOT / "templates" / "admin_image_test.html")


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 이미지 생성 테스트 (해부학·자유 프롬프트 검증용)
# 베타 한도 미반영. 본인 클리닉(=ADMIN_CLINIC_ID) 글만 노출.
# ─────────────────────────────────────────────────────────────────

# 의미 없는 테스트 글 자동 제외 패턴
_TEST_TITLE_PATTERNS = _re.compile(r"(test|테스트|tst|asdf|ㅁㄴㅇ|qwer)", _re.IGNORECASE)
_MIN_CHARS_FOR_REAL_POST = 500


def _is_spam_title(title: str) -> bool:
    """반복 문자 spam 감지: 같은 글자 6회 이상 또는 단일 글자 비율 70% 초과."""
    if not title or len(title) < 3:
        return True
    # 같은 글자 6회 이상 연속 (예: "가가가가가가...")
    if _re.search(r"(.)\1{5,}", title):
        return True
    # 한 글자가 70% 이상 차지 (반복+공백 등)
    most_common = max(title.count(c) for c in set(title))
    if most_common / len(title) > 0.7:
        return True
    return False


def _resolve_admin_clinic_id(request: Request) -> int:
    """세션 또는 Bearer 사용자의 clinic_id. Bearer는 ADMIN_CLINIC_ID 사용."""
    from dependencies import _admin_clinic_id  # type: ignore
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            payload = decode_token(token)
            user_id = int(payload.get("sub", 0))
            with _get_db() as conn:
                row = conn.execute(
                    "SELECT clinic_id FROM users WHERE id = ? AND is_active = 1",
                    (user_id,),
                ).fetchone()
            if row:
                return int(row["clinic_id"])
        except Exception:
            pass
    cid = _admin_clinic_id()
    if cid is None:
        raise HTTPException(status_code=400, detail="ADMIN_CLINIC_ID 미설정")
    return cid


@router.get("/api/admin/image-test/anatomy-slugs")
def api_admin_image_test_slugs(request: Request):
    """anatomical_region 드롭다운용 30 부위 슬러그 + 한글명."""
    _require_admin_or_session(request)
    slugs_path = ROOT / "data" / "anatomy" / "_SLUGS.json"
    if not slugs_path.exists():
        return JSONResponse({"categories": {}, "parts": {}})
    try:
        return JSONResponse(_json.loads(slugs_path.read_text(encoding="utf-8")))
    except Exception:
        return JSONResponse({"categories": {}, "parts": {}})


@router.get("/api/admin/image-test/posts")
def api_admin_image_test_posts(request: Request, page: int = 1, per_page: int = 50):
    """어드민 본인 클리닉 글 목록 (테스트성 글 자동 제외)."""
    _require_admin_or_session(request)
    clinic_id = _resolve_admin_clinic_id(request)

    from blog_history import get_history_list
    raw = get_history_list(clinic_id, page=page, per_page=per_page)
    items = []
    for it in raw.get("items", []):
        char_count = it.get("char_count", 0)
        title = it.get("title") or it.get("keyword") or ""
        keyword = it.get("keyword") or ""
        if char_count < _MIN_CHARS_FOR_REAL_POST:
            continue
        if _TEST_TITLE_PATTERNS.search(title) or _TEST_TITLE_PATTERNS.search(keyword):
            continue
        if _is_spam_title(title) or _is_spam_title(keyword):
            continue
        if not it.get("has_text"):
            # 본문 만료된 글은 프롬프트 베이스로 못 씀
            continue
        items.append({
            "id": it["id"],
            "title": title,
            "keyword": keyword,
            "char_count": char_count,
            "created_at": it.get("created_at"),
        })
    return JSONResponse({"items": items})


@router.get("/api/admin/image-test/post/{entry_id}")
def api_admin_image_test_post_detail(entry_id: int, request: Request):
    """본문 전문 반환 (구간 드래그용)."""
    _require_admin_or_session(request)
    clinic_id = _resolve_admin_clinic_id(request)

    from blog_history import get_blog_text
    text = get_blog_text(entry_id, clinic_id)
    if text is None:
        raise HTTPException(status_code=404, detail="본문이 만료됐거나 권한 없음")
    return JSONResponse({"id": entry_id, "blog_text": text})


@router.post("/api/admin/image-test/generate")
async def api_admin_image_test_generate(request: Request):
    """이미지 생성 테스트.

    body: {
        mode: 'pipeline' | 'raw',
        base_text: str,           # pipeline 모드 본문
        keyword: str,             # pipeline 모드 키워드 (해부학 힌트 라벨)
        raw_prompt: str,          # raw 모드 영문 프롬프트
        force_region: str|None,   # pipeline 모드에서 anatomical_region 강제
        anatomy_hint: bool,       # base_text 가 해부학 핵심 부분이라 가정
        count: 1 | 5,
        parallel: bool,           # 5장에서만 의미
        plan: 'standard' | 'pro',
        global_directives: bool,  # raw 모드에서 build_global_directives prepend
    }
    응답: {
        images: [{b64, prompt, module, region}],
        usage: {input, output, image_count},
        elapsed: float,
        analysis: dict | None,
    }
    """
    import time
    _require_admin_or_session(request)

    body = await request.json()
    mode = body.get("mode", "pipeline")
    count = int(body.get("count", 1))
    if count not in (1, 5):
        raise HTTPException(status_code=400, detail="count 는 1 또는 5")
    parallel = bool(body.get("parallel", False))
    plan = body.get("plan", "standard")
    if plan not in ("standard", "pro"):
        plan = "standard"

    from image_generator import (
        ImageQuotaExceeded, get_plan_dimensions, normalize_plan_id,
    )
    from ai_client import call_openai_image_generate, AIClientError

    started = time.time()
    images_out: list[dict] = []
    total_usage = {"input": 0, "output": 0, "image_count": 0}
    analysis_out: Optional[dict] = None

    try:
        if mode == "raw":
            raw_prompt = (body.get("raw_prompt") or "").strip()
            if not raw_prompt:
                raise HTTPException(status_code=400, detail="raw_prompt 비어 있음")
            if body.get("global_directives", True):
                from image_modules import build_global_directives
                raw_prompt = f"{raw_prompt}\n\n{build_global_directives()}"

            size, quality = get_plan_dimensions(plan)

            if count == 1:
                resps = call_openai_image_generate(prompt=raw_prompt, size=size, quality=quality, n=1)
                for r in resps:
                    images_out.append({"b64": r.content, "prompt": raw_prompt, "module": None, "region": None})
            else:
                if parallel:
                    resps = call_openai_image_generate(prompt=raw_prompt, size=size, quality=quality, n=5)
                    for r in resps:
                        images_out.append({"b64": r.content, "prompt": raw_prompt, "module": None, "region": None})
                else:
                    for _ in range(5):
                        rs = call_openai_image_generate(prompt=raw_prompt, size=size, quality=quality, n=1)
                        if rs:
                            images_out.append({"b64": rs[0].content, "prompt": raw_prompt, "module": None, "region": None})
            total_usage["image_count"] = len(images_out)

        else:  # pipeline
            base_text = (body.get("base_text") or "").strip()
            keyword = (body.get("keyword") or "테스트").strip()
            if not base_text:
                raise HTTPException(status_code=400, detail="base_text 비어 있음")

            anatomy_hint = bool(body.get("anatomy_hint", False))
            force_region = body.get("force_region") or None

            anth_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not anth_key:
                raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY 미설정")

            from image_prompt_generator import _analyze_blog, _generate_prompts

            base_for_analysis = base_text
            if anatomy_hint:
                base_for_analysis = (
                    "(이 본문은 글 전체가 아니라 해부학 설명 핵심 부분만 추출한 것입니다. "
                    "이 부분의 해부학적 묘사를 이미지 장면으로 변환하세요.)\n\n" + base_text
                )

            analysis, usage1 = _analyze_blog(keyword, base_for_analysis, anth_key)

            # force_region 적용 — 모든 scene 의 anatomical_region 강제 덮어쓰기
            if force_region and isinstance(analysis, dict):
                scenes = analysis.get("scenes") or []
                for s in scenes:
                    if isinstance(s, dict):
                        s["anatomical_region"] = force_region

            # count 만큼 scene 잘라내기 (5 scene 분석 결과에서 앞 N개 사용)
            if isinstance(analysis, dict):
                analysis_for_count = dict(analysis)
                scenes = analysis_for_count.get("scenes") or []
                analysis_for_count["scenes"] = scenes[:count]
            else:
                analysis_for_count = analysis

            prompts_result, usage2 = _generate_prompts(analysis_for_count, anth_key)
            raw_prompts = prompts_result.get("prompts", []) if isinstance(prompts_result, dict) else []
            raw_prompts = raw_prompts[:count]

            # blog_chat_flow.py 와 동일 — dict 항목은 prompt + negative_prompt 합치기
            inject_negatives = os.getenv("IMAGE_INJECT_NEGATIVES", "1").strip() != "0"
            prompts_list: list[str] = []
            for p in raw_prompts:
                if isinstance(p, str):
                    prompts_list.append(p)
                elif isinstance(p, dict):
                    body = (p.get("prompt") or "").strip()
                    neg = (p.get("negative_prompt") or "").strip()
                    if inject_negatives and neg:
                        body = body.rstrip() + f"\n\nNegative aspects to avoid: {neg}"
                    if body:
                        prompts_list.append(body)

            if not prompts_list:
                raise HTTPException(status_code=502, detail="프롬프트 생성 결과 비어 있음")

            total_usage["input"] = usage1.get("input", 0) + usage2.get("input", 0)
            total_usage["output"] = usage1.get("output", 0) + usage2.get("output", 0)
            analysis_out = analysis_for_count

            scenes_meta = (analysis_for_count.get("scenes") if isinstance(analysis_for_count, dict) else []) or []
            size, quality = get_plan_dimensions(plan)

            def _meta_for(idx: int) -> tuple[Optional[int], Optional[str]]:
                scene = scenes_meta[idx] if idx < len(scenes_meta) and isinstance(scenes_meta[idx], dict) else {}
                return scene.get("module"), scene.get("anatomical_region")

            if count == 1:
                p = prompts_list[0]
                rs = call_openai_image_generate(prompt=p, size=size, quality=quality, n=1)
                if rs:
                    m, r = _meta_for(0)
                    images_out.append({"b64": rs[0].content, "prompt": p, "module": m, "region": r})
            else:
                # 5 prompt 다중 호출
                if parallel:
                    # 동시 5개 — semaphore 는 ai_client 내부
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                        futures = [
                            ex.submit(call_openai_image_generate, p, size, quality, 1)
                            for p in prompts_list
                        ]
                        for idx, fut in enumerate(futures):
                            rs = fut.result()
                            if rs:
                                m, r = _meta_for(idx)
                                images_out.append({"b64": rs[0].content, "prompt": prompts_list[idx], "module": m, "region": r})
                else:
                    for idx, p in enumerate(prompts_list):
                        rs = call_openai_image_generate(prompt=p, size=size, quality=quality, n=1)
                        if rs:
                            m, r = _meta_for(idx)
                            images_out.append({"b64": rs[0].content, "prompt": p, "module": m, "region": r})
            total_usage["image_count"] = len(images_out)

    except HTTPException:
        raise
    except AIClientError as e:
        raise HTTPException(status_code=502, detail=f"AI 호출 실패: {e}")
    except ImageQuotaExceeded as e:
        raise HTTPException(status_code=429, detail=f"한도 초과: {e}")
    except Exception as e:
        _error_logger.exception("admin image-test generate 실패")
        raise HTTPException(status_code=500, detail=f"내부 오류: {type(e).__name__}: {e}")

    elapsed = round(time.time() - started, 2)
    size_used, quality_used = get_plan_dimensions(plan)
    return JSONResponse({
        "images": images_out,
        "usage": total_usage,
        "elapsed": elapsed,
        "plan": normalize_plan_id(plan),
        "size": size_used,
        "quality": quality_used,
        "analysis": analysis_out,
    })


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 데일리 리포트 / 클리닉 생성
# ─────────────────────────────────────────────────────────────────

@router.post("/api/admin/daily-report")
async def admin_daily_report(request: Request):
    """
    관리자 전용 — 데일리 리포트 즉시 생성.
    인증: 세션(chief_director + ADMIN_CLINIC_ID) 또는 ADMIN_SECRET Bearer.
    body(선택): { "date": "YYYY-MM-DD" } — 생략 시 오늘 날짜.
    """
    _require_admin_or_session(request)

    try:
        body = await request.json()
        date_str = (body.get("date") or "").strip()
    except Exception:
        date_str = ""

    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if not _DATE_RE.match(date_str):
        return JSONResponse({"detail": "date 형식: YYYY-MM-DD"}, status_code=400)

    try:
        from daily_report import generate_daily_report
        out_path = await asyncio.to_thread(generate_daily_report, date_str)
        return JSONResponse({"ok": True, "date": date_str, "path": str(out_path)})
    except Exception as e:
        return JSONResponse({"detail": f"리포트 생성 실패: {e}"}, status_code=500)


@router.post("/api/admin/clinic")
async def admin_create_clinic(request: Request):
    """
    관리자 전용 — 신규 한의원 생성 (trial_expires_at 자동 설정).

    인증: Authorization: Bearer <ADMIN_SECRET> 헤더.
    ADMIN_SECRET 환경 변수 미설정 시 비활성화.

    요청 예시:
        { "name": "강남 한의원", "max_slots": 5 }

    응답 예시:
        { "clinic_id": 3, "trial_expires_at": "2026-05-06T00:00:00+00:00" }
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret:
        return JSONResponse({"detail": "관리자 기능이 비활성화되어 있습니다."}, status_code=403)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or not hmac.compare_digest(auth_header[7:], admin_secret):
        return JSONResponse({"detail": "인증 실패"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"detail": "name 필드가 필요합니다."}, status_code=400)

    max_slots = int(body.get("max_slots", 5))

    trial_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    clinic_id = create_clinic(name, max_slots)
    return JSONResponse({"clinic_id": clinic_id, "trial_expires_at": trial_expires_at})


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 로그인 이력
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/login-history")
async def api_admin_login_history(request: Request):
    """
    로그인 이력 조회 + 의심 IP 감지.
    쿼리: email=, user_id=, ip=, success=(0|1), days=(기본 7), limit=(기본 200, 최대 1000).
    의심 IP 기준: 1시간 내 같은 IP에서 5회 이상 실패.
    """
    _require_admin_or_session(request)

    qp = request.query_params
    email      = (qp.get("email") or "").strip().lower() or None
    user_id    = qp.get("user_id")
    ip_filter  = (qp.get("ip") or "").strip() or None
    success_q  = qp.get("success")
    try:
        days = max(1, min(int(qp.get("days") or "7"), 90))
    except ValueError:
        days = 7
    try:
        limit = max(1, min(int(qp.get("limit") or "200"), 1000))
    except ValueError:
        limit = 200

    # cutoff는 SQLite native datetime 함수로 — created_at 문자열 형식 차이(공백 vs T) 회피
    days_modifier = f"-{days} days"
    sql = (
        "SELECT id, user_id, email, clinic_id, ip, user_agent, success, failure_reason, created_at "
        "FROM login_history "
        "WHERE datetime(created_at) >= datetime('now', ?) "
    )
    params: list = [days_modifier]
    if email:
        sql += "AND lower(email) = ? "
        params.append(email)
    if user_id and user_id.isdigit():
        sql += "AND user_id = ? "
        params.append(int(user_id))
    if ip_filter:
        sql += "AND ip = ? "
        params.append(ip_filter)
    if success_q in ("0", "1"):
        sql += "AND success = ? "
        params.append(int(success_q))
    sql += "ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with _get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        stats = conn.execute(
            """
            SELECT
              COUNT(*)                                              AS total,
              SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END)           AS ok,
              SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END)           AS fail
            FROM login_history
            WHERE datetime(created_at) >= datetime('now', ?)
            """,
            (days_modifier,),
        ).fetchone()
        # 의심 IP: 최근 1시간 내 같은 IP에서 5회 이상 실패
        suspicious = conn.execute(
            """
            SELECT ip, COUNT(*) AS fails
            FROM login_history
            WHERE success = 0 AND ip IS NOT NULL
              AND datetime(created_at) >= datetime('now', '-1 hour')
            GROUP BY ip
            HAVING COUNT(*) >= 5
            ORDER BY fails DESC
            LIMIT 20
            """
        ).fetchall()

    return JSONResponse({
        "rows": [dict(r) for r in rows],
        "stats": dict(stats) if stats else {},
        "suspicious_ips": [dict(r) for r in suspicious],
        "days": days,
        "limit": limit,
    })


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 블로그 통합 조회
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/blogs")
async def api_admin_blogs(request: Request):
    """전체 클리닉의 블로그 통합 조회 + 발행 상태 통합.
    쿼리: clinic_id, q(keyword/title 부분일치), publish_status(none/pending/found/missing),
          date_from, date_to, page=1, per_page=50(최대 200)."""
    _require_admin_or_session(request)
    qp = request.query_params
    clinic_id_q = qp.get("clinic_id")
    q           = (qp.get("q") or "").strip().lower() or None
    pub_status  = (qp.get("publish_status") or "").strip() or None
    date_from   = (qp.get("date_from") or "").strip() or None
    date_to     = (qp.get("date_to") or "").strip() or None
    try:
        page = max(1, int(qp.get("page") or "1"))
    except ValueError:
        page = 1
    try:
        per_page = max(1, min(int(qp.get("per_page") or "50"), 200))
    except ValueError:
        per_page = 50

    # 1) blog_stats 로드
    from blog_history import _load_json as _bh_load_json, STATS_PATH as _STATS_PATH
    stats: list = _bh_load_json(_STATS_PATH, default=[])

    # 2) 클리닉명 매핑
    clinic_map: dict = {}
    with _get_db() as conn:
        for row in conn.execute("SELECT id, name FROM clinics").fetchall():
            clinic_map[int(row["id"])] = row["name"]

    # 3) 발행 상태 — pending_checks.json
    from naver_checker import _load as _load_pending
    pending_items = _load_pending()
    publish_map: dict = {}
    for it in pending_items:
        publish_map[int(it.get("blog_stat_id", 0))] = {
            "status": it.get("status"),
            "found_url": it.get("found_url"),
            "started_at": it.get("started_at"),
            "check_count": it.get("check_count", 0),
        }

    # 4) 통합 + 필터
    out = []
    for e in stats:
        cid = e.get("clinic_id")
        cname = clinic_map.get(int(cid)) if cid else None
        # 발행 상태 종합
        pub = publish_map.get(int(e.get("id", -1)))
        if e.get("naver_url"):
            ps = "found"
        elif pub and pub.get("status") == "found":
            ps = "found"
        elif pub and pub.get("status") == "missing":
            ps = "missing"
        elif pub:
            ps = "pending"
        else:
            ps = "none"

        # 필터
        if clinic_id_q and clinic_id_q.isdigit() and (cid != int(clinic_id_q)):
            continue
        if pub_status and pub_status != ps:
            continue
        if date_from and (e.get("created_at") or "")[:10] < date_from:
            continue
        if date_to and (e.get("created_at") or "")[:10] > date_to:
            continue
        if q:
            blob = (
                (e.get("keyword") or "") + " " +
                (e.get("title") or "")
            ).lower()
            if q not in blob:
                continue

        out.append({
            "id": e.get("id"),
            "clinic_id": cid,
            "clinic_name": cname or "—",
            "keyword": e.get("keyword"),
            "title": e.get("title"),
            "tone": e.get("tone"),
            "char_count": e.get("char_count", 0),
            "cost_krw": e.get("cost_krw", 0),
            "naver_url": e.get("naver_url") or (pub.get("found_url") if pub else None),
            "publish_status": ps,
            "publish_check_count": pub.get("check_count") if pub else 0,
            "created_at": e.get("created_at"),
        })

    # 최신순
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    total = len(out)
    start = (page - 1) * per_page
    items = out[start: start + per_page]

    # 통계 (필터 적용 결과 기준)
    by_status = {"none": 0, "pending": 0, "found": 0, "missing": 0}
    by_clinic: dict = {}
    for r in out:
        by_status[r["publish_status"]] = by_status.get(r["publish_status"], 0) + 1
        cn = r["clinic_name"]
        by_clinic[cn] = by_clinic.get(cn, 0) + 1
    top_clinics = sorted(by_clinic.items(), key=lambda x: -x[1])[:10]

    return JSONResponse({
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "stats": {
            "by_status": by_status,
            "top_clinics": [{"name": n, "count": c} for n, c in top_clinics],
        },
        "clinics": [{"id": k, "name": v} for k, v in sorted(clinic_map.items())],
    })


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 에러 로그
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/errors/dates")
async def api_admin_error_dates(request: Request):
    """가용 일자 목록 (최신부터). 사이드바 날짜 픽커용."""
    _require_admin_or_session(request)
    if not _ERROR_LOG_DIR.exists():
        return JSONResponse({"dates": []})
    dates = []
    for p in _ERROR_LOG_DIR.glob("*.jsonl"):
        if _DATE_RE.match(p.stem):
            try:
                size = p.stat().st_size
            except Exception:
                size = 0
            dates.append({"date": p.stem, "size": size})
    dates.sort(key=lambda x: x["date"], reverse=True)
    return JSONResponse({"dates": dates})


@router.get("/api/admin/errors/summary")
async def api_admin_error_summary(request: Request):
    """최근 N일 통계: 일자별 카운트, top error_type, top path."""
    _require_admin_or_session(request)
    try:
        days = max(1, min(int(request.query_params.get("days") or "7"), 90))
    except ValueError:
        days = 7

    today = datetime.now(timezone.utc).date()
    daily_counts = []
    error_types: dict = {}
    paths: dict = {}
    total = 0

    for d in range(days):
        date = today - timedelta(days=d)
        rows = _read_error_log_file(date.isoformat())
        cnt = len(rows)
        daily_counts.append({"date": date.isoformat(), "count": cnt})
        total += cnt
        for row in rows:
            et = row.get("error_type") or "Unknown"
            error_types[et] = error_types.get(et, 0) + 1
            p = row.get("path") or "/"
            paths[p] = paths.get(p, 0) + 1

    top_types = sorted(error_types.items(), key=lambda x: -x[1])[:10]
    top_paths = sorted(paths.items(), key=lambda x: -x[1])[:10]

    return JSONResponse({
        "days": days,
        "total": total,
        "daily_counts": list(reversed(daily_counts)),  # 오래된 → 최신 (차트용)
        "top_error_types": [{"type": t, "count": c} for t, c in top_types],
        "top_paths":       [{"path": p, "count": c} for p, c in top_paths],
    })


@router.get("/api/admin/errors")
async def api_admin_errors(request: Request):
    """일자별 에러 로그 + 필터.
    쿼리: date=YYYY-MM-DD(기본 오늘), status, error_type, path_q, limit(기본 200, 최대 1000)."""
    _require_admin_or_session(request)
    qp = request.query_params
    date_str = (qp.get("date") or "").strip() or datetime.now(timezone.utc).date().isoformat()
    if not _DATE_RE.match(date_str):
        return JSONResponse({"detail": "date 형식: YYYY-MM-DD"}, status_code=400)
    status_q = qp.get("status")
    error_type = (qp.get("error_type") or "").strip() or None
    path_q = (qp.get("path_q") or "").strip().lower() or None
    try:
        limit = max(1, min(int(qp.get("limit") or "200"), 1000))
    except ValueError:
        limit = 200

    rows = _read_error_log_file(date_str)

    # 필터
    out = []
    for row in rows:
        if status_q and str(row.get("status")) != status_q:
            continue
        if error_type and row.get("error_type") != error_type:
            continue
        if path_q and path_q not in (row.get("path") or "").lower():
            continue
        out.append(row)

    # 최신부터, limit 적용
    out.reverse()
    truncated = len(out) > limit
    out = out[:limit]

    return JSONResponse({
        "date": date_str,
        "rows": out,
        "total_in_file": len(rows),
        "matched": sum(1 for r in rows if (
            (not status_q or str(r.get("status")) == status_q) and
            (not error_type or r.get("error_type") == error_type) and
            (not path_q or path_q in (r.get("path") or "").lower())
        )),
        "truncated": truncated,
        "limit": limit,
    })


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 클리닉 / 사용량 / 피드백
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/clinics")
async def api_admin_clinics(request: Request):
    """전체 클리닉 목록 + 핵심 메타. 세션 또는 ADMIN_SECRET Bearer."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.created_at, c.plan_id, c.plan_expires_at,
                   c.trial_expires_at, c.api_key_configured, c.first_blog_at,
                   c.is_admin_clinic, c.naver_blog_id,
                   (SELECT COUNT(*) FROM usage_logs u
                      WHERE u.clinic_id = c.id AND u.feature = 'blog_generation'
                        AND u.used_at >= datetime('now','start of month')) AS blog_this_month,
                   (SELECT COUNT(*) FROM usage_logs u WHERE u.clinic_id = c.id) AS usage_total,
                   (SELECT MAX(used_at) FROM usage_logs u WHERE u.clinic_id = c.id) AS last_seen,
                   (SELECT COUNT(*) FROM users WHERE clinic_id = c.id AND is_active = 1) AS active_users
            FROM clinics c
            ORDER BY c.created_at DESC
            """
        ).fetchall()
    return JSONResponse({"clinics": [dict(r) for r in rows]})


@router.patch("/api/admin/clinic/{clinic_id}")
async def api_admin_update_clinic(clinic_id: int, request: Request):
    """클리닉 메타 일부 수정 — plan_id / trial_expires_at / plan_expires_at."""
    _require_admin_or_session(request)
    body = await request.json()
    fields: list[str] = []
    values: list = []
    if "plan_id" in body:
        plan = (body["plan_id"] or "").strip()
        if plan:
            fields.append("plan_id = ?"); values.append(plan)
    if "trial_expires_at" in body:
        fields.append("trial_expires_at = ?"); values.append(body["trial_expires_at"] or None)
    if "plan_expires_at" in body:
        fields.append("plan_expires_at = ?"); values.append(body["plan_expires_at"] or None)
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다.")
    values.append(clinic_id)
    with _get_db() as conn:
        cur = conn.execute(f"UPDATE clinics SET {', '.join(fields)} WHERE id = ?", values)
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="클리닉을 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@router.get("/api/admin/usage")
async def api_admin_usage(request: Request):
    """전체·클리닉별 사용량 집계."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        # 전체 합산
        total_blog = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs "
            "WHERE feature = 'blog_generation' AND used_at >= datetime('now','start of month')"
        ).fetchone()
        total_blog_all = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs WHERE feature = 'blog_generation'"
        ).fetchone()
        total_copy = conn.execute(
            "SELECT COUNT(*) AS c FROM usage_logs "
            "WHERE feature = 'prompt_copy' AND used_at >= datetime('now','start of month')"
        ).fetchone()
        # 클리닉별 이번 달 블로그 랭킹
        ranking = conn.execute(
            """
            SELECT c.id, c.name, c.plan_id,
                   COUNT(u.id) AS blog_this_month
            FROM clinics c
            LEFT JOIN usage_logs u
              ON u.clinic_id = c.id AND u.feature = 'blog_generation'
                 AND u.used_at >= datetime('now','start of month')
            GROUP BY c.id
            ORDER BY blog_this_month DESC, c.created_at DESC
            LIMIT 50
            """
        ).fetchall()
    # 에러 카운트 (data/error_logs/{date}.jsonl 오늘자만 빠르게)
    error_count_today = 0
    try:
        from datetime import datetime as _dt
        today = _dt.utcnow().strftime("%Y-%m-%d")
        err_path = ROOT / "data" / "error_logs" / f"{today}.jsonl"
        if err_path.exists():
            with open(err_path, encoding="utf-8") as f:
                error_count_today = sum(1 for line in f if line.strip())
    except Exception:
        pass
    return JSONResponse({
        "blog_this_month": int(total_blog["c"]) if total_blog else 0,
        "blog_all_time": int(total_blog_all["c"]) if total_blog_all else 0,
        "prompt_copy_this_month": int(total_copy["c"]) if total_copy else 0,
        "error_count_today": error_count_today,
        "ranking": [dict(r) for r in ranking],
    })


@router.get("/api/admin/feedback")
async def api_admin_feedback(
    request: Request,
    status: str = "all",  # all / new / viewed
    page: int = 1,
    per_page: int = 30,
):
    """피드백 목록. status=new(미확인) / viewed(확인됨) / all."""
    _require_admin_or_session(request)
    where = ""
    if status == "new":
        where = "WHERE viewed_at IS NULL"
    elif status == "viewed":
        where = "WHERE viewed_at IS NOT NULL"
    offset = (max(page, 1) - 1) * per_page
    with _get_db() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM feedback {where}").fetchone()["c"]
        new_count = conn.execute(
            "SELECT COUNT(*) AS c FROM feedback WHERE viewed_at IS NULL"
        ).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT f.id, f.page, f.message, f.created_at, f.viewed_at,
                   f.context_json,
                   f.clinic_id, f.user_id,
                   c.name AS clinic_name,
                   u.email AS user_email
            FROM feedback f
            LEFT JOIN clinics c ON c.id = f.clinic_id
            LEFT JOIN users u ON u.id = f.user_id
            {where}
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return JSONResponse({
        "total": total,
        "new_count": new_count,
        "items": [dict(r) for r in rows],
    })


@router.post("/api/admin/feedback/{fid}/viewed")
async def api_admin_feedback_mark_viewed(fid: int, request: Request):
    """피드백 확인 처리."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        cur = conn.execute(
            "UPDATE feedback SET viewed_at = datetime('now','utc') WHERE id = ? AND viewed_at IS NULL",
            (fid,),
        )
    return JSONResponse({"ok": True, "updated": cur.rowcount})


@router.post("/api/admin/feedback/{fid}/unview")
async def api_admin_feedback_unview(fid: int, request: Request):
    """피드백 확인 취소 (다시 미확인)."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        conn.execute("UPDATE feedback SET viewed_at = NULL WHERE id = ?", (fid,))
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 네이버 API 설정
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/naver-config")
async def get_naver_config(request: Request):
    """네이버 API 설정 조회 — 세션 또는 ADMIN_SECRET Bearer 인증"""
    _require_admin_or_session(request)
    from naver_checker import APP_SETTINGS_PATH
    import json as _j
    cfg: dict = {}
    try:
        if APP_SETTINGS_PATH.exists():
            with open(APP_SETTINGS_PATH, encoding="utf-8") as f:
                cfg = _j.load(f)
    except Exception:
        pass
    return JSONResponse({
        "naver_client_id": cfg.get("naver_client_id", ""),
        "naver_client_secret_masked": "****" if cfg.get("naver_client_secret") else "",
        "configured": bool(cfg.get("naver_client_id") and cfg.get("naver_client_secret")),
    })


@router.post("/api/admin/naver-config")
async def save_naver_config(request: Request):
    """네이버 API 설정 저장 — 세션 또는 ADMIN_SECRET Bearer 인증"""
    _require_admin_or_session(request)
    body = await request.json()
    client_id = body.get("naver_client_id", "").strip()
    client_secret = body.get("naver_client_secret", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=400, detail="Client ID와 Secret 모두 필요합니다.")
    from naver_checker import APP_SETTINGS_PATH
    import json as _j
    cfg: dict = {}
    try:
        if APP_SETTINGS_PATH.exists():
            with open(APP_SETTINGS_PATH, encoding="utf-8") as f:
                cfg = _j.load(f)
    except Exception:
        pass
    cfg["naver_client_id"] = client_id
    cfg["naver_client_secret"] = client_secret
    APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(APP_SETTINGS_PATH, "w", encoding="utf-8") as f:
        _j.dump(cfg, f, ensure_ascii=False, indent=2)
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 어드민 API — 베타 신청자 관리
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/applicants")
async def api_admin_applicants(request: Request):
    """신청자 목록 + 퍼널 통계 반환. 세션 또는 ADMIN_SECRET Bearer 인증."""
    _require_admin_or_session(request)

    application_type = request.query_params.get("type")  # beta / general / 전체(None)
    status_filter    = request.query_params.get("status")  # pending/invited/registered/rejected/expired

    with _get_db() as conn:
        sql = (
            "SELECT a.*, "
            "  (SELECT COUNT(*) FROM applicant_emails e WHERE e.applicant_id = a.id) AS email_count, "
            "  (SELECT COUNT(*) FROM applicant_emails e WHERE e.applicant_id = a.id AND e.success = 0) AS email_failed "
            "FROM beta_applicants a "
            "WHERE 1=1 "
        )
        params: list = []
        if application_type in ("beta", "general"):
            sql += "AND a.application_type = ? "
            params.append(application_type)
        if status_filter in ("pending", "invited", "registered", "rejected", "expired"):
            sql += "AND a.status = ? "
            params.append(status_filter)
        sql += "ORDER BY a.applied_at DESC"
        rows = conn.execute(sql, params).fetchall()

        # 퍼널 통계 (필터 무관 — 전체 기준)
        stats = conn.execute(
            """
            SELECT
              COUNT(*)                                                         AS total,
              SUM(CASE WHEN status = 'pending'    THEN 1 ELSE 0 END)            AS pending,
              SUM(CASE WHEN status = 'invited'    THEN 1 ELSE 0 END)            AS invited,
              SUM(CASE WHEN status = 'registered' THEN 1 ELSE 0 END)            AS registered,
              SUM(CASE WHEN status = 'rejected'   THEN 1 ELSE 0 END)            AS rejected,
              SUM(CASE WHEN status = 'expired'    THEN 1 ELSE 0 END)            AS expired,
              SUM(CASE WHEN invited_at IS NOT NULL THEN 1 ELSE 0 END)           AS sent,
              SUM(CASE WHEN clicked_at IS NOT NULL THEN 1 ELSE 0 END)           AS clicked
            FROM beta_applicants
            """
        ).fetchone()

    return JSONResponse({
        "applicants": [dict(r) for r in rows],
        "stats": dict(stats) if stats else {},
    })


@router.get("/api/admin/applicants/{applicant_id}/emails")
async def api_admin_applicant_emails(request: Request, applicant_id: int):
    """신청자별 이메일 발송 이력 (timeline UI용)."""
    _require_admin_or_session(request)
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT id, email_type, sent_at, success, error_msg "
            "FROM applicant_emails WHERE applicant_id = ? "
            "ORDER BY sent_at DESC, id DESC",
            (applicant_id,),
        ).fetchall()
    return JSONResponse({"emails": [dict(r) for r in rows]})


@router.patch("/api/admin/applicants/{applicant_id}")
async def api_admin_applicant_patch(request: Request, applicant_id: int):
    """admin_notes / admin_tags 수정. 세션 또는 ADMIN_SECRET 인증."""
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    fields: list = []
    params: list = []
    if "admin_notes" in body:
        fields.append("admin_notes = ?")
        params.append((body.get("admin_notes") or "").strip() or None)
    if "admin_tags" in body:
        # 콤마 구분, 트림
        raw = (body.get("admin_tags") or "").strip()
        cleaned = ",".join(t.strip() for t in raw.split(",") if t.strip()) if raw else None
        fields.append("admin_tags = ?")
        params.append(cleaned)
    if not fields:
        return JSONResponse({"detail": "수정할 필드가 없습니다."}, status_code=400)
    params.append(applicant_id)

    with _get_db() as conn:
        cur = conn.execute(
            f"UPDATE beta_applicants SET {', '.join(fields)} WHERE id = ?",
            params,
        )
        if cur.rowcount == 0:
            return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"ok": True})


@router.post("/api/admin/applicants/{applicant_id}/reject")
async def api_admin_applicant_reject(request: Request, applicant_id: int):
    """신청 거절. 사유 기록 + status='rejected'."""
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = (body.get("reason") or "").strip() or "사유 미기재"

    with _get_db() as conn:
        row = conn.execute(
            "SELECT status FROM beta_applicants WHERE id = ?", (applicant_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)
        if row["status"] in ("registered",):
            return JSONResponse(
                {"detail": "이미 가입 완료된 신청자는 거절할 수 없습니다."}, status_code=400,
            )
        conn.execute(
            "UPDATE beta_applicants SET status = 'rejected', rejection_reason = ? WHERE id = ?",
            (reason, applicant_id),
        )
    return JSONResponse({"ok": True})


@router.post("/api/admin/applicants/{applicant_id}/resend")
async def api_admin_applicant_resend(request: Request, applicant_id: int):
    """
    수동 재발송. body.email_type:
      - apply_confirm / admin_notify / invite / reminder
    invite/reminder는 invite_token이 있어야 함 (없으면 invite-batch로 다시 진행).
    """
    _require_admin_or_session(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    email_type = (body.get("email_type") or "").strip()
    if email_type not in ("apply_confirm", "admin_notify", "invite", "reminder"):
        return JSONResponse({"detail": "허용되지 않은 email_type"}, status_code=400)

    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, name, clinic_name, phone, email, note, invite_token "
            "FROM beta_applicants WHERE id = ?",
            (applicant_id,),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "신청자를 찾을 수 없습니다."}, status_code=404)

    base_url = os.getenv("BASE_URL", "https://cligent.kr")

    try:
        if email_type == "apply_confirm":
            from plan_notify import send_beta_apply_confirm
            ok = await asyncio.to_thread(
                send_beta_apply_confirm, row["email"], row["name"], applicant_id,
            )
        elif email_type == "admin_notify":
            from plan_notify import send_beta_admin_notify
            ok = await asyncio.to_thread(
                send_beta_admin_notify,
                row["name"], row["clinic_name"], row["email"], row["note"] or "",
                applicant_id,
            )
        else:  # invite / reminder — invite_token 필요
            if not row["invite_token"]:
                return JSONResponse(
                    {"detail": "초대 토큰이 없습니다. invite-batch로 먼저 초대해 주세요."},
                    status_code=400,
                )
            invite_url = f"{base_url}/onboard?token={row['invite_token']}"
            if email_type == "invite":
                from plan_notify import send_beta_invite_email
                ok = await asyncio.to_thread(
                    send_beta_invite_email, row["email"], row["name"], invite_url, applicant_id,
                )
            else:  # reminder
                from plan_notify import send_beta_reminder
                ok = await asyncio.to_thread(
                    send_beta_reminder, row["email"], row["name"], invite_url, applicant_id,
                )
    except Exception as exc:
        _logging.getLogger(__name__).warning("resend 실패 (id=%s): %s", applicant_id, exc)
        return JSONResponse({"detail": f"발송 중 오류: {exc}"}, status_code=500)

    return JSONResponse({"ok": True, "sent": bool(ok)})


@router.post("/api/admin/invite-batch")
async def api_admin_invite_batch(request: Request):
    """
    선택한 신청자에게 초대 링크 일괄 발송.
    인증: 세션(chief_director + ADMIN_CLINIC_ID) 또는 Bearer ADMIN_SECRET.

    요청: { "ids": [1, 2, 3] }
    응답: { "invited": [...], "failed": [...] }
    """
    _require_admin_or_session(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "JSON 파싱 오류"}, status_code=400)

    ids = body.get("ids", [])
    if not ids or not isinstance(ids, list):
        return JSONResponse({"detail": "ids 배열이 필요합니다."}, status_code=400)

    admin_clinic_id = int(os.getenv("ADMIN_CLINIC_ID", "1"))
    admin_user_id   = int(os.getenv("ADMIN_USER_ID", "1"))
    base_url = os.getenv("BASE_URL", "https://cligent.kr")

    from plan_notify import send_beta_invite_email

    invited = []
    failed  = []

    # asyncio.Semaphore는 코루틴 내부에서 생성해야 함
    sem = asyncio.Semaphore(5)

    async def _send_one(row: dict) -> None:
        async with sem:
            try:
                token = create_invite(
                    clinic_id=admin_clinic_id,
                    email=row["email"],
                    role="chief_director",
                    created_by=admin_user_id,
                )
                invite_url = f"{base_url}/onboard?token={token}"
                now_iso = datetime.now(timezone.utc).isoformat()

                with _get_db() as conn:
                    conn.execute(
                        "UPDATE beta_applicants "
                        "SET status = 'invited', invited_at = ?, invite_token = ? "
                        "WHERE id = ?",
                        (now_iso, token, row["id"]),
                    )

                await asyncio.to_thread(
                    send_beta_invite_email, row["email"], row["name"], invite_url, row["id"],
                )
                invited.append(row["id"])

            except ValueError as exc:
                failed.append({"id": row["id"], "reason": str(exc)})
            except Exception as exc:
                _logging.getLogger(__name__).warning(
                    "invite-batch 실패 (id=%s): %s", row["id"], exc
                )
                failed.append({"id": row["id"], "reason": "초대 생성 오류"})

    with _get_db() as conn:
        rows = conn.execute(
            f"SELECT id, name, email FROM beta_applicants "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND status = 'pending'",
            ids,
        ).fetchall()

    await asyncio.gather(*[_send_one(dict(r)) for r in rows])

    return JSONResponse({"invited": invited, "failed": failed})


# ─────────────────────────────────────────────────────────────────
# 공지사항 작성·수정·삭제 (admin only)
#   - 공지 read 라우트(/announcements 목록·상세·읽음)는 dashboard.py 에 위치.
#   - /announcements/new 와 /announcements/{ann_id}/edit 는 dashboard 의
#     /announcements/{ann_id} (int 검증) 보다 먼저 등록되어야 라우팅 충돌 회피.
#     main.py 의 include_router 순서가 이를 보장 (admin → dashboard).
# ─────────────────────────────────────────────────────────────────

@router.get("/announcements/new")
async def announcement_new_page(user: dict = Depends(get_current_user)):
    """공지 작성 페이지 — admin only."""
    _require_announce_admin(user)
    return FileResponse(ROOT / "templates" / "announcement_edit.html")


@router.get("/announcements/{ann_id}/edit")
async def announcement_edit_page(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 수정 페이지 — admin only."""
    _require_announce_admin(user)
    return FileResponse(ROOT / "templates" / "announcement_edit.html")


@router.post("/api/announcements")
async def api_announcement_create(request: Request, user: dict = Depends(get_current_user)):
    """공지 신규 작성 — admin only."""
    _require_announce_admin(user)
    body = await request.json()
    title = (body.get("title") or "").strip()
    body_md = (body.get("body_md") or "").strip()
    category = body.get("category", "general")
    is_pinned = 1 if body.get("is_pinned") else 0
    if not title or not body_md:
        raise HTTPException(status_code=400, detail="제목과 본문을 입력해주세요.")
    if category not in _ANNOUNCE_CATEGORIES:
        category = "general"
    author = body.get("author") or "원장"
    with _get_db() as conn:
        cur = conn.execute(
            "INSERT INTO announcements (title, body_md, category, is_pinned, author) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, body_md, category, is_pinned, author),
        )
        new_id = cur.lastrowid
    return JSONResponse({"id": new_id})


@router.patch("/api/announcements/{ann_id}")
async def api_announcement_update(ann_id: int, request: Request, user: dict = Depends(get_current_user)):
    """공지 수정 — admin only."""
    _require_announce_admin(user)
    body = await request.json()
    fields = []
    values = []
    for key in ("title", "body_md", "author"):
        if key in body:
            val = (body[key] or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail=f"{key}는 비워둘 수 없습니다.")
            fields.append(f"{key} = ?")
            values.append(val)
    if "category" in body:
        cat = body["category"]
        if cat not in _ANNOUNCE_CATEGORIES:
            cat = "general"
        fields.append("category = ?")
        values.append(cat)
    if "is_pinned" in body:
        fields.append("is_pinned = ?")
        values.append(1 if body["is_pinned"] else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="수정할 항목이 없습니다.")
    fields.append("updated_at = datetime('now', 'utc')")
    values.append(ann_id)
    with _get_db() as conn:
        cur = conn.execute(
            f"UPDATE announcements SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@router.delete("/api/announcements/{ann_id}")
async def api_announcement_delete(ann_id: int, user: dict = Depends(get_current_user)):
    """공지 삭제 — admin only."""
    _require_announce_admin(user)
    with _get_db() as conn:
        cur = conn.execute("DELETE FROM announcements WHERE id = ?", (ann_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="공지를 찾을 수 없습니다.")
    return JSONResponse({"ok": True})


@router.post("/api/announcements/upload-image")
async def api_announcement_upload_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """공지 본문 첨부 이미지 업로드 — admin only. 반환: {url}"""
    _require_announce_admin(user)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ANNOUNCE_ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="지원하지 않는 형식입니다 (jpg, png, webp, gif).")
    data = await file.read()
    if len(data) > _ANNOUNCE_MAX_UPLOAD:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다 (최대 5MB).")
    upload_dir = ROOT / "static" / "uploads" / "announcements"
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_uuid.uuid4().hex}{ext}"
    fpath = upload_dir / fname
    fpath.write_bytes(data)
    url = f"/static/uploads/announcements/{fname}"
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO announcement_attachments (filename, url) VALUES (?, ?)",
            (fname, url),
        )
    return JSONResponse({"url": url})


# ─────────────────────────────────────────────────────────────────
# 관리자 OpenAI API 키 등록 (Phase 1, 2026-04-30)
#   베타 단계: BYOAI 비활성, 모든 사용자가 이 키를 공유 사용.
#   저장: server_secrets 테이블 + Fernet 암호화 (secret_manager 모듈)
#   검증: OpenAI models.list() 호출로 즉시 키 유효성 확인.
# ─────────────────────────────────────────────────────────────────

@router.get("/api/admin/openai-key")
def api_admin_get_openai_key(request: Request):
    """현재 등록된 OpenAI 키 메타 (마스킹된 값 + 갱신일 + 갱신자). 미등록 시 secret=null."""
    _require_admin_or_session(request)
    from secret_manager import get_secret_meta
    meta = get_secret_meta("openai_api_key")
    return JSONResponse({"secret": meta})


@router.post("/api/admin/openai-key")
async def api_admin_set_openai_key(request: Request):
    """
    OpenAI 키 저장 + 즉시 유효성 검증.
    Body: {"value": "sk-..."}
    검증 실패 시 저장하지 않고 400 반환.
    """
    _require_admin_or_session(request)
    body = await request.json()
    value = (body.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="키 값이 비어 있습니다.")
    if not value.startswith("sk-"):
        raise HTTPException(status_code=400, detail="OpenAI 키는 'sk-'로 시작해야 합니다.")

    # OpenAI에 가벼운 호출로 키 검증
    # Restricted(이미지 전용) 키는 models.list() 권한이 없어 PermissionDeniedError 발생.
    # 401 AuthenticationError = 키 자체가 무효 → 거부
    # 403 PermissionDeniedError = 키는 유효하지만 Models 스코프만 없음 → 통과
    #   (이미지 스코프는 첫 실호출 시 ai_client에서 검증됨)
    try:
        import openai
        client = openai.OpenAI(api_key=value, timeout=10.0)
        client.models.list()  # 200 → 유효
    except openai.AuthenticationError:
        raise HTTPException(status_code=400, detail="유효하지 않은 OpenAI 키입니다.")
    except openai.PermissionDeniedError:
        # Restricted 키 (이미지 전용 등) — 인증은 통과한 상태이므로 저장 허용
        _error_logger.info("OpenAI 키 검증: PermissionDenied — Restricted 키로 간주하고 저장")
    except openai.APIConnectionError:
        raise HTTPException(status_code=503, detail="OpenAI 연결 실패. 잠시 후 다시 시도하세요.")
    except openai.RateLimitError:
        raise HTTPException(status_code=429, detail="OpenAI 요청 한도 도달. 잠시 후 다시 시도하세요.")
    except HTTPException:
        raise
    except Exception as exc:
        _error_logger.exception("OpenAI 키 검증 중 예외")
        raise HTTPException(status_code=500, detail=f"검증 중 오류: {type(exc).__name__}")

    # 검증 성공 → 저장 (감사용 user_id 동봉)
    user_id = _resolve_user_id_from_session(request)
    from secret_manager import set_server_secret, get_secret_meta
    set_server_secret("openai_api_key", value, user_id=user_id)
    meta = get_secret_meta("openai_api_key")
    return JSONResponse({"ok": True, "secret": meta})


@router.delete("/api/admin/openai-key")
def api_admin_delete_openai_key(request: Request):
    """OpenAI 키 삭제 (테스트·키 회전용)."""
    _require_admin_or_session(request)
    from secret_manager import delete_server_secret
    deleted = delete_server_secret("openai_api_key")
    return JSONResponse({"ok": True, "deleted": deleted})
