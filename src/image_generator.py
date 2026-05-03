"""
image_generator.py — 이미지 생성 비즈니스 로직 (Phase 2, 2026-04-30)

설계:
  - HTTP / DB I/O는 main.py 담당. 이 모듈은 순수 도메인 로직.
  - ai_client.call_openai_image_* 를 호출하고 plan별 한도·해상도를 적용.

플랜 정책 (가격 v7):
  Standard  → 1024×1024 medium. 재생성 1회 + 수정 2회 무료
  Pro       → 1536×1024 high.  재생성 2회 + 수정 4회 무료
  trial     → Standard와 동일 처리 (체험 14일)
  free      → 베타 종료 후 정식 free 플랜에서만 의미 (현재 베타엔 미사용)

용어:
  initial   — 블로그 1편당 5장 생성 (한 번만)
  regen     — 같은 프롬프트 재생성 (5장 단위)
  edit      — 1장 부분 수정 (img2img / inpainting)

호출자 책임:
  - 누적 카운터(이번 세션의 regen/edit 횟수)는 호출자가 DB에서 조회 후 전달
  - 한도 초과 시 raise되는 ImageQuotaExceeded를 잡아 종량제 안내 처리
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from ai_client import (
    AIClientError,
    AIResponse,
    call_openai_image_edit,
    call_openai_image_generate,
)

logger = logging.getLogger(__name__)


# ── 플랜 정책 ─────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_beta_image_limits() -> dict[str, int]:
    """config.yaml beta: 섹션에서 trial 한도 override 로드. 실패 시 기본값."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        beta = cfg.get("beta", {}) or {}
        return {
            "regen_free": int(beta.get("image_regen_per_blog", 1)),
            "edit_free": int(beta.get("image_edit_per_blog", 2)),
        }
    except Exception as exc:
        logger.warning("image_generator: config.yaml 로드 실패, 기본값 사용 (%s)", exc)
        return {"regen_free": 1, "edit_free": 2}


# 플랜별 무료 한도 — 1편 블로그 세트 단위로 적용.
# trial은 config.yaml beta.image_regen_per_blog / image_edit_per_blog로 override 가능 (베타 한도 조정용).
# standard / pro / free는 코드 고정 (정식 출시 시점에 별도 분리).
PLAN_LIMITS: dict[str, dict[str, int]] = {
    "standard": {"regen_free": 1, "edit_free": 2},
    "pro": {"regen_free": 2, "edit_free": 4},
    "trial": _load_beta_image_limits(),
    "free": {"regen_free": 0, "edit_free": 0},
}

# 플랜별 해상도·품질 — gpt-image-2 호출 파라미터
PLAN_DIMENSIONS: dict[str, tuple[str, str]] = {
    "standard": ("1024x1024", "medium"),
    "pro": ("1536x1024", "high"),
    "trial": ("1024x1024", "medium"),
    "free": ("1024x1024", "low"),
}

INITIAL_SET_SIZE = 5  # 1편 블로그당 초기 생성 장수


# ── 예외 ──────────────────────────────────────────────────


class ImageQuotaExceeded(Exception):
    """무료 한도 초과. detail에 plan_id / kind / used / limit 포함."""

    def __init__(self, plan_id: str, kind: str, used: int, limit: int):
        self.plan_id = plan_id
        self.kind = kind  # 'regen' | 'edit'
        self.used = used
        self.limit = limit
        super().__init__(
            f"{plan_id} 플랜 {kind} 한도 초과 (사용 {used}/{limit})"
        )


# ── 플랜 헬퍼 ─────────────────────────────────────────────


def normalize_plan_id(plan_id: Optional[str]) -> str:
    """알 수 없는 plan_id는 free로 강등."""
    if plan_id is None:
        return "free"
    if plan_id in PLAN_LIMITS:
        return plan_id
    return "free"


def get_plan_limits(plan_id: Optional[str]) -> dict[str, int]:
    return PLAN_LIMITS[normalize_plan_id(plan_id)]


def get_plan_dimensions(plan_id: Optional[str]) -> tuple[str, str]:
    """(size, quality) 반환."""
    return PLAN_DIMENSIONS[normalize_plan_id(plan_id)]


# ── 결과 컨테이너 ─────────────────────────────────────────


@dataclass(frozen=True)
class ImageSet:
    """이미지 호출 결과 묶음.

    images: base64 PNG 리스트
    plan_id: 적용된 플랜
    size / quality: 호출 해상도 (반환된 메타에 일치)
    mode: 'initial' | 'regen' | 'edit'
    """

    images: list[str]
    plan_id: str
    size: str
    quality: str
    mode: str


# ── 핵심 함수 ─────────────────────────────────────────────


def _build_set(
    responses: list[AIResponse], plan_id: str, mode: str
) -> ImageSet:
    size, quality = get_plan_dimensions(plan_id)
    return ImageSet(
        images=[r.content for r in responses],
        plan_id=normalize_plan_id(plan_id),
        size=size,
        quality=quality,
        mode=mode,
    )


def generate_initial_set(prompt, plan_id: str, on_progress=None) -> ImageSet:
    """블로그 1편당 첫 5장 생성. 한도 체크 없음 (initial은 항상 무료).

    Args:
        prompt: 단일 str (모듈 동일 5장 variation) 또는 list[str]
                (5개 다른 모듈 prompt — 각 1장씩 생성).
                list 길이는 정확히 INITIAL_SET_SIZE(=5).
        plan_id: 플랜.
        on_progress: list 입력 시 호출되는 콜백 — fn(index, total) → None.
                    SSE stage_text 갱신에 사용. 단일 str 입력엔 미사용.

    Returns:
        ImageSet (images 5장).

    Raises:
        AIClientError on bad input. 5번 호출 중 1개라도 실패 시 raise.
    """
    if isinstance(prompt, list):
        return _generate_initial_multi(prompt, plan_id, on_progress=on_progress)

    if not isinstance(prompt, str) or not prompt.strip():
        raise AIClientError("bad_request", "이미지 프롬프트가 비어 있습니다.")

    size, quality = get_plan_dimensions(plan_id)
    responses = call_openai_image_generate(
        prompt=prompt, size=size, quality=quality, n=INITIAL_SET_SIZE
    )
    return _build_set(responses, plan_id, mode="initial")


def _generate_initial_multi(
    prompts: list, plan_id: str, on_progress=None
) -> ImageSet:
    """5개 다른 prompt 각각 1장씩 생성. ImageSet 5장으로 합쳐 반환.

    OpenAI Tier1 rate limit는 ai_client.call_openai_image_generate 내부
    semaphore가 처리. 여기서는 직렬 호출 (각 6~10초, 합 30~50초 예상).

    Args:
        on_progress: fn(index, total) → None. 각 호출 직전에 발화.
                    SSE stage_text로 "이미지 N/5 그리는 중" 등 표시.
    """
    if len(prompts) != INITIAL_SET_SIZE:
        raise AIClientError(
            "bad_request",
            f"prompts list 길이는 정확히 {INITIAL_SET_SIZE}여야 합니다 "
            f"(받은 길이: {len(prompts)}).",
        )
    for idx, p in enumerate(prompts):
        if not isinstance(p, str) or not p.strip():
            raise AIClientError(
                "bad_request",
                f"prompts[{idx}]가 비어 있거나 문자열이 아닙니다.",
            )

    size, quality = get_plan_dimensions(plan_id)
    images: list[str] = []
    for idx, p in enumerate(prompts):
        if on_progress is not None:
            try:
                on_progress(idx, INITIAL_SET_SIZE)
            except Exception:
                logger.debug("on_progress callback raised, ignoring")
        responses = call_openai_image_generate(
            prompt=p, size=size, quality=quality, n=1,
        )
        if not responses:
            raise AIClientError(
                "server", "OpenAI에서 이미지를 받지 못했습니다.",
            )
        images.append(responses[0].content)

    return ImageSet(
        images=images,
        plan_id=normalize_plan_id(plan_id),
        size=size,
        quality=quality,
        mode="initial",
    )


def regenerate_set(
    prompt: str, plan_id: str, regen_used: int, n: int = INITIAL_SET_SIZE,
) -> ImageSet:
    """n장 재생성. regen_used가 무료 한도 이상이면 ImageQuotaExceeded.

    Args:
        regen_used: 이번 세션에서 이미 사용한 재생성 횟수 (이번 호출 미포함).
        n: 생성 장수 (1~5). 기본 5 (전체 재생성). 카드별 [↺]는 n=1.
           1장 재생성도 한도 1회 차감 — 사용자 경제성 명확.

    Raises:
        ImageQuotaExceeded: 무료 한도 초과.
        AIClientError: API 호출 실패.
    """
    if not prompt or not prompt.strip():
        raise AIClientError("bad_request", "이미지 프롬프트가 비어 있습니다.")
    if n < 1 or n > INITIAL_SET_SIZE:
        raise AIClientError("bad_request", f"n은 1~{INITIAL_SET_SIZE} 범위여야 합니다.")

    limits = get_plan_limits(plan_id)
    free_limit = limits["regen_free"]
    if regen_used >= free_limit:
        raise ImageQuotaExceeded(
            plan_id=normalize_plan_id(plan_id),
            kind="regen",
            used=regen_used,
            limit=free_limit,
        )

    size, quality = get_plan_dimensions(plan_id)
    responses = call_openai_image_generate(
        prompt=prompt, size=size, quality=quality, n=n
    )
    return _build_set(responses, plan_id, mode="regen")


def edit_image(
    image_bytes: bytes,
    prompt: str,
    plan_id: str,
    edit_used: int,
    mask_bytes: Optional[bytes] = None,
) -> ImageSet:
    """이미지 1장 부분 수정. edit_used가 무료 한도 이상이면 ImageQuotaExceeded.

    edit endpoint는 generations 대비 input image token 추가로 단가가 높지만,
    한 번에 만족도 높아 *세션 총비용* 35% ↓. 정책상 edit를 우선 권장.

    Args:
        image_bytes: 베이스 PNG.
        prompt: 수정 지시 ("신유혈 강조" 등).
        edit_used: 이번 세션 누적 edit 횟수.
        mask_bytes: 인페인팅 마스크 (선택).

    Raises:
        ImageQuotaExceeded: 무료 한도 초과.
        AIClientError: API 호출 실패.
    """
    if not image_bytes:
        raise AIClientError("bad_request", "수정할 이미지가 비어 있습니다.")
    if not prompt or not prompt.strip():
        raise AIClientError("bad_request", "수정 프롬프트가 비어 있습니다.")

    limits = get_plan_limits(plan_id)
    free_limit = limits["edit_free"]
    if edit_used >= free_limit:
        raise ImageQuotaExceeded(
            plan_id=normalize_plan_id(plan_id),
            kind="edit",
            used=edit_used,
            limit=free_limit,
        )

    size, quality = get_plan_dimensions(plan_id)
    responses = call_openai_image_edit(
        image_bytes=image_bytes,
        prompt=prompt,
        size=size,
        quality=quality,
        mask_bytes=mask_bytes,
        n=1,
    )
    return _build_set(responses, plan_id, mode="edit")


# ── 한도 정보 조회 (UI/API 응답용) ─────────────────────────


def get_quota_status(
    plan_id: str, regen_used: int, edit_used: int
) -> dict:
    """현재 세션 한도 상태. UI에 그대로 전달 가능한 형태."""
    limits = get_plan_limits(plan_id)
    norm = normalize_plan_id(plan_id)
    return {
        "plan_id": norm,
        "regen": {
            "used": regen_used,
            "free_limit": limits["regen_free"],
            "remaining": max(0, limits["regen_free"] - regen_used),
        },
        "edit": {
            "used": edit_used,
            "free_limit": limits["edit_free"],
            "remaining": max(0, limits["edit_free"] - edit_used),
        },
    }
