"""
db_manager.py — SQLite 데이터베이스 초기화 및 연결 관리

테이블:
  clinics  — 한의원 정보 + 슬롯 한도
  users    — 로그인 사용자 (역할 포함)
  invites  — 72시간 유효 1회용 초대 토큰
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "cligent.db"


def init_db() -> None:
    """서버 시작 시 호출 — 테이블이 없으면 생성"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS clinics (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                max_slots  INTEGER NOT NULL DEFAULT 5,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id        INTEGER NOT NULL REFERENCES clinics(id),
                email            TEXT    NOT NULL UNIQUE,
                hashed_password  TEXT,
                role             TEXT    NOT NULL DEFAULT 'team_member',
                is_active        INTEGER NOT NULL DEFAULT 1,
                must_change_pw   INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS invites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
                email       TEXT    NOT NULL,
                role        TEXT    NOT NULL DEFAULT 'team_member',
                token       TEXT    NOT NULL UNIQUE,
                expires_at  TEXT    NOT NULL,
                used_at     TEXT,
                created_by  INTEGER NOT NULL REFERENCES users(id),
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_users_email    ON users(email);
            CREATE INDEX IF NOT EXISTS idx_invites_token  ON invites(token);
            CREATE INDEX IF NOT EXISTS idx_invites_clinic ON invites(clinic_id);
        """)


@contextmanager
def get_db():
    """DB 커넥션 컨텍스트 매니저 — with get_db() as conn: 형태로 사용"""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 딕셔너리처럼 접근 가능
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── 개발용 시드 헬퍼 ──────────────────────────────────────────────

def seed_demo_clinic(name: str = "데모 한의원", max_slots: int = 10) -> int:
    """
    개발/테스트용 — 한의원이 하나도 없을 때 데모 클리닉 생성 후 clinic_id 반환
    프로덕션에서는 관리자 API로 대체
    """
    with get_db() as conn:
        row = conn.execute("SELECT id FROM clinics LIMIT 1").fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO clinics (name, max_slots) VALUES (?, ?)",
            (name, max_slots),
        )
        return cur.lastrowid
