# TODOS — Cligent

## P1 (완료)

### 1. agents/ + src/ 폴더 분리 ✅
**완료일**: 2026-04-18
**What**: 루트의 `.md` 파일들을 `agents/`로 이동, FastAPI 코드는 `src/`에 시작.

---

### 2. 시스템 프롬프트 설계 (MVP 핵심) ✅
**완료일**: 2026-04-16
**What**: prompts/ 폴더에 blog.txt, questions.txt, image_prompt.txt 구현

---

### 3. 인증 시스템 구현 ✅
**완료일**: 2026-04-18
**What**: JWT httpOnly 쿠키, 5단계 RBAC, 초대 기반 온보딩, 슬롯 관리

---

### 4. 대시보드 로그아웃 + 직원 초대 UI ✅
**완료일**: 2026-04-18
**What**: 로그아웃 버튼, 직원 초대 모달 (이메일 + 역할 선택 + URL 복사)

---

## P1 (다음 구현)

### 5. [설정 페이지] 팀 & 권한 관리 UI
**What**: 직원 목록 테이블 + 모듈 권한 토글 + 역할 변경 UI
**Why**: 백엔드 API 완료. UI 연결만 필요.
**Context**: `/api/modules/config`, `/api/auth/invite` 이미 구현됨. `settings_setup.html` 초기 설정 위자드는 별도로 있음. 일반 설정 페이지(`settings.html`)에 팀 관리 탭 신규 생성 필요.
**Depends on**: 없음. 바로 시작 가능.

---

### 6. [설정 페이지] 한의원 프로필 ✅
**완료일**: 2026-04-21
**What**: 한의원 이름/주소/전화, 진료과목, 진료시간, 원장 소개글(블로그 자동 반영)
**Context**: clinics 테이블 ALTER 마이그레이션 완료. GET/POST /api/settings/clinic/profile 구현.
  설정 > 시스템 & 보안 탭 내 서브탭 구조로 구현 (chief_director 전용)

---

### 7. [설정 페이지] AI 설정 ✅
**완료일**: 2026-04-21
**What**: API Key 마스킹 표시/변경, 모델 선택 (Haiku/Sonnet/Opus), 월 예산 한도
**Context**: Fernet 암호화로 DB 저장. GET/POST /api/settings/clinic/ai 구현. chief_director 전용.
  블로그 생성 시 DB 저장 키 우선 사용은 별도 연결 필요 (현재는 .env 키 유지)

---

### 8. [설정 페이지] 블로그 설정 ✅
**완료일**: 2026-04-21
**What**: 질문단계 ON/OFF·개수, 글자 수, 기본 톤 선택, 프롬프트 직접 편집
**Context**: config.yaml 직접 수정 (save_blog_config). 프롬프트 편집은 chief_director 전용.
  GET/POST /api/settings/blog + /api/settings/blog/prompt 구현. 콘텐츠 에이전트 탭으로 배치.

---

### 9. 비밀번호 찾기 기능 ✅
**완료일**: 2026-04-21
**What**: 원장이 직원 편집 패널에서 "비밀번호 재설정 링크 생성" → 72시간 유효 링크 복사 → 직원이 링크로 비밀번호 재설정
**Context**: create_reinvite() 신규 함수 (기존 토큰 만료 처리 후 새 토큰 생성).
  complete_onboarding()이 기존 사용자는 INSERT 대신 UPDATE로 처리하도록 수정.
  POST /api/settings/staff/{id}/reinvite 엔드포인트 구현.

---

## P2 (설정 페이지 나머지)

### 10. [설정 페이지] 알림 설정
**What**: 재처방/예약취소/카카오톡 알림, 브라우저/이메일 방식 선택
**Context**: CRM 모듈 이전엔 알림 대상 없음. UI는 미리 만들되 기능은 빈 상태.
**Depends on**: CRM 모듈 시작 후

---

### 11. [설정 페이지] 보안
**What**: 비밀번호 변경 폼, 로그인 이력 조회 (최근 10건)
**Context**: 비밀번호 변경 API 이미 구현 (`/api/auth/change-password`). 로그인 이력 DB 테이블 추가 필요.
**Depends on**: DB 마이그레이션

---

### 12. [설정 페이지] 플랜 & 사용량
**What**: 현재 플랜 표시, 이번 달 토큰/비용, 월 예산 대비 프로그레스 바
**Context**: 토큰 추적 테이블 DB 추가 필요. blog_history.json 기반 간이 구현 가능.
**Depends on**: AI 설정 (월 예산 한도) 완료 후

---

### 13. [설정 페이지] 데이터 관리
**What**: 블로그 이력 CSV 내보내기, 직원 권한 초기화, 전체 데이터 삭제 (한의원명 입력 확인)
**Depends on**: 설정 페이지 기본 구조 완료 후

---

## P2 (기타)

### 14. 실제 한의원 블로그 10편 베타 테스트
**What**: 원장님 본인 한의원 블로그에 MVP 생성 글 10편 실제 업로드.
**Why**: 실제 사용 데이터가 Phase 2(휴머나이저 등) 진입 기준.
**Depends on**: FastAPI MVP 완성.

---

### 15. BYOAI 온보딩 위자드 ✅ (2026-04-22 — #15 대체)
**What**: 앱 내 4단계 위자드로 API 키 온보딩 진행. RBAC 분기(대표원장만 설정 가능), 실제 API 유효성 검증, ?onboard=1 자동 오픈.
**Supersedes**: BYOAI 온보딩 가이드 영상 (가이드 영상 불필요, 인앱 위자드로 대체)

---

### 15-a. API 키 자동 감지 (접두사 인식)
**What**: 붙여넣은 문자열에서 `sk-ant-` / `sk-proj-` / `AIza` 등 접두사를 인식해 공급사를 자동 판별. 위자드 Step 1 선택 없이 바로 Step 3으로 이동.
**Why**: 위자드 시나리오 선택 단계가 불필요해져서 더 빠른 온보딩 가능.
**Depends on**: Gemini/ChatGPT 멀티 제공자 지원 시 구현 (현재는 Claude 단독).

---

### 16. 휴머나이저 (AI 냄새 제거)
**What**: 생성된 블로그 글을 더 자연스러운 사람 문체로 변환.
**Depends on**: 베타 테스트 10편 결과로 필요성 판단.

---

### 17. JWT 인증 + API 키 암호화 (멀티유저)
**What**: 멀티유저 전환 시 필요한 인증 시스템 강화.
**Context**: 현재 로컬 전용. SaaS 공개 시 필수.
**Depends on**: 고객 수 증가 후.

---

### 18. 대시보드 중앙 현황판
**What**: 여러 기능(블로그, CRM, 재고 등)을 한 화면에서 확인.
**Depends on**: 2번째 기능(CRM 또는 재고) 완성 후.

---

### 19. 10개 AI 모델 어댑터 (GPT, Gemini 등)
**What**: Claude 외 다른 AI 모델도 선택 가능하게.
**Depends on**: 실제 고객 요청 발생 시.
