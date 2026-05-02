"""
db_manager.py — SQLite 데이터베이스 초기화 및 연결 관리

테이블 (대표):
  clinics, users, invites              — 인증·온보딩
  plans, usage_logs, subscriptions     — 결제·플랜
  feedback, beta_applicants, ...       — 운영
  image_sessions                       — 1편 블로그당 1세션, regen/edit 카운트
  blog_chat_sessions                   — chat-driven UX state (서버 보유)
  image_jobs                           — 이미지 생성 큐 (M1+ worker pool)

trial_expires_at 정책:
  - create_clinic() 호출 시 1회만 NOW + 14일로 설정
  - 이미 값이 있으면 절대 덮어쓰지 않음 (trial abuse 방어)
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "cligent.db"


def init_db() -> None:
    """서버 시작 시 호출 — 테이블이 없으면 생성, 컬럼 마이그레이션 포함"""
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

            -- 플랜 정의 테이블
            CREATE TABLE IF NOT EXISTS plans (
                id                  TEXT    PRIMARY KEY,
                name                TEXT    NOT NULL,
                monthly_blog_limit  INTEGER,           -- NULL = 무제한
                price_krw           INTEGER NOT NULL DEFAULT 0,
                features            TEXT               -- JSON 문자열
            );

            -- 사용량 로그
            CREATE TABLE IF NOT EXISTS usage_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
                feature     TEXT    NOT NULL,
                used_at     TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                metadata    TEXT                       -- JSON 문자열
            );

            -- 성능: plan_guard의 월별 집계 쿼리용 복합 인덱스
            CREATE INDEX IF NOT EXISTS idx_usage_logs_clinic_month
                ON usage_logs (clinic_id, feature, used_at);

            -- 피드백 / 오류 신고
            CREATE TABLE IF NOT EXISTS feedback (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id   INTEGER REFERENCES clinics(id),
                user_id     INTEGER REFERENCES users(id),
                page        TEXT    NOT NULL DEFAULT 'unknown',
                message     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_feedback_clinic ON feedback(clinic_id, created_at);

            -- 구독 이력 (Phase 1: 빈 테이블, Phase 3에서 데이터 삽입)
            -- CS 디버깅: 언제 어떤 결제로 플랜이 바뀌었는지 추적
            CREATE TABLE IF NOT EXISTS subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id   INTEGER NOT NULL REFERENCES clinics(id),
                plan_id     TEXT    NOT NULL REFERENCES plans(id),
                status      TEXT    NOT NULL DEFAULT 'active',
                starts_at   TEXT    NOT NULL,
                ends_at     TEXT,
                payment_id  TEXT,                      -- 포트원 payment_id (idempotency 키)
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );

            -- 베타/일반 신청자 (모집 시스템)
            -- status 값: pending / invited / registered / rejected / expired
            CREATE TABLE IF NOT EXISTS beta_applicants (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                clinic_name  TEXT    NOT NULL,
                phone        TEXT,
                email        TEXT    NOT NULL,
                note         TEXT,                                 -- 신청자 본인이 쓴 메시지
                applied_at   TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                invited_at   TEXT,
                clicked_at   TEXT,
                invite_token TEXT    UNIQUE,                       -- invites.token 연결 (E5 clicked_at 추적용)
                status       TEXT    NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_beta_applicants_status ON beta_applicants(status);
            CREATE INDEX IF NOT EXISTS idx_beta_applicants_email  ON beta_applicants(email);

            -- 로그인 이력 (PIPA 90일 보존, 통신비밀보호법 권고 기준)
            -- 성공/실패 모두 기록 → brute force 감지·계정 탈취 추적
            CREATE TABLE IF NOT EXISTS login_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER REFERENCES users(id),    -- 실패 시 NULL 가능 (이메일 매칭 실패)
                email           TEXT,                            -- 시도된 이메일 (raw, 90일 후 자동 삭제)
                clinic_id       INTEGER REFERENCES clinics(id),  -- 성공 시
                ip              TEXT,
                user_agent      TEXT,
                success         INTEGER NOT NULL DEFAULT 0,
                failure_reason  TEXT,                            -- user_not_found / invalid_credentials / disabled / password_not_set / other
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_login_history_user_at
                ON login_history(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_login_history_at
                ON login_history(created_at);
            CREATE INDEX IF NOT EXISTS idx_login_history_ip_at
                ON login_history(ip, created_at);

            -- 신청자 이메일 발송 이력 (E1~E4, 거절 등 모든 발송 기록)
            CREATE TABLE IF NOT EXISTS applicant_emails (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_id INTEGER NOT NULL REFERENCES beta_applicants(id) ON DELETE CASCADE,
                email_type   TEXT    NOT NULL,                     -- apply_confirm / admin_notify / invite / reminder / rejection
                sent_at      TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                success      INTEGER NOT NULL DEFAULT 0,
                error_msg    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_applicant_emails_applicant
                ON applicant_emails(applicant_id, sent_at DESC);

            -- 공지사항 게시판
            CREATE TABLE IF NOT EXISTS announcements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                body_md     TEXT    NOT NULL,
                category    TEXT    NOT NULL DEFAULT 'general',  -- 'update' / 'maintenance' / 'general'
                is_pinned   INTEGER NOT NULL DEFAULT 0,
                author      TEXT    NOT NULL DEFAULT 'Cligent',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_announcements_pinned_created
                ON announcements(is_pinned DESC, created_at DESC);

            -- 사용자별 읽음 추적 (안 읽은 공지 뱃지용)
            CREATE TABLE IF NOT EXISTS announcement_reads (
                user_id          INTEGER NOT NULL REFERENCES users(id),
                announcement_id  INTEGER NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
                read_at          TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                PRIMARY KEY (user_id, announcement_id)
            );

            -- 공지 첨부 이미지 (admin 업로드)
            CREATE TABLE IF NOT EXISTS announcement_attachments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                announcement_id  INTEGER REFERENCES announcements(id) ON DELETE CASCADE,
                filename         TEXT    NOT NULL,
                url              TEXT    NOT NULL,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );

            -- 서버 전역 비밀 (관리자 전용, Fernet 암호화)
            -- 베타: BYOAI 비활성, 모든 사용자가 같은 OpenAI 키 사용
            -- 미래 (Naver/Google 등) 서버 비밀도 같은 테이블 재사용
            CREATE TABLE IF NOT EXISTS server_secrets (
                name              TEXT    PRIMARY KEY,                 -- e.g., 'openai_api_key'
                value_enc         TEXT    NOT NULL,                    -- Fernet 암호화 (SECRET_KEY 파생)
                updated_at        TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                updated_by_user_id INTEGER REFERENCES users(id)
            );

            -- 이미지 세션 (Phase 4, 2026-04-30)
            -- 1편 블로그당 1세션. initial 5장 생성 시 만들어지고, regen/edit 횟수를 카운트.
            -- 플랜별 무료 한도(Standard 1+2 / Pro 2+4)는 image_generator 모듈에서 검사.
            CREATE TABLE IF NOT EXISTS image_sessions (
                session_id        TEXT    PRIMARY KEY,                 -- UUID4
                clinic_id         INTEGER NOT NULL,
                user_id           INTEGER,
                blog_keyword      TEXT,
                plan_id_at_start  TEXT,                                -- 세션 생성 시점 플랜 (기록용)
                regen_count       INTEGER NOT NULL DEFAULT 0,
                edit_count        INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                last_active_at    TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_image_sessions_clinic
              ON image_sessions(clinic_id);
            CREATE INDEX IF NOT EXISTS idx_image_sessions_created
              ON image_sessions(created_at);

            -- 블로그 챗 세션 (Step 1, v10 plan E1 — 서버 state 보유)
            -- 클라는 session_id만 보유, 서버가 state 100% 보유 → 끊김 복구·다중 탭 가드
            -- in-memory LRU + DB 백업 (TTL 24h, 미완료 세션 일일 정리)
            -- stage 값: topic / length / questions / seo / generating / image / feedback / done
            CREATE TABLE IF NOT EXISTS blog_chat_sessions (
                session_id      TEXT    PRIMARY KEY,                 -- UUID4
                clinic_id       INTEGER NOT NULL REFERENCES clinics(id),
                user_id         INTEGER REFERENCES users(id),
                stage           TEXT    NOT NULL DEFAULT 'topic',
                state_json      TEXT    NOT NULL,                    -- 누적 입력·옵션·본문·이미지 메타
                created_at      TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                last_active_at  TEXT    NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_blog_chat_sessions_clinic
              ON blog_chat_sessions(clinic_id, last_active_at DESC);
            CREATE INDEX IF NOT EXISTS idx_blog_chat_sessions_active
              ON blog_chat_sessions(last_active_at);

            -- 이미지 생성 작업 큐 (Step 1, v10 plan E3 + worker pool)
            -- M0(5인): 동기 호출, 큐 미사용 (job 행은 추적용으로만 기록)
            -- M1(25인): worker pool 동적 (concurrency = MIN(IPM × 0.6, active_sessions))
            -- M2(50인): + Batch API 24h 모드
            -- job_type: initial / regen / edit
            -- status: queued / running / done / failed
            CREATE TABLE IF NOT EXISTS image_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT    NOT NULL,                    -- blog_chat_sessions.session_id
                clinic_id       INTEGER NOT NULL REFERENCES clinics(id),
                user_id         INTEGER REFERENCES users(id),
                job_type        TEXT    NOT NULL,
                payload_json    TEXT    NOT NULL,
                status          TEXT    NOT NULL DEFAULT 'queued',
                submitted_at    TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
                started_at      TEXT,
                completed_at    TEXT,
                result_json     TEXT,
                error_message   TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_image_jobs_status_time
              ON image_jobs(status, submitted_at);
            CREATE INDEX IF NOT EXISTS idx_image_jobs_clinic
              ON image_jobs(clinic_id);
            CREATE INDEX IF NOT EXISTS idx_image_jobs_session
              ON image_jobs(session_id);
        """)
        # feedback 컬럼 마이그레이션
        # - viewed_at: admin 뷰어 미확인/확인 토글
        # - context_json: blog_chat 발생 단계·session_id·error 등 (어드민 펼침용)
        existing_fb = {row[1] for row in conn.execute("PRAGMA table_info(feedback)")}
        if "viewed_at" not in existing_fb:
            conn.execute("ALTER TABLE feedback ADD COLUMN viewed_at TEXT")
        if "context_json" not in existing_fb:
            conn.execute("ALTER TABLE feedback ADD COLUMN context_json TEXT")

        # beta_applicants 컬럼 마이그레이션 (라이프사이클 강화 2026-04-29)
        existing_ba = {row[1] for row in conn.execute("PRAGMA table_info(beta_applicants)")}
        for col, definition in [
            ("application_type",        "TEXT DEFAULT 'beta'"),
            ("marketing_consent",       "INTEGER DEFAULT 0"),
            ("consented_terms_version", "TEXT"),
            ("ip_address",              "TEXT"),
            ("user_agent",              "TEXT"),
            ("rejection_reason",        "TEXT"),
            ("admin_notes",             "TEXT"),
            ("admin_tags",              "TEXT"),
            ("expires_at",              "TEXT"),
        ]:
            if col not in existing_ba:
                conn.execute(f"ALTER TABLE beta_applicants ADD COLUMN {col} {definition}")
        # 기존 행 expires_at 백필 (applied_at + 30일)
        conn.execute(
            "UPDATE beta_applicants SET expires_at = datetime(applied_at, '+30 days') "
            "WHERE expires_at IS NULL"
        )

        # clinics 컬럼 마이그레이션 (기존 DB 대응)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(clinics)")}
        for col, definition in [
            ("phone",              "TEXT"),
            ("address",            "TEXT"),
            ("specialty",          "TEXT"),
            ("hours",              "TEXT"),       # JSON 문자열로 저장
            ("intro",              "TEXT"),
            ("model",              "TEXT"),       # 사용 AI 모델
            ("monthly_budget_krw", "INTEGER"),    # 월 예산 (원)
            ("api_key_enc",        "TEXT"),       # Fernet 암호화된 API 키
            # 플랜 관련 컬럼
            ("plan_id",            "TEXT DEFAULT 'free'"),
            ("plan_expires_at",    "TEXT"),       # ISO8601. NULL = 무료 플랜
            # trial_expires_at: signup 시 1회만 설정, 절대 재설정 없음 (trial abuse 방어)
            ("trial_expires_at",   "TEXT"),       # ISO8601. NULL = 체험 미사용
            ("payment_status",       "TEXT"),          # 'pending': 결제 성공 but DB 저장 실패
            # API 키 온보딩 추적
            ("api_key_configured",   "INTEGER DEFAULT 0"),  # 1 = API 키 등록 완료
            ("onboarding_started_at","TEXT"),               # 온보딩 위자드 첫 표시 시각 (ISO8601)
            ("first_blog_at",        "TEXT"),               # 첫 블로그 생성 완료 시각 (ISO8601)
            ("blog_features",        "TEXT"),               # 클리닉 특징·장점 (블로그 생성 시 자동 반영)
            # 어드민 클리닉 플래그 — 일반 직원이 합류하지 못하도록 차단
            ("is_admin_clinic",      "INTEGER DEFAULT 0"),
            ("naver_blog_id",        "TEXT"),               # 네이버 블로그 아이디 (발행 확인용)
            # Step 1 Phase 1F — 블로그 챗 Cohort 1 게이트
            # 0 = 미노출(/blog 폼만) / 1 = /blog/chat 진입 허용
            ("chat_beta_enabled",    "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE clinics ADD COLUMN {col} {definition}")

        # 기존 API 키 보유 한의원은 api_key_configured=1로 초기화 (마이그레이션)
        conn.execute(
            "UPDATE clinics SET api_key_configured = 1 "
            "WHERE api_key_enc IS NOT NULL AND api_key_enc != '' AND api_key_configured = 0"
        )

        # plans 시드 데이터 (없을 때만 삽입)
        plan_count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        if plan_count == 0:
            conn.executemany(
                "INSERT INTO plans (id, name, monthly_blog_limit, price_krw, features) VALUES (?, ?, ?, ?, ?)",
                [
                    ("free",     "무료",     3,    0,      '{"blog": true, "agent_chat": true}'),
                    ("standard", "스탠다드", None, 29000,  '{"blog": true, "agent_chat": true, "blog_custom": true}'),
                    ("pro",      "프로",     None, 59000,  '{"blog": true, "agent_chat": true, "blog_custom": true, "youtube": true, "legal": true, "tax": true}'),
                ],
            )


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


# ── 클리닉 생성 (프로덕션 & 개발 공용) ──────────────────────────────

def create_clinic(name: str, max_slots: int = 5) -> int:
    """
    신규 한의원 생성 후 clinic_id 반환.

    - trial_expires_at = NOW() + 14일 을 1회만 설정 (trial abuse 방어)
    - 이미 trial_expires_at이 있는 행에는 절대 덮어쓰지 않음
    """
    # 체험 기간 만료 시각: 현재 UTC + 14일
    trial_expires_at = (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO clinics (name, max_slots, trial_expires_at)
            VALUES (?, ?, ?)
            """,
            (name, max_slots, trial_expires_at),
        )
        return cur.lastrowid


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


_FIRST_ANNOUNCEMENT_TITLE = "Cligent 업데이트 — 블로그·이미지 품질 개선"
_FIRST_ANNOUNCEMENT_BODY = """안녕하세요, Cligent입니다.

이번 업데이트에서는 블로그·이미지 품질과 모바일 사용성을 개선했습니다.

## 주요 개선

- **블로그 글 품질 향상** — 도입부 다양화, 한의학 참고 문헌 자동 인용
- **이미지 프롬프트 정확도 향상** — 한의 진료실 분위기, 해부학 정확성 강화
- **네이버 발행 확인** — 글 발행 후 검색 노출 자동 알림
- **모바일 사용성 개선** — 하단 메뉴 정리, 안내 배너 개선
- **공지사항 게시판 신설** — 업데이트 소식을 한곳에서 확인

---

문의·피드백은 페이지 상단 피드백 바를 이용해주세요.
"""


def seed_first_announcement() -> None:
    """공지 테이블이 비어 있을 때 첫 업데이트 노트 1건 삽입."""
    with get_db() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0]
        if cnt > 0:
            return
        conn.execute(
            "INSERT INTO announcements (title, body_md, category, is_pinned, author) "
            "VALUES (?, ?, 'update', 1, '원장')",
            (_FIRST_ANNOUNCEMENT_TITLE, _FIRST_ANNOUNCEMENT_BODY),
        )


def seed_demo_owner(
    clinic_id: int,
    email: str = "owner@cligent.dev",
    password: str = "Demo1234!",
) -> None:
    """
    개발/테스트용 — owner 계정이 없을 때 대표원장 계정 자동 생성
    비밀번호: Demo1234! (개발 환경 전용)
    """
    from passlib.context import CryptContext
    pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    with get_db() as conn:
        exists = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)
        ).fetchone()
        if exists:
            return
        hashed = pwd_ctx.hash(password)
        conn.execute(
            "INSERT INTO users (clinic_id, email, hashed_password, role, is_active, must_change_pw) "
            "VALUES (?, ?, ?, 'chief_director', 1, 0)",
            (clinic_id, email, hashed),
        )
