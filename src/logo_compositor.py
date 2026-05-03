"""
src/logo_compositor.py — 한의원 로고 합성 모듈 (콘텐츠 개인화 / 베타 게이트 ④).

기능:
1) process_logo(raw_bytes, out_path)
   - 업로드된 로고 이미지(PNG/JPG/WEBP) → rembg로 흰배경/단색 배경 자동 제거
   - 투명 PNG로 표준화하여 저장
   - 실패 시 LogoProcessError 발생 (라우터에서 400 응답)

2) composite_logo(image_path, logo_path, *, position, size_pct, opacity_pct)
   - 생성된 블로그 이미지에 로고를 후처리 합성
   - 위치(tl/tr/bl/br) + 크기(이미지 가로 대비 %) + 투명도(%) 인자
   - 이미지 파이프라인 마지막에 한 줄 호출 (image_generator.py)

설계 원칙:
- AI 모델에게 로고를 그리게 하지 않음 — 100% 왜곡 위험. 항상 PIL 후처리 합성.
- 흰배경 제거 실패해도 원본 그대로 저장(단순 로고는 자체가 PNG일 수 있음).
- 미설치 환경 대응: rembg/Pillow import 실패 시 명확한 에러 메시지.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


class LogoProcessError(Exception):
    """로고 처리 실패 — 사용자에게 노출되는 메시지."""


# 안전 마진 (이미지 가장자리에서 떨어진 픽셀 수, 이미지 가로의 2%)
_MARGIN_PCT = 2

# 로고 위치 키
PositionKey = Literal["tl", "tr", "bl", "br"]


def process_logo(raw_bytes: bytes, out_path: Path) -> None:
    """업로드된 로고 → 흰배경 제거 → 투명 PNG로 저장.

    raw_bytes: 클라이언트가 업로드한 원본 바이트 (PNG/JPG/WEBP)
    out_path: 저장 경로 (확장자 .png)

    실패 케이스:
    - 이미지 로딩 실패 (포맷 불량) → LogoProcessError
    - rembg 미설치 → 흰배경 제거 건너뛰고 원본 그대로 저장 (경고 로그)
    """
    try:
        from PIL import Image
    except ImportError as e:
        raise LogoProcessError("이미지 처리 라이브러리(Pillow)가 설치되지 않았습니다.") from e

    try:
        img = Image.open(io.BytesIO(raw_bytes))
    except Exception as e:
        raise LogoProcessError(f"이미지를 읽을 수 없습니다: {e}") from e

    # RGBA 변환 (알파 채널 보장)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # 흰배경 제거 시도 (rembg) — 실패 시 원본 유지
    try:
        from rembg import remove
        # rembg는 PIL Image도 받지만 bytes 입력이 더 안정적
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out_bytes = remove(buf.getvalue())
        img = Image.open(io.BytesIO(out_bytes)).convert("RGBA")
    except ImportError:
        logger.warning("rembg 미설치 — 흰배경 제거 건너뜀")
    except Exception as e:
        logger.warning(f"rembg 처리 실패, 원본 사용: {e}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)


def composite_logo(
    image_path: Path,
    logo_path: Path,
    *,
    position: PositionKey = "br",
    size_pct: int = 10,
    opacity_pct: int = 80,
) -> None:
    """생성된 블로그 이미지에 로고를 합성 (in-place 덮어쓰기).

    image_path: 합성할 블로그 이미지 (이 파일이 덮어써짐)
    logo_path: process_logo로 저장된 투명 PNG
    position: tl/tr/bl/br
    size_pct: 이미지 가로 대비 로고 가로 % (8~12 권장)
    opacity_pct: 로고 투명도 % (70~90 권장)

    실패는 silent — 로고 합성 실패가 블로그 이미지 자체를 망가뜨리면 안 됨.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 미설치 — 로고 합성 건너뜀")
        return

    if not image_path.exists():
        logger.warning(f"합성 대상 이미지 없음: {image_path}")
        return
    if not logo_path.exists():
        logger.warning(f"로고 파일 없음: {logo_path}")
        return

    try:
        base = Image.open(image_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"이미지 로딩 실패, 합성 건너뜀: {e}")
        return

    # 로고 크기 계산: 이미지 가로 × size_pct/100
    bw, bh = base.size
    target_w = max(1, int(bw * size_pct / 100))
    ratio = target_w / logo.size[0]
    target_h = max(1, int(logo.size[1] * ratio))
    logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # 투명도 적용 (alpha 채널 스케일링)
    if opacity_pct < 100:
        alpha = logo.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity_pct / 100))
        logo.putalpha(alpha)

    # 위치 계산 (안전 마진 포함)
    margin = max(8, int(bw * _MARGIN_PCT / 100))
    if position == "tl":
        x, y = margin, margin
    elif position == "tr":
        x, y = bw - target_w - margin, margin
    elif position == "bl":
        x, y = margin, bh - target_h - margin
    else:  # "br" 기본값
        x, y = bw - target_w - margin, bh - target_h - margin

    # 합성 — 알파 채널 사용
    base.alpha_composite(logo, (x, y))

    # 원본 포맷 유지 (PNG/JPG)
    fmt = (image_path.suffix.lower().lstrip(".") or "png").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt == "JPEG":
        # JPEG는 알파 미지원 → RGB 변환
        base = base.convert("RGB")
    base.save(image_path, format=fmt)


def composite_logo_b64(
    image_b64: str,
    logo_path: Path,
    *,
    position: PositionKey = "br",
    size_pct: int = 10,
    opacity_pct: int = 80,
) -> str:
    """base64 PNG에 로고 합성 → base64 PNG 반환 (메모리 처리).

    OpenAI gpt-image-2가 base64로 반환하는 이미지에 직접 적용.
    실패 시 원본 base64 그대로 반환 (silent fallback).
    """
    try:
        import base64
        from PIL import Image
    except ImportError:
        logger.warning("Pillow 미설치 — 로고 합성 건너뜀")
        return image_b64

    if not logo_path.exists():
        return image_b64

    try:
        raw = base64.b64decode(image_b64)
        base = Image.open(io.BytesIO(raw)).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as e:
        logger.warning(f"이미지 로딩 실패, 합성 건너뜀: {e}")
        return image_b64

    bw, bh = base.size
    target_w = max(1, int(bw * size_pct / 100))
    ratio = target_w / logo.size[0]
    target_h = max(1, int(logo.size[1] * ratio))
    logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

    if opacity_pct < 100:
        alpha = logo.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity_pct / 100))
        logo.putalpha(alpha)

    margin = max(8, int(bw * _MARGIN_PCT / 100))
    if position == "tl":
        x, y = margin, margin
    elif position == "tr":
        x, y = bw - target_w - margin, margin
    elif position == "bl":
        x, y = margin, bh - target_h - margin
    else:
        x, y = bw - target_w - margin, bh - target_h - margin

    base.alpha_composite(logo, (x, y))

    # OpenAI 원본은 PNG. PNG로 재인코딩.
    out_buf = io.BytesIO()
    base.save(out_buf, format="PNG", optimize=True)
    return base64.b64encode(out_buf.getvalue()).decode("ascii")


def apply_logo_to_b64_images(b64_images: list[str], clinic_id: int) -> list[str]:
    """한의원 로고가 설정돼 있으면 b64 이미지 리스트 전체에 합성.

    DB에서 logo_url + 위치/크기/투명도 조회. 미설정이면 원본 그대로 반환.
    이미지 파이프라인 끝에서 한 줄로 호출.

    실패는 silent — 로고 합성이 블로그 이미지 자체를 망가뜨리면 안 됨.
    """
    if not b64_images:
        return b64_images

    try:
        from db_manager import get_db
        with get_db() as conn:
            row = conn.execute(
                "SELECT logo_url, logo_position, logo_size_pct, logo_opacity_pct "
                "FROM clinics WHERE id = ?",
                (clinic_id,),
            ).fetchone()
    except Exception as e:
        logger.warning(f"로고 설정 조회 실패: {e}")
        return b64_images

    if not row or not row["logo_url"]:
        return b64_images

    # logo_url에서 실제 파일 경로 도출 (/static/uploads/logos/{id}.png?v=...)
    logo_path = Path(__file__).resolve().parent.parent / "static" / "uploads" / "logos" / f"{clinic_id}.png"
    if not logo_path.exists():
        return b64_images

    position = row["logo_position"] or "br"
    size_pct = row["logo_size_pct"] if row["logo_size_pct"] is not None else 10
    opacity_pct = row["logo_opacity_pct"] if row["logo_opacity_pct"] is not None else 80

    return [
        composite_logo_b64(
            img, logo_path,
            position=position, size_pct=size_pct, opacity_pct=opacity_pct,
        )
        for img in b64_images
    ]
