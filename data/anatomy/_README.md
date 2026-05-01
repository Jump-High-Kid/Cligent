# 해부학 DB Phase 1 — 자료 수집 가이드

Cligent 베타 critical path. 30 부위 × 1 자료 = Phase 1 완료. 1주 일정 목표.

## 작업 흐름 (1부위당 ~3분)

### Step 1. 검색 URL 확인
```bash
# 부위별 3소스 검색 링크 출력
python -c "import json; print(json.dumps(json.load(open('data/anatomy/_SEARCH_URLS.json'))['search_urls']['neck_anterior'], indent=2, ensure_ascii=False))"
```

### Step 2. 적합한 자료 선택
3 소스 우선순위:
1. **Servier Medical Art** — 메인. CC BY 4.0, 의학 일러스트 톤 통일성↑
2. **AnatomyTOOL** — 보완. CC BY 일부 (라이선스는 자료별 확인)
3. **Wikimedia Commons** — 백업. 좋은 해부도 많음

**선택 기준:**
- 한국 한의원 톤에 맞는 의학 일러스트 (너무 임상적/너무 만화 X)
- 경혈 좌표 매핑 가능 (Phase 2)한 평면 뷰
- SVG 우선, 없으면 고해상도 PNG

### Step 3. 자동 다운로드
```bash
python scripts/fetch_anatomy.py neck_anterior \
  --url "https://smart.servier.com/smart_image/neck-3/" \
  --view anterior
```
→ Playwright headless로 페이지 fetch
→ 이미지 다운로드 → `data/anatomy/neck_anterior/source.svg`
→ 페이지에서 라이선스·저자·제목 자동 파싱
→ `meta.json` 자동 생성

### Step 4. (Servier 외 소스 또는 fetch 실패 시) 수동 fallback
```bash
# 빈 메타 생성
python scripts/init_anatomy_part.py neck_anterior --view anterior

# 자료 직접 복사
cp ~/Downloads/anatomy.svg data/anatomy/neck_anterior/source.svg

# meta.json 열어서 source_url 입력
```

### Step 5. 검증
```bash
python scripts/validate_anatomy_meta.py
# 예상 출력: ✓ 1/30 완료 (3.3%)

# 실패 시 자동 수정
python scripts/validate_anatomy_meta.py --fix
```

## 30 부위 목록 (slug ↔ 한글)

| 카테고리 | slug | 한글 |
|---|---|---|
| 머리·목 | face | 안면 |
| | occiput | 후두부 |
| | temple | 측두부 |
| | neck_anterior | 전경부 |
| | neck_posterior | 후경부 |
| 어깨·팔 | shoulder | 견관절 |
| | upper_arm | 상완 |
| | elbow | 주관절 |
| | forearm | 전완 |
| | hand | 수부 |
| 가슴·등 | chest | 흉부 |
| | scapula | 견갑 |
| | upper_back | 상배부 |
| | mid_back | 요배부 |
| 허리·골반 | lumbar | 요부 |
| | sacrum | 천골부 |
| | buttock | 둔부 |
| | groin | 서혜부 |
| 다리 | hip | 고관절 |
| | thigh | 대퇴 |
| | knee | 슬관절 |
| | lower_leg | 하퇴 |
| | ankle | 족관절 |
| | foot_dorsal | 족배 |
| | foot_plantar | 족저 |
| | achilles | 아킬레스 |
| 복부·기타 | upper_abdomen | 상복부 |
| | lower_abdomen | 하복부 |
| | flank | 옆구리 |
| | skeleton | 전신골격 |

## view_angle 가이드

| 값 | 한글 | 설명 |
|---|---|---|
| `anterior` | 정면 (앞쪽) | 환자가 마주 보는 방향. 가장 많이 사용 |
| `posterior` | 후면 (뒤쪽) | 등 쪽에서 본 방향 |
| `lateral` | 외측 (옆) | 몸의 바깥쪽 측면 |
| `medial` | 내측 (안쪽) | 몸의 중심선 쪽 측면 |
| `oblique` | 사면 (비스듬히) | 3/4 view 등 |

대부분 부위는 `anterior` 또는 `posterior` 1개로 충분. 어깨·고관절 같은 입체 부위는 `lateral`이 더 적합한 경우 있음.

## 라이선스 (중요)

**허용 라이선스만 사용 가능 (자동 검증):**
- ✅ CC BY 4.0
- ✅ CC BY-SA 4.0
- ✅ CC0 (Public Domain)
- ✅ CC BY 3.0

다른 라이선스 자료는 **사용 금지**. attribution_text는 검증 스크립트가 자동 생성·검증하므로 손으로 안 써도 됨.

## 진행률 추적

```bash
python scripts/validate_anatomy_meta.py
# 23/30 완료 (76.7%) — 7 부위 남음:
#   - mid_back, sacrum, groin, hip, ankle, foot_plantar, skeleton
```

`.gitkeep`만 있는 디렉토리 = 아직 자료 없음.
`meta.json` 있고 검증 통과 = 완료.

## 자주 발생하는 문제

| 증상 | 원인 | 해결 |
|---|---|---|
| `fetch_anatomy.py` 멈춤 | Playwright Chromium 미설치 | `playwright install chromium` |
| `attribution_text mismatch` | 수동 입력 오타 | `--fix` 플래그로 자동 수정 |
| `file_path not found` | 파일 복사 잊음 | `cp ~/Downloads/file.svg data/anatomy/{slug}/source.svg` |
| `license not in whitelist` | 자료 라이선스 다름 | 다른 자료 선택 또는 화이트리스트 확장 |
| 한글 부위명 폴더 만들어짐 | 영문 slug 미사용 | `data/anatomy/<영문 slug>/`만 사용 |

## 다음 단계 (Phase 2 예약)

Phase 1 (30/30 완료) 후:
- `Week 2`: 경혈 좌표 매핑 도구 (AI 작업)
- `Week 3~4`: 240 경혈 좌표 매핑 (원장님 도메인 작업)
- `M1~M2`: image2 edit endpoint 통합
