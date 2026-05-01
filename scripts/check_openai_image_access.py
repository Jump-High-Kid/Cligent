"""OpenAI 이미지 모델 접근 진단.

어드민 등록 OpenAI 키로 models.list() 호출 → gpt-image-2 노출 여부 확인.
실행: python3 scripts/check_openai_image_access.py
"""
import sys
from pathlib import Path

# src/ 경로 추가 (run.py와 동일 패턴)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from secret_manager import get_server_secret  # noqa: E402
from openai import OpenAI  # noqa: E402


def main() -> int:
    key = get_server_secret("openai_api_key")
    if not key:
        print("[FAIL] OpenAI 키 미등록 — /admin/settings에서 등록 후 재시도")
        return 1

    print(f"[OK]  키 로드 성공 (length={len(key)}, 앞 8자={key[:8]})")
    client = OpenAI(api_key=key)

    try:
        models = client.models.list().data
    except Exception as exc:
        print(f"[FAIL] models.list() 호출 실패: {exc}")
        return 1

    ids = sorted(m.id for m in models)
    image_ids = [m for m in ids if "image" in m or "dall" in m]

    print(f"\n총 노출 모델: {len(ids)}개")
    print("이미지 관련 모델:")
    for m in image_ids:
        print(f"  - {m}")

    has_g2 = "gpt-image-2" in ids
    has_g1 = "gpt-image-1" in ids
    has_dalle2 = "dall-e-2" in ids

    print("\n진단:")
    print(f"  gpt-image-2: {'노출됨' if has_g2 else '미노출'}")
    print(f"  gpt-image-1: {'노출됨' if has_g1 else '미노출'}")
    print(f"  dall-e-2:    {'노출됨' if has_dalle2 else '미노출'}")

    print("\n결론:")
    if has_g2:
        print("  → 모델 노출 OK. edits 거부는 OpenAI 백엔드의 별도 게이트(verification·tier·정책).")
        print("    OpenAI 고객센터(help.openai.com)에 'gpt-image-2 edits API access' 요청 권장.")
    else:
        print("  → 이 계정에 gpt-image-2가 노출되지 않음.")
        print("    원인 후보: Organization Verification 미반영 / 다른 조직 키 / verification 풀림.")
        print("    조치: platform.openai.com → Settings → Organization → General에서 'Verified' 배지 재확인.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
