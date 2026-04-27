#!/usr/bin/env python3
"""
reset_password.py — 사용자 비밀번호 재설정 도구

사용:
  python3 scripts/reset_password.py <email>

대표원장(chief_director) 비밀번호 분실 등 UI 흐름이 없는 경우 사용.
SSH 접근 가능자만 실행 가능 (서버 셸 권한 = 신뢰 경계).
"""
from __future__ import annotations

import getpass
import sqlite3
import sys
from pathlib import Path

import bcrypt


def main() -> int:
    if len(sys.argv) != 2:
        print("사용: python3 scripts/reset_password.py <email>")
        return 1

    email = sys.argv[1].strip()
    db_path = Path(__file__).parent.parent / "data" / "cligent.db"

    if not db_path.exists():
        print(f"DB 파일 없음: {db_path}")
        return 1

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT id, role, clinic_id, is_active FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        if not row:
            print(f"계정 없음: {email}")
            return 1
        print(f"대상: id={row[0]} role={row[1]} clinic_id={row[2]} active={row[3]}")

        pw = getpass.getpass("새 비밀번호 (8자 이상, 입력 시 안 보임): ").strip()
        if len(pw) < 8:
            print("너무 짧음 (8자 이상 필요)")
            return 1
        pw2 = getpass.getpass("한 번 더 입력: ").strip()
        if pw != pw2:
            print("일치하지 않음")
            return 1

        h = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        conn.execute(
            "UPDATE users SET hashed_password = ?, must_change_pw = 0 WHERE email = ?",
            (h, email),
        )
        conn.commit()

    print(f"OK — {email} 비밀번호 재설정 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
