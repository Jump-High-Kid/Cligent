# Desktop-Ready 웹앱 준비 설계 (Cligent)

**날짜:** 2026-04-16
**목표:** 현재 FastAPI + Vanilla JS 웹앱을 나중에 Electron 기반 Windows 데스크탑 앱으로 전환할 때 최소한의 공사로 가능하도록 지금부터 구조를 준비한다.

---

## 배경 및 전제

- 현재: FastAPI(Python) + Vanilla JS 웹앱, Anthropic API 호출 포함
- 목표 형태: Windows 설치형 데스크탑 앱 (.exe)
- 역할: 원장(admin) / 직원(staff) / 공통(common) 3단계 접근 제어
- 데이터 공유: 전자 차트 추출, 엑셀 업로드, 수동 입력 → 원장·직원이 함께 열람
- 데이터 저장소: Supabase(클라우드) — 인터넷 연결 필요, 무료 티어 활용
- 선택한 방식: **Electron 래퍼 + Supabase (A안)**

---

## 준비 단계 4가지

### 1단계: API 접두사 분리 (백엔드/프론트엔드 완전 분리)

**현재 문제**
- FastAPI가 `GET /` 에서 HTML도 서빙 중
- Electron 전환 시 프론트엔드는 HTML 파일을 직접 로드하고, FastAPI는 API만 담당해야 함

**변경 사항**
- 모든 API 엔드포인트에 `/api/` 접두사 적용
- 프론트엔드의 fetch URL을 환경변수 기반으로 관리

```
변경 전: POST /questions, POST /generate
변경 후: POST /api/questions, POST /api/generate
```

**Electron 최종 구조**
```
Electron
├── 프론트엔드: HTML/JS 파일을 직접 로드 (file:// 또는 localhost)
└── 백엔드: FastAPI를 subprocess로 실행 → localhost:8000 에서 API만 제공
```

**완료 기준**
- [ ] 모든 엔드포인트 `/api/` 접두사 적용
- [ ] 프론트엔드 fetch URL 환경변수(`APP_API_URL`, 기본값 `http://localhost:8000`)로 관리
- [ ] `GET /` 헬스체크 유지 `{"status": "ok"}` — HTML 서빙 제거, `/api/health`는 별도 추가 불필요
- [ ] FastAPI CORS 미들웨어 추가: `allow_origins=["null", "http://localhost"]` — Electron `file://` origin은 `"null"`로 전달됨
- [ ] `APP_API_URL` 환경변수 Vanilla JS 주입 방법: FastAPI가 `/config.js` 엔드포인트를 통해 `window.APP_CONFIG = {apiUrl: "..."}` 반환, HTML에서 `<script src="/config.js">` 로드

---

### 2단계: Repository 패턴 도입 (DB 교체 비용 최소화)

**현재 문제**
- 생성된 블로그, 입력 데이터 등을 저장하는 DB 레이어가 없음
- Electron + 공유 데이터 필요 시 비즈니스 로직과 DB 코드가 엉킬 위험

**구조**
```
src/
├── repositories/
│   ├── base.py          # 추상 인터페이스 (이번 스프린트)
│   ├── sqlite_repo.py   # 로컬 개발 / 단독 사용 (이번 스프린트)
│   └── supabase_repo.py # Electron 전환 스프린트에서 추가 (미구현)
├── models/
│   ├── blog.py          # Blog Pydantic 모델 (이번 스프린트)
│   ├── conversation.py  # ConversationSession 모델 (이번 스프린트)
│   └── image_prompt.py  # ImagePrompt 모델 (이번 스프린트)
```

```python
# base.py — 모든 데이터 타입 커버
class BlogRepository(ABC):
    def save(self, blog: Blog) -> Blog: ...
    def list_all(self) -> list[Blog]: ...
    def get_by_id(self, id: str) -> Blog | None: ...
    def delete(self, id: str) -> None: ...  # 소프트 딜리트

class ConversationRepository(ABC):
    def save(self, session: ConversationSession) -> ConversationSession: ...
    def get_by_id(self, id: str) -> ConversationSession | None: ...

class ImagePromptRepository(ABC):
    def save(self, prompt: ImagePrompt) -> ImagePrompt: ...
    def list_by_blog(self, blog_id: str) -> list[ImagePrompt]: ...
```

**모델 정의 필요 (구현 전 선행)**
- `Blog`, `ConversationSession`, `ImagePrompt` 데이터클래스/Pydantic 모델 먼저 정의

**환경변수로 저장소 선택**
```
DB_BACKEND=sqlite   # 로컬 개발
DB_BACKEND=supabase # Electron + 공유 환경
```

**완료 기준**
- [ ] 3개 모델 (`Blog`, `ConversationSession`, `ImagePrompt`) Pydantic 모델 정의
- [ ] 3개 Repository 추상 클래스 정의
- [ ] `SQLiteRepository` 구현체 완성 (3개 모두)
- [ ] 기존 비즈니스 로직이 추상 인터페이스만 참조
- [ ] SQLiteRepository 단위 테스트 (save/list/get/delete)
- [ ] 에러 전파 명시: DB 실패 시 예외를 호출자에게 전달 (묵살 금지)

---

### 3단계: 역할 구조 설계 (인증 공사 최소화)

**현재 문제**
- 인증 없음, 역할 개념 없음
- 나중에 역할 추가 시 엔드포인트 전체 수정 필요

**지금 할 것: 역할 Enum + 엔드포인트 태그만 심기**
```python
class Role(str, Enum):
    ADMIN = "admin"    # 원장
    STAFF = "staff"    # 직원
    COMMON = "common"  # 공통

# 엔드포인트에 태그 명시
@app.post("/api/blog/generate", tags=["common"])
@app.get("/api/reports/monthly", tags=["admin"])
@app.get("/api/schedule/today", tags=["staff"])
```

**단계별 전략**
| 단계 | 시점 | 내용 |
|------|------|------|
| 1 | 지금 | 역할 Enum + 태그 |
| 2 | 웹앱 완성 후 | 간단한 API 키 방식 원장/직원 구분 |
| 3 | Electron 전환 시 | Supabase Auth 로그인 시스템 연결 |

**완료 기준**
- [ ] `Role` Enum 정의 (`src/models/role.py`)
- [ ] 모든 엔드포인트에 역할 태그 명시
- [ ] 역할별 접근 제어 미들웨어 stub 작성 (빈 함수로도 OK)

---

### 4단계: Electron 번들링 대비 (경로/포트/프로세스 안정화)

**현재 문제**
- 상대경로 사용 (`"prompts/blog.txt"`) → Electron 실행 시 경로 깨짐
- 포트 하드코딩 → 충돌 위험
- 서버 종료 시그널 처리 없음 → Electron이 프로세스를 깔끔하게 종료 못함

**변경 사항**

```python
# ① 절대경로 통일
BASE_DIR = Path(__file__).parent.parent
PROMPT_DIR = BASE_DIR / "prompts"
CONFIG_PATH = BASE_DIR / "config.yaml"

# ② 포트 환경변수
PORT = int(os.getenv("APP_PORT", 8000))

# ③ 종료 처리 (run.py) — atexit가 주 수단, Windows에서도 안정적
import signal, sys, atexit

def cleanup():
    """DB 연결, 임시 파일 등 정리"""
    pass  # 2단계 DB 도입 후 채울 것

atexit.register(cleanup)

def shutdown(sig, frame):
    sys.exit(0)  # atexit 자동 호출됨

signal.signal(signal.SIGTERM, shutdown)  # Linux/macOS only (Windows 미지원)
signal.signal(signal.SIGINT, shutdown)   # Ctrl+C

# ④ APP_ENV로 reload + host 제어
is_prod = os.getenv("APP_ENV") == "production"
host = "127.0.0.1" if is_prod else "0.0.0.0"  # 프로덕션은 로컬만 바인딩
uvicorn.run("main:app", host=host, port=PORT, reload=not is_prod)
```

**Electron 전환 시 추가 작업 (지금은 X)**
- `pyinstaller`로 FastAPI 앱 → `.exe` 패키징
- Electron main process에서 `.exe` subprocess 실행
- `electron-builder`로 Windows 설치 파일 생성

**완료 기준**
- [ ] 모든 파일 경로 `BASE_DIR` 기준 절대경로로 통일 (config_loader.py는 이미 완료)
- [ ] `APP_PORT` 환경변수 지원
- [ ] `SIGTERM` / `SIGINT` 시그널 핸들러 추가
- [ ] `atexit.register(cleanup)` 추가 — Windows 호환 종료 처리
- [ ] `APP_ENV=production` 일 때 `reload=False` — PyInstaller 번들링 호환

---

## 전체 요약

| 단계 | 작업 | 난이도 | 데스크탑 전환 시 효과 |
|------|------|--------|----------------------|
| 1 | API `/api/` 분리 + URL 환경변수 | 낮음 | 프론트-백 완전 분리 |
| 2 | Repository 패턴 도입 | 중간 | DB 교체 비용 제로 |
| 3 | 역할 Enum + 엔드포인트 태그 | 낮음 | 인증 공사 최소화 |
| 4 | 경로/포트/시그널 정리 | 낮음 | Electron 번들링 안정화 |

---

## 미결 사항

- Supabase 프로젝트 생성 및 스키마 설계는 Electron 전환 시점에 진행
- Electron 래퍼 코드(Node.js) 작성은 별도 스프린트
- 역할별 UI 분기(화면 숨김/표시)는 인증 단계에서 설계
- **Electron 전환 시 API 키 보안 저장소 설계 필요** — `.env` 파일 대신 OS 키체인(keytar 등) 사용 검토
- SQLite 파일 저장 위치 결정 필요 — PyInstaller 번들 후 쓰기 가능한 경로 (`%APPDATA%/Cligent/` 권장, 설치 디렉토리는 읽기 전용일 수 있음)
- SQLite → Supabase 마이그레이션 전략 별도 설계 필요 (Electron 전환 스프린트에서) — **주의: ID 전략(UUID vs 자동증가), 트랜잭션 차이, 오프라인 동기화가 실제 전환 시 최대 리스크**

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | 스코프 & 전략 | 1 | CLEAN | HOLD_SCOPE, 0 critical gaps, 9 issues caught and fixed (2 rounds) |
| Codex Review | `/codex review` | 독립 2차 의견 | 0 | — | — |
| Eng Review | `/plan-eng-review` | 아키텍처 & 테스트 (필수) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX 갭 | 0 | — | — |
| DX Review | `/plan-devex-review` | 개발자 경험 갭 | 0 | — | — |

**UNRESOLVED:** 0
**VERDICT:** CEO CLEARED — Eng Review 필수 (아직 미실행)
