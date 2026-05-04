#!/usr/bin/env python3
"""
migrate_per_row_salt.py — K-9 (2026-05-04)

레거시 단일 salt(b'cligent_v1')로 암호화된 행을 per-row random salt 로 재암호화.

대상:
  1. server_secrets — salt IS NULL 인 행 전부
  2. clinics       — api_key_enc IS NOT NULL AND crypto_salt IS NULL

dry-run 기본. --apply 명시해야 실제 변경.
실행 전 자동으로 data/cligent.db.bak.<ts> 백업 (--apply 시).

사용법:
    python3 scripts/migrate_per_row_salt.py            # dry-run
    python3 scripts/migrate_per_row_salt.py --apply    # 실제 적용

롤백:
    1. 서버 정지
    2. cp data/cligent.db.bak.<ts> data/cligent.db
    3. (선택) ALTER TABLE 컬럼 추가는 무해 — 굳이 되돌릴 필요 없음
    4. 서버 재시작

검증:
    --verify (기존 마이그레이션 결과를 round-trip 으로 재검증, 변경 없음)
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=True)

from crypto_utils import LEGACY_SALT, _build_fernet  # noqa: E402

DB_PATH = ROOT / "data" / "cligent.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _backup_db() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = DB_PATH.with_suffix(f".db.bak.{ts}-pre-k9")
    shutil.copy2(DB_PATH, dst)
    return dst


def _re_encrypt(plain: str) -> tuple[str, bytes]:
    """평문을 새 random salt 로 재암호화."""
    new_salt = os.urandom(16)
    new_enc = _build_fernet(new_salt).encrypt(plain.encode()).decode()
    return new_enc, new_salt


def _decrypt_legacy(enc: str) -> str:
    return _build_fernet(LEGACY_SALT).decrypt(enc.encode()).decode()


def migrate_server_secrets(conn: sqlite3.Connection, *, apply: bool) -> tuple[int, int]:
    """returns (legacy_count, migrated_count)."""
    rows = conn.execute(
        "SELECT name, value_enc, salt FROM server_secrets"
    ).fetchall()
    legacy = [r for r in rows if not r["salt"]]
    migrated = 0
    for r in legacy:
        try:
            plain = _decrypt_legacy(r["value_enc"])
        except Exception as exc:
            print(f"  [server_secrets:{r['name']}] 레거시 복호화 실패: {exc}", file=sys.stderr)
            continue
        new_enc, new_salt = _re_encrypt(plain)
        # round-trip 검증
        rt = _build_fernet(new_salt).decrypt(new_enc.encode()).decode()
        if rt != plain:
            print(f"  [server_secrets:{r['name']}] round-trip 불일치 — 스킵", file=sys.stderr)
            continue
        if apply:
            conn.execute(
                "UPDATE server_secrets SET value_enc = ?, salt = ? WHERE name = ?",
                (new_enc, new_salt, r["name"]),
            )
            print(f"  [server_secrets:{r['name']}] 마이그레이션 완료 (salt {len(new_salt)}바이트)")
        else:
            print(f"  [server_secrets:{r['name']}] dry-run — 마이그레이션 대상")
        migrated += 1
    return len(legacy), migrated


def migrate_clinics(conn: sqlite3.Connection, *, apply: bool) -> tuple[int, int]:
    rows = conn.execute(
        "SELECT id, name, api_key_enc, crypto_salt FROM clinics "
        "WHERE api_key_enc IS NOT NULL AND api_key_enc != '' AND crypto_salt IS NULL"
    ).fetchall()
    migrated = 0
    for r in rows:
        try:
            plain = _decrypt_legacy(r["api_key_enc"])
        except Exception as exc:
            print(f"  [clinics:{r['id']} {r['name']}] 레거시 복호화 실패: {exc}", file=sys.stderr)
            continue
        new_enc, new_salt = _re_encrypt(plain)
        rt = _build_fernet(new_salt).decrypt(new_enc.encode()).decode()
        if rt != plain:
            print(f"  [clinics:{r['id']}] round-trip 불일치 — 스킵", file=sys.stderr)
            continue
        if apply:
            conn.execute(
                "UPDATE clinics SET api_key_enc = ?, crypto_salt = ? WHERE id = ?",
                (new_enc, new_salt, r["id"]),
            )
            print(f"  [clinics:{r['id']} {r['name']}] 마이그레이션 완료")
        else:
            print(f"  [clinics:{r['id']} {r['name']}] dry-run — 마이그레이션 대상")
        migrated += 1
    return len(rows), migrated


def verify(conn: sqlite3.Connection) -> int:
    """저장된 (enc, salt) 모두 복호화 가능한지 round-trip 확인. 0 = 모두 정상."""
    failures = 0
    for r in conn.execute("SELECT name, value_enc, salt FROM server_secrets"):
        try:
            salt = r["salt"] if r["salt"] else LEGACY_SALT
            _build_fernet(salt).decrypt(r["value_enc"].encode())
        except Exception as exc:
            print(f"  [server_secrets:{r['name']}] 복호화 실패: {exc}", file=sys.stderr)
            failures += 1
    for r in conn.execute(
        "SELECT id, api_key_enc, crypto_salt FROM clinics "
        "WHERE api_key_enc IS NOT NULL AND api_key_enc != ''"
    ):
        try:
            salt = r["crypto_salt"] if r["crypto_salt"] else LEGACY_SALT
            _build_fernet(salt).decrypt(r["api_key_enc"].encode())
        except Exception as exc:
            print(f"  [clinics:{r['id']}] 복호화 실패: {exc}", file=sys.stderr)
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="K-9 per-row salt 마이그레이션")
    parser.add_argument("--apply", action="store_true", help="실제 DB 변경 (기본은 dry-run)")
    parser.add_argument("--verify", action="store_true", help="현재 상태 검증만 수행")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"DB 없음: {DB_PATH}", file=sys.stderr)
        return 2
    if not os.getenv("SECRET_KEY"):
        print("SECRET_KEY 환경변수가 필요합니다.", file=sys.stderr)
        return 2

    conn = _connect()

    if args.verify:
        print("[verify] 모든 행 round-trip 검증 중…")
        failures = verify(conn)
        print(f"[verify] 실패 {failures}건")
        return 0 if failures == 0 else 1

    if args.apply:
        backup = _backup_db()
        print(f"[backup] {backup}")

    print(f"[mode] {'APPLY' if args.apply else 'DRY-RUN'}")
    print("[server_secrets]")
    ss_legacy, ss_migrated = migrate_server_secrets(conn, apply=args.apply)
    print(f"  레거시 {ss_legacy}건 / 처리 {ss_migrated}건")
    print("[clinics]")
    c_legacy, c_migrated = migrate_clinics(conn, apply=args.apply)
    print(f"  레거시 {c_legacy}건 / 처리 {c_migrated}건")

    if args.apply:
        conn.commit()
        print("\n[verify] 마이그레이션 후 round-trip 재검증…")
        failures = verify(conn)
        if failures == 0:
            print("[verify] 모두 정상 — 마이그레이션 완료")
            return 0
        else:
            print(f"[verify] 실패 {failures}건 — .db.bak 으로 롤백 권장", file=sys.stderr)
            return 1

    conn.close()
    print("\n[dry-run] 변경 없음. 실제 적용은 --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
