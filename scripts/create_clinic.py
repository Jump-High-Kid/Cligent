#!/usr/bin/env python3
"""
scripts/create_clinic.py — 신규 한의원 생성 CLI

trial_expires_at(14일)을 자동으로 설정한다.
프로덕션 배포 후 최초 한의원 등록 또는 베타 참가자 초대 시 사용.

사용법:
    python3 scripts/create_clinic.py --name "강남 한의원"
    python3 scripts/create_clinic.py --name "분당 한의원" --slots 10

출력:
    clinic_id=3  trial_expires_at=2026-05-06T00:00:00+00:00
"""

import argparse
import sys
from pathlib import Path

# src/ 폴더를 파이썬 경로에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from db_manager import create_clinic, init_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Cligent 신규 한의원 생성")
    parser.add_argument("--name", required=True, help="한의원 이름")
    parser.add_argument("--slots", type=int, default=5, help="최대 직원 슬롯 수 (기본: 5)")
    args = parser.parse_args()

    name = args.name.strip()
    if not name:
        print("오류: --name 값이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    init_db()
    clinic_id = create_clinic(name, args.slots)

    from datetime import datetime, timedelta, timezone
    trial_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    print(f"clinic_id={clinic_id}  name={name!r}  max_slots={args.slots}")
    print(f"trial_expires_at={trial_expires_at}  (DB에 저장된 값)")
    print()
    print("다음 단계: 대표원장 계정 생성 후 초대 링크를 카톡으로 전달")


if __name__ == "__main__":
    main()
