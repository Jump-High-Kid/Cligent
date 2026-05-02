"""
src/routers/clinic.py — 클리닉 설정·팀·AI·블로그 설정·RBAC·모듈 라우터

라우트 (25개):
  HTML 페이지:
    GET  /settings                              설정 메인 (인증 필요)
    GET  /settings/setup                        RBAC 위자드 (인증 필요)

  팀 & 권한 (6):
    GET    /api/settings/staff
    POST   /api/settings/staff/modules
    PATCH  /api/settings/staff/{staff_id}
    POST   /api/settings/staff/{staff_id}/reinvite
    DELETE /api/settings/staff/{staff_id}
    POST   /api/settings/staff/{staff_id}/activate

  한의원 프로필 (2) + Naver Blog ID (1):
    GET  /api/settings/clinic/profile
    POST /api/settings/clinic/profile
    POST /api/settings/clinic/naver-blog-id

  AI 설정 (4):
    GET  /api/settings/clinic/ai
    POST /api/settings/clinic/ai
    POST /api/settings/clinic/ai/validate
    POST /api/settings/clinic/ai/onboarding-start

  블로그 설정·프롬프트 (5):
    GET  /api/settings/blog
    POST /api/settings/blog
    GET  /api/settings/blog/prompt
    POST /api/settings/blog/prompt
    POST /api/settings/blog/prompt/reset

  RBAC (2):
    GET  /api/settings/rbac
    POST /api/settings/rbac

  모듈 (3):
    GET  /api/modules/my
    GET  /api/modules/info
    POST /api/modules/config

main.py 4,000줄 분할의 두 번째 라우터 (v0.9.0 / 2026-05-02). auth.py 다음.
"""
from __future__ import annotations

import json as _json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)

from auth_manager import COOKIE_NAME, create_reinvite, get_current_user
from config_loader import load_config, save_blog_config, save_prompt
from crypto_utils import decrypt_key, encrypt_key, mask_key
from dependencies import NO_CACHE_HEADERS, is_admin_clinic
from module_manager import (
    get_allowed_modules,
    get_module_info,
    role_has_access,
    save_staff_permissions,
)
from settings_manager import get_setup_wizard_data, save_wizard_result

# 프로젝트 루트 (src/routers/clinic.py 기준 3단계 위)
ROOT = Path(__file__).resolve().parent.parent.parent

router = APIRouter()


# 허용 Anthropic 모델 — AI 설정 저장 시 검증
_VALID_MODELS = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
}


# ─────────────────────────────────────────────────────────────────
# HTML 페이지
# ─────────────────────────────────────────────────────────────────

@router.get("/settings")
async def settings_page(request: Request):
    """설정 페이지 (메인)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")
    return FileResponse(ROOT / "templates" / "settings.html", headers=NO_CACHE_HEADERS)


@router.get("/settings/setup")
async def settings_setup(request: Request):
    """RBAC 초기 설정 위자드 — 대표원장 전용 (페이지 자체는 토큰만 확인)."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return RedirectResponse("/login")

    template_path = ROOT / "templates" / "settings_setup.html"
    html = template_path.read_text(encoding="utf-8")
    wizard_data = get_setup_wizard_data()
    html = html.replace("__RBAC_DATA__", _json.dumps(wizard_data, ensure_ascii=False))
    return HTMLResponse(content=html, headers=NO_CACHE_HEADERS)


# ─────────────────────────────────────────────────────────────────
# 팀 & 권한 관리
# ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/staff")
async def get_staff_list(user: dict = Depends(get_current_user)):
    """설정 페이지용 직원 목록 — DB에서 직접 조회."""
    from db_manager import get_db
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, role, is_active FROM users WHERE clinic_id = ? ORDER BY id",
            (user["clinic_id"],),
        ).fetchall()
    staff = [dict(r) for r in rows]
    # 각 직원의 모듈 권한 읽기
    staff_path = ROOT / "data" / "staff_permissions.json"
    perms = _json.loads(staff_path.read_text()) if staff_path.exists() else {}
    for s in staff:
        key = str(s["id"])
        s["modules"] = perms.get(key, {}).get("modules", [])
        s["name"] = perms.get(key, {}).get("name", s["email"].split("@")[0])
    return JSONResponse({"staff": staff})


@router.post("/api/settings/staff/modules")
async def save_staff_modules(request: Request, user: dict = Depends(get_current_user)):
    """직원 모듈 권한 즉시 저장 (토글 자동저장용)."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)
    body = await request.json()
    staff_id = str(body.get("staff_id", "")).strip()
    modules = body.get("modules", [])
    if not staff_id:
        return JSONResponse({"detail": "staff_id가 필요합니다."}, status_code=400)
    # 이름 조회
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (staff_id,)).fetchone()
    name = row["email"].split("@")[0] if row else staff_id
    result = save_staff_permissions(staff_id, name, modules)
    return JSONResponse({"ok": True, **result})


@router.patch("/api/settings/staff/{staff_id}")
async def update_staff(staff_id: str, request: Request, user: dict = Depends(get_current_user)):
    """직원 이름·역할 변경 — director 이상 전용."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    body = await request.json()
    new_name = body.get("name", "").strip()
    new_role = body.get("role", "").strip()

    VALID_ROLES = {"team_member", "team_leader", "manager", "director", "chief_director"}

    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

        # chief_director 역할은 변경 금지
        if row["role"] == "chief_director" and new_role and new_role != "chief_director":
            return JSONResponse({"detail": "대표원장 역할은 변경할 수 없습니다."}, status_code=403)

        if new_role:
            if new_role not in VALID_ROLES:
                return JSONResponse({"detail": "유효하지 않은 역할입니다."}, status_code=400)
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, staff_id))

    if new_name:
        staff_path = ROOT / "data" / "staff_permissions.json"
        perms = _json.loads(staff_path.read_text()) if staff_path.exists() else {}
        if staff_id not in perms:
            perms[staff_id] = {"modules": []}
        perms[staff_id]["name"] = new_name
        staff_path.write_text(_json.dumps(perms, ensure_ascii=False, indent=2))

    return JSONResponse({"ok": True})


@router.post("/api/settings/staff/{staff_id}/reinvite")
async def reinvite_staff(staff_id: str, request: Request, user: dict = Depends(get_current_user)):
    """비밀번호 재설정 링크 생성 — director 이상 + 베타 정책상 admin 클리닉만."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    # 베타 정책: 본인 클리닉 외 직원 관리 기능 차단
    if not is_admin_clinic(user):
        return JSONResponse(
            {"detail": "베타 단계에서는 직원 관리 기능이 일시 비활성화되어 있습니다."},
            status_code=403,
        )

    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, role FROM users WHERE id = ? AND clinic_id = ? AND is_active = 1",
            (staff_id, user["clinic_id"]),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

    token = create_reinvite(
        clinic_id=user["clinic_id"],
        email=row["email"],
        role=row["role"],
        created_by=user["id"],
    )
    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/onboard?token={token}"
    return JSONResponse({"ok": True, "invite_url": invite_url})


@router.delete("/api/settings/staff/{staff_id}")
async def deactivate_staff(staff_id: str, user: dict = Depends(get_current_user)):
    """직원 비활성화 (소프트 딜리트) — director 이상 전용."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)
        if row["role"] == "chief_director":
            return JSONResponse({"detail": "대표원장은 비활성화할 수 없습니다."}, status_code=403)
        if str(staff_id) == str(user["id"]):
            return JSONResponse({"detail": "본인 계정은 비활성화할 수 없습니다."}, status_code=403)

        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (staff_id,))

    return JSONResponse({"ok": True})


@router.post("/api/settings/staff/{staff_id}/activate")
async def activate_staff(staff_id: str, user: dict = Depends(get_current_user)):
    """비활성화된 직원 재활성화 — director 이상 전용."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE id = ? AND clinic_id = ?",
            (staff_id, user["clinic_id"]),
        ).fetchone()
        if not row:
            return JSONResponse({"detail": "직원을 찾을 수 없습니다."}, status_code=404)

        conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (staff_id,))

    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 한의원 프로필
# ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/clinic/profile")
async def get_clinic_profile(user: dict = Depends(get_current_user)):
    """한의원 프로필 조회 — 인증된 사용자라면 누구나 조회 가능."""
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT name, phone, address, specialty, hours, intro, blog_features, naver_blog_id "
            "FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "한의원 정보를 찾을 수 없습니다."}, status_code=404)
    hours = None
    try:
        hours = _json.loads(row["hours"]) if row["hours"] else None
    except Exception:
        hours = None
    return JSONResponse({
        "name": row["name"] or "",
        "phone": row["phone"] or "",
        "address": row["address"] or "",
        "specialty": row["specialty"] or "",
        "hours": hours,
        "intro": row["intro"] or "",
        "blog_features": row["blog_features"] or "",
        "naver_blog_id": row["naver_blog_id"] or "",
    })


@router.post("/api/settings/clinic/profile")
async def save_clinic_profile(request: Request, user: dict = Depends(get_current_user)):
    """한의원 프로필 저장 — chief_director 전용."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()

    name = body.get("name", "").strip()
    phone = body.get("phone", "").strip()
    address = body.get("address", "").strip()
    specialty = body.get("specialty", "").strip()
    hours = body.get("hours")  # dict or None
    intro = body.get("intro", "").strip()
    blog_features = body.get("blog_features", "").strip()
    naver_blog_id = body.get("naver_blog_id", "").strip()

    if not name:
        return JSONResponse({"detail": "한의원 이름은 필수입니다."}, status_code=400)

    hours_json = _json.dumps(hours, ensure_ascii=False) if hours else None

    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE clinics SET name=?, phone=?, address=?, specialty=?, hours=?, "
            "intro=?, blog_features=?, naver_blog_id=? WHERE id=?",
            (name, phone or None, address or None, specialty or None, hours_json,
             intro or None, blog_features or None, naver_blog_id or None, user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


@router.post("/api/settings/clinic/naver-blog-id")
async def save_naver_blog_id(request: Request, user: dict = Depends(get_current_user)):
    """네이버 블로그 아이디 단일 필드 저장 — chief_director 전용."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()
    naver_blog_id = body.get("naver_blog_id", "").strip()
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE clinics SET naver_blog_id=? WHERE id=?",
            (naver_blog_id or None, user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# AI 설정 (Anthropic API 키·모델·예산)
# ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/clinic/ai")
async def get_clinic_ai(user: dict = Depends(get_current_user)):
    """AI 설정 조회 — chief_director 전용. .env 폴백 표시."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 접근할 수 있습니다."}, status_code=403)
    from db_manager import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT model, monthly_budget_krw, api_key_enc FROM clinics WHERE id = ?",
            (user["clinic_id"],),
        ).fetchone()
    if not row:
        return JSONResponse({"detail": "한의원 정보를 찾을 수 없습니다."}, status_code=404)

    # DB에 API 키가 없으면 .env 키 사용 여부를 표시
    api_key_set = bool(row["api_key_enc"])
    env_key = os.getenv("ANTHROPIC_API_KEY", "")
    api_key_masked = ""
    if api_key_set:
        try:
            plain = decrypt_key(row["api_key_enc"])
            api_key_masked = mask_key(plain)
        except Exception:
            api_key_masked = "복호화 오류"
    elif env_key:
        api_key_masked = mask_key(env_key) + " (.env)"

    return JSONResponse({
        "model": row["model"] or "claude-sonnet-4-6",
        "monthly_budget_krw": row["monthly_budget_krw"] or 10000,
        "api_key_masked": api_key_masked,
        "api_key_source": "db" if api_key_set else ("env" if env_key else "none"),
    })


@router.post("/api/settings/clinic/ai")
async def save_clinic_ai(request: Request, user: dict = Depends(get_current_user)):
    """AI 설정 저장 — chief_director 전용. 빈 api_key는 기존 유지, clear_key=True 명시 삭제."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()

    model = body.get("model", "").strip()
    budget = body.get("monthly_budget_krw")
    api_key_new = body.get("api_key", "").strip()  # 빈 문자열이면 기존 유지

    if model and model not in _VALID_MODELS:
        return JSONResponse({"detail": "지원하지 않는 모델입니다."}, status_code=400)
    if budget is not None:
        try:
            budget = int(budget)
            if budget < 0:
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse({"detail": "예산은 0 이상의 정수여야 합니다."}, status_code=400)

    api_key_enc = None
    clear_key = body.get("clear_key", False)  # 명시적 키 삭제 요청

    if api_key_new:
        if not api_key_new.startswith("sk-ant-"):
            return JSONResponse(
                {"detail": "올바른 Anthropic API 키 형식이 아닙니다. (sk-ant- 로 시작해야 함)"},
                status_code=400,
            )
        api_key_enc = encrypt_key(api_key_new)

    from db_manager import get_db
    with get_db() as conn:
        if api_key_enc:
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw), api_key_enc=?, api_key_configured=1 WHERE id=?",
                (model, user["clinic_id"], budget, api_key_enc, user["clinic_id"]),
            )
        elif clear_key:
            # 키 명시적 삭제 시 api_key_configured 리셋
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw), api_key_enc=NULL, api_key_configured=0 WHERE id=?",
                (model, user["clinic_id"], budget, user["clinic_id"]),
            )
        else:
            conn.execute(
                "UPDATE clinics SET model=COALESCE(NULLIF(?,''),(SELECT model FROM clinics WHERE id=?)), "
                "monthly_budget_krw=COALESCE(?,monthly_budget_krw) WHERE id=?",
                (model, user["clinic_id"], budget, user["clinic_id"]),
            )
    return JSONResponse({"ok": True})


@router.post("/api/settings/clinic/ai/validate")
async def validate_clinic_ai_key(request: Request, user: dict = Depends(get_current_user)):
    """API 키 유효성 검증 — 온보딩 위자드용. 실제 Anthropic API 호출."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 API 키를 설정할 수 있습니다."}, status_code=403)

    body = await request.json()
    api_key = body.get("api_key", "").strip()

    if not api_key:
        return JSONResponse({"detail": "API 키를 입력해주세요."}, status_code=400)
    if not api_key.startswith("sk-ant-"):
        return JSONResponse(
            {"detail": "올바른 Claude API 키 형식이 아닙니다. (sk-ant- 로 시작해야 함)"},
            status_code=400,
        )

    import anthropic
    import httpx
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=8.0)
        # models.list()는 가장 저렴한 검증용 호출
        client.models.list(limit=1)
        return JSONResponse({"ok": True})
    except anthropic.AuthenticationError:
        return JSONResponse(
            {"detail": "유효하지 않은 API 키입니다. 키를 다시 확인해주세요."},
            status_code=401,
        )
    except anthropic.RateLimitError:
        return JSONResponse(
            {"detail": "잠시 후 다시 시도해주세요. (요청 한도 초과)"},
            status_code=429,
        )
    except (anthropic.APIConnectionError, httpx.TimeoutException):
        return JSONResponse(
            {"detail": "Anthropic 서버에 연결할 수 없습니다. 인터넷 연결을 확인해주세요."},
            status_code=503,
        )
    except Exception:
        return JSONResponse(
            {"detail": "키 검증 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."},
            status_code=500,
        )


@router.post("/api/settings/clinic/ai/onboarding-start")
async def mark_onboarding_start(user: dict = Depends(get_current_user)):
    """온보딩 위자드 첫 표시 시각 기록 (첫 블로그까지 시간 측정용)."""
    from db_manager import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE clinics SET onboarding_started_at = COALESCE(onboarding_started_at, ?) WHERE id = ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"), user["clinic_id"]),
        )
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 블로그 설정 + 프롬프트
# ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/blog")
async def get_blog_settings(user: dict = Depends(get_current_user)):
    """블로그 설정 조회 — director 이상."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 접근할 수 있습니다."}, status_code=403)
    cfg = load_config()
    return JSONResponse({"flow": cfg.get("flow", {}), "blog": cfg.get("blog", {})})


@router.post("/api/settings/blog")
async def save_blog_settings(request: Request, user: dict = Depends(get_current_user)):
    """블로그 설정 저장 — director 이상. flow.questions_count 1~5, tone whitelist 검증."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 수정할 수 있습니다."}, status_code=403)
    body = await request.json()

    flow = body.get("flow", {})
    blog = body.get("blog", {})

    # 유효성 검사
    if "questions_count" in flow:
        qc = int(flow["questions_count"])
        if not (1 <= qc <= 5):
            return JSONResponse({"detail": "질문 개수는 1~5 사이여야 합니다."}, status_code=400)
        flow["questions_count"] = qc
    if "questions_enabled" in flow:
        flow["questions_enabled"] = bool(flow["questions_enabled"])

    if "min_chars" in blog:
        blog["min_chars"] = int(blog["min_chars"])
    if "max_chars" in blog:
        blog["max_chars"] = int(blog["max_chars"])
    if "min_chars" in blog and "max_chars" in blog:
        if blog["min_chars"] >= blog["max_chars"]:
            return JSONResponse(
                {"detail": "최소 글자 수는 최대 글자 수보다 작아야 합니다."},
                status_code=400,
            )

    VALID_TONES = {"전문적", "친근한", "설명적"}
    if "tone" in blog and blog["tone"] not in VALID_TONES:
        return JSONResponse(
            {"detail": f"톤은 {', '.join(VALID_TONES)} 중 하나여야 합니다."},
            status_code=400,
        )

    save_blog_config(flow, blog)
    return JSONResponse({"ok": True})


@router.get("/api/settings/blog/prompt")
async def get_blog_prompt(user: dict = Depends(get_current_user)):
    """블로그 프롬프트 파일 내용 조회 — director 이상."""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "director 이상만 접근할 수 있습니다."}, status_code=403)
    from config_loader import load_prompt
    content = load_prompt("blog")
    return JSONResponse({"content": content})


@router.post("/api/settings/blog/prompt")
async def save_blog_prompt(request: Request, user: dict = Depends(get_current_user)):
    """블로그 프롬프트 파일 저장 — chief_director 전용."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 프롬프트를 수정할 수 있습니다."}, status_code=403)
    body = await request.json()
    content = body.get("content", "")
    if not content.strip():
        return JSONResponse({"detail": "프롬프트 내용이 비어 있습니다."}, status_code=400)
    save_prompt("blog", content)
    return JSONResponse({"ok": True})


@router.post("/api/settings/blog/prompt/reset")
async def reset_blog_prompt(user: dict = Depends(get_current_user)):
    """블로그 프롬프트를 기본값(blog.default.txt)으로 초기화 — chief_director 전용."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse({"detail": "대표원장만 프롬프트를 초기화할 수 있습니다."}, status_code=403)
    default_path = ROOT / "prompts" / "blog.default.txt"
    if not default_path.exists():
        return JSONResponse({"detail": "기본 프롬프트 파일이 없습니다."}, status_code=404)
    content = default_path.read_text(encoding="utf-8")
    save_prompt("blog", content)
    return JSONResponse({"ok": True, "content": content})


# ─────────────────────────────────────────────────────────────────
# RBAC
# ─────────────────────────────────────────────────────────────────

@router.get("/api/settings/rbac")
async def get_rbac(user: dict = Depends(get_current_user)):
    """RBAC 위자드 데이터 조회 — 현재는 wizard data 그대로."""
    return JSONResponse(get_setup_wizard_data())


@router.post("/api/settings/rbac")
async def save_rbac(request: Request, user: dict = Depends(get_current_user)):
    """RBAC 설정 저장 — chief_director 전용."""
    if not role_has_access(user["role"], ["chief_director"]):
        return JSONResponse(
            {"detail": "대표원장만 RBAC 설정을 변경할 수 있습니다."},
            status_code=403,
        )

    body = await request.json()
    module_permissions = body.get("module_permissions", {})
    settings_permissions = body.get("settings_permissions", {})

    if not module_permissions or not settings_permissions:
        return JSONResponse(
            {"detail": "module_permissions와 settings_permissions가 필요합니다."},
            status_code=400,
        )

    save_wizard_result(module_permissions, settings_permissions)
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────────────
# 모듈 권한 (사용자별·전역 정보)
# ─────────────────────────────────────────────────────────────────

@router.get("/api/modules/my")
async def my_modules(user: dict = Depends(get_current_user)):
    """현재 로그인 사용자에게 허용된 모듈 목록."""
    allowed = get_allowed_modules(role=user["role"], staff_id=None)
    return JSONResponse({"role": user["role"], "modules": allowed})


@router.get("/api/modules/info")
async def modules_info(user: dict = Depends(get_current_user)):
    """전체 모듈 메타정보 (id/이름/설명 등)."""
    return JSONResponse(get_module_info())


@router.post("/api/modules/config")
async def save_module_config(request: Request, user: dict = Depends(get_current_user)):
    """직원 모듈 권한 저장 — director 이상 전용. (legacy 진입점)"""
    if not role_has_access(user["role"], ["chief_director", "director"]):
        return JSONResponse({"detail": "권한이 없습니다."}, status_code=403)

    body = await request.json()
    staff_id = body.get("staff_id", "").strip()
    name = body.get("name", "").strip()
    modules = body.get("modules", [])

    if not staff_id or not name:
        return JSONResponse({"detail": "staff_id와 name은 필수입니다."}, status_code=400)

    result = save_staff_permissions(staff_id, name, modules)
    return JSONResponse({"ok": True, "staff_id": staff_id, **result})
