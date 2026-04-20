"""
pattern_selector.py — 블로그 패턴 조합 선택 엔진

5개 레이어로 최적 패턴 조합을 선택합니다:
  Layer 1: 하드 블록 — 맥락 상 불가한 서론+본론 조합 제거
  Layer 2: 내러티브 아크 — 서론 톤과 어울리는 결론 패턴만 허용
  Layer 3: 본론 시너지 — 함께 있어야 완성되는 패턴 쌍 강제
  Layer 4: 사용자 콘텐츠 분석 — 추가 자료 기반 패턴 친화도 가중치
  Layer 5: 히스토리 중복 방지 — 최근 사용 패턴 가중치 감소

화제 전환 패턴: 본론 섹션 사이에 1~2개 삽입 (모든 조합과 호환)
"""
import json
import random
import re
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# 패턴 메타데이터
# tags: 톤/스타일 분류 (Layer 2 아크 매칭에 사용)
# signals: 이 패턴을 선호하게 만드는 사용자 입력 키워드 (Layer 4)
# ──────────────────────────────────────────────────────────────────────────────

INTRO_PATTERNS: dict[str, dict] = {
    "증상_공감형":     {"tags": ["emotional", "empathy"],      "signals": ["증상", "아프", "불편", "고통", "힘들", "피로"]},
    "자가진단_질문형": {"tags": ["interactive", "structural"],  "signals": ["확인", "체크", "해당", "진단", "테스트"]},
    "통계_충격형":     {"tags": ["data", "logical"],           "signals": ["연구", "통계", "조사", "결과", "명", "비율"]},
    "시나리오_공감형": {"tags": ["emotional", "narrative"],    "signals": ["직장인", "주부", "수험생", "하루", "일상", "야근"]},
    "오해_반박형":     {"tags": ["logical", "educational"],    "signals": ["오해", "잘못", "사실", "실제로", "알려진", "반대"]},
    "계절_시기_연결형":{"tags": ["contextual", "timely"],      "signals": ["봄", "여름", "가을", "겨울", "환절기", "계절", "날씨", "습도"]},
    "독자_분류형":     {"tags": ["structural", "targeted"],    "signals": ["40대", "50대", "임산부", "노인", "청소년", "갱년기"]},
}

BODY_PATTERNS: dict[str, dict] = {
    "증상_원인_기전_설명형":  {"tags": ["educational", "logical"],      "signals": ["원인", "이유", "기전", "왜", "과정"]},
    "서양_한의학_비교형":    {"tags": ["comparative", "educational"],   "signals": ["양방", "병원", "약", "차이", "비교", "서양"]},
    "치료법_단계별_소개형":  {"tags": ["structural", "informational"],  "signals": ["치료", "침", "한약", "뜸", "단계", "방법"]},
    "변증_유형_분류형":      {"tags": ["technical", "specialized"],     "signals": ["변증", "체질", "유형", "기허", "혈허", "음허"]},
    "Q&A_자주묻는질문형":    {"tags": ["interactive", "informational"], "signals": ["질문", "궁금", "자주", "FAQ", "물음"]},
    "생활_관리_팁_리스트형": {"tags": ["practical", "emotional"],       "signals": ["생활", "습관", "음식", "운동", "관리", "집에서"]},
    "케이스_스터디형":       {"tags": ["narrative", "emotional"],       "signals": ["환자", "사례", "경험", "내원", "회복"]},
    "시각화_보조형":         {"tags": ["visual", "structural"],         "signals": ["경혈", "위치", "이미지", "그림", "도표"]},
}

CONCLUSION_PATTERNS: dict[str, dict] = {
    # arcs: 이 결론과 어울리는 서론 태그 목록 (Layer 2)
    "위험_신호_경고형":         {"tags": ["logical", "urgent"],        "arcs": ["logical", "data", "interactive", "educational"]},
    "요약_핵심_메시지형":       {"tags": ["logical", "structural"],    "arcs": ["logical", "data", "structural", "educational"]},
    "부드러운_내원_유도형":     {"tags": ["emotional", "soft"],        "arcs": ["emotional", "narrative", "contextual", "targeted", "empathy"]},
    "연관_콘텐츠_연결형":       {"tags": ["structural", "informational"], "arcs": ["structural", "contextual", "interactive", "timely"]},
    "생활_실천_과제형":         {"tags": ["practical", "emotional"],   "arcs": ["emotional", "narrative", "contextual", "practical"]},
    "공감_재확인_희망_제시형":  {"tags": ["emotional", "warm"],        "arcs": ["emotional", "narrative", "empathy"]},
    "전문가_권위_마무리형":     {"tags": ["logical", "authoritative"], "arcs": ["logical", "data", "educational", "comparative"]},
}

PIVOT_PATTERNS: dict[str, str] = {
    "역발상_전환형":         "흔한 통념과 반대되는 관점으로 전환 — '그런데 반대로 생각해보면...'",
    "독자_참여_전환형":      "독자 스스로 확인하게 유도 — '잠깐, 지금 바로 확인해보세요'",
    "최신_근거_전환형":      "최근 연구·학술 동향 소개 — '최근 연구에서 흥미로운 결과가...'",
    "감성_정보_교차_전환형": "감성 흐름 ↔ 정보 흐름 톤 전환으로 리듬 변화 생성",
    "줌인_줌아웃_전환형":    "시야 스케일 전환 — '큰 그림에서 보면...' / '더 구체적으로는...'",
    "일상_비유_전환형":      "한의학 개념을 친숙한 일상 비유로 풀어 이해를 돕는 전환",
}

# ──────────────────────────────────────────────────────────────────────────────
# Layer 1: 하드 블록 — (intro_id, body_id) 금지 조합
# ──────────────────────────────────────────────────────────────────────────────
INCOMPATIBLE_INTRO_BODY: set[tuple[str, str]] = {
    ("통계_충격형",     "케이스_스터디형"),       # 숫자 → 개인 사례 직행: 흐름 단절
    ("시나리오_공감형", "서양_한의학_비교형"),    # 감성 스토리 → 비교표: 분위기 방해
    ("독자_분류형",     "시각화_보조형"),         # 대상 분류 후 시각화만: 핵심 설명 공백
    ("오해_반박형",     "케이스_스터디형"),       # 논리 반박 후 개인 사례: 반박 논리가 희석됨
}

# ──────────────────────────────────────────────────────────────────────────────
# Layer 3: 본론 시너지 — A 선택 시 B를 선택 풀에 추가
# ──────────────────────────────────────────────────────────────────────────────
BODY_SYNERGIES: list[tuple[str, str]] = [
    ("변증_유형_분류형",  "치료법_단계별_소개형"),  # 변증 후 치료 연결 필수
    ("케이스_스터디형",   "생활_관리_팁_리스트형"), # 사례 후 실용 팁 자연스럽게 연결
]

# ──────────────────────────────────────────────────────────────────────────────
# Layer 4: 사용자 콘텐츠 신호 → 태그 친화도 매핑
# ──────────────────────────────────────────────────────────────────────────────
SIGNAL_TO_TAG_AFFINITY: dict[str, list[str]] = {
    "emotional":    ["emotional", "empathy", "narrative", "warm", "practical"],
    "data":         ["data", "logical", "informational", "educational"],
    "seasonal":     ["contextual", "timely"],
    "narrative":    ["narrative", "emotional"],
    "technical":    ["technical", "specialized", "educational", "comparative"],
    "interactive":  ["interactive"],
    "practical":    ["practical", "informational"],
    "comparative":  ["comparative", "educational"],
}

CONTENT_SIGNAL_KEYWORDS: dict[str, list[str]] = {
    "emotional":   ["증상", "아프", "불편", "고통", "힘들", "걱정", "불안", "스트레스"],
    "data":        ["연구", "통계", "%", "명", "조사", "결과", "효과", "논문", "임상"],
    "seasonal":    ["봄", "여름", "가을", "겨울", "환절기", "계절", "날씨", "습도", "온도"],
    "narrative":   ["환자", "사례", "경험", "내원", "회복", "치료 받", "분이", "어르신"],
    "technical":   ["변증", "기허", "혈허", "음허", "경락", "오장", "육부", "한의학", "경혈"],
    "interactive": ["확인", "체크", "해당", "질문", "궁금", "테스트"],
    "practical":   ["생활", "습관", "음식", "운동", "관리", "집에서", "셀프"],
    "comparative": ["양방", "서양의학", "비교", "차이", "반면", "대조"],
}


def select_patterns(
    keyword: str,
    materials: Optional[dict] = None,
    history_path: Optional[Path] = None,
) -> dict:
    """
    블로그 주제와 사용자 추가 자료를 분석해 최적 패턴 조합을 반환합니다.

    Returns:
        {
            "intro":      [str, ...],  # 1~2개 서론 패턴 ID
            "body":       [str, ...],  # 3~4개 본론 패턴 ID (순서 유지)
            "pivots":     [str, ...],  # 1~2개 화제 전환 패턴 ID
            "conclusion": [str, ...],  # 1~2개 결론 패턴 ID
            "prompt_block": str,       # Claude에게 전달할 패턴 지시 블록
        }
    """
    # Layer 4: 사용자 콘텐츠에서 신호 추출
    content_signals = _analyze_content(keyword, materials)

    # 서론 선택
    intro_scores = _score_patterns(INTRO_PATTERNS, content_signals)
    intro_scores = _apply_history_penalty(intro_scores, history_path)
    n_intro = random.choice([1, 2])
    selected_intro = _weighted_sample(intro_scores, k=n_intro)

    # 서론 태그 집합 (Layer 2 아크 + Layer 1 블록에 사용)
    intro_tags: set[str] = set()
    for pid in selected_intro:
        intro_tags.update(INTRO_PATTERNS[pid]["tags"])

    # Layer 1: 하드 블록 적용 후 본론 풀 구성
    available_body = {
        pid: data for pid, data in BODY_PATTERNS.items()
        if not any((intro, pid) in INCOMPATIBLE_INTRO_BODY for intro in selected_intro)
    }

    body_scores = _score_patterns(available_body, content_signals)
    body_scores = _apply_history_penalty(body_scores, history_path)
    n_body = random.choice([3, 4])
    selected_body = _weighted_sample(body_scores, k=min(n_body, len(available_body)))

    # Layer 3: 시너지 강제 적용
    selected_body = _apply_synergies(selected_body, available_body)

    # 화제 전환 패턴 선택 (1~2개, 히스토리 무관한 와일드카드)
    n_pivot = random.choice([1, 2])
    selected_pivots = random.sample(list(PIVOT_PATTERNS.keys()), k=min(n_pivot, len(PIVOT_PATTERNS)))

    # Layer 2: 내러티브 아크 — 서론 태그와 어울리는 결론만 허용
    eligible_conclusions = {
        pid: data for pid, data in CONCLUSION_PATTERNS.items()
        if any(arc in intro_tags for arc in data["arcs"])
    }
    if not eligible_conclusions:  # 매칭 없을 경우 전체 허용
        eligible_conclusions = CONCLUSION_PATTERNS

    conclusion_scores = _score_patterns(eligible_conclusions, content_signals)
    conclusion_scores = _apply_history_penalty(conclusion_scores, history_path)
    n_conclusion = random.choice([1, 2])
    selected_conclusion = _weighted_sample(conclusion_scores, k=min(n_conclusion, len(eligible_conclusions)))

    # Layer 5: 히스토리 기록
    if history_path:
        combo_key = _combo_key(selected_intro, selected_body, selected_conclusion)
        _update_history(history_path, combo_key)

    # 패턴 지시 블록 생성
    prompt_block = _build_prompt_block(
        selected_intro, selected_body, selected_pivots, selected_conclusion, history_path
    )

    return {
        "intro":        selected_intro,
        "body":         selected_body,
        "pivots":       selected_pivots,
        "conclusion":   selected_conclusion,
        "prompt_block": prompt_block,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 내부 함수
# ──────────────────────────────────────────────────────────────────────────────

def _analyze_content(keyword: str, materials: Optional[dict]) -> dict[str, float]:
    """사용자 입력 전체에서 신호 키워드를 집계하여 신호 강도를 반환합니다."""
    text_parts = [keyword]
    if materials:
        text_parts.append(materials.get("text", ""))
        for link in materials.get("webLinks", []):
            text_parts.append(link.get("content", "") if isinstance(link, dict) else "")
        for yt in materials.get("youtubeLinks", []):
            text_parts.append(yt.get("transcript", "") if isinstance(yt, dict) else "")

    combined = " ".join(text_parts).lower()

    signals: dict[str, float] = {}
    for signal_type, keywords in CONTENT_SIGNAL_KEYWORDS.items():
        count = sum(combined.count(kw) for kw in keywords)
        signals[signal_type] = float(count)

    return signals


def _score_patterns(patterns: dict, signals: dict[str, float]) -> dict[str, float]:
    """신호 강도 기반으로 각 패턴의 선택 가중치를 계산합니다."""
    scores: dict[str, float] = {}
    for pid, data in patterns.items():
        score = 1.0  # 기본 점수 — 신호 없어도 선택 가능
        tags = data.get("tags", [])
        for signal_type, intensity in signals.items():
            if intensity > 0:
                affinity_tags = SIGNAL_TO_TAG_AFFINITY.get(signal_type, [])
                if any(t in tags for t in affinity_tags):
                    score += intensity * 0.4
        scores[pid] = score
    return scores


def _apply_history_penalty(
    scores: dict[str, float], history_path: Optional[Path]
) -> dict[str, float]:
    """최근 5회 조합에 등장한 패턴의 가중치를 50% 감소시킵니다."""
    if not history_path or not history_path.exists():
        return scores
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        recent_combos = data.get("pattern_combos", [])[:5]
        recent_pids: set[str] = set()
        for combo in recent_combos:
            for pid in re.split(r"[:|]", combo):
                if pid:
                    recent_pids.add(pid)
        return {pid: (score * 0.5 if pid in recent_pids else score) for pid, score in scores.items()}
    except Exception:
        return scores


def _weighted_sample(scores: dict[str, float], k: int) -> list[str]:
    """가중치 기반 비복원 샘플링 — 점수가 높을수록 선택될 확률이 높습니다."""
    if k >= len(scores):
        return list(scores.keys())

    remaining = list(scores.items())  # [(pid, score), ...]
    selected: list[str] = []

    for _ in range(k):
        if not remaining:
            break
        total = sum(w for _, w in remaining)
        r = random.uniform(0, total)
        cumulative = 0.0
        for i, (pid, weight) in enumerate(remaining):
            cumulative += weight
            if r <= cumulative:
                selected.append(pid)
                remaining.pop(i)
                break

    return selected


def _apply_synergies(selected_body: list[str], available_body: dict) -> list[str]:
    """Layer 3: 시너지 파트너가 빠졌으면 자동 추가합니다."""
    result = list(selected_body)
    for trigger, partner in BODY_SYNERGIES:
        if trigger in result and partner not in result and partner in available_body:
            result.append(partner)
    return result


def _combo_key(intro: list[str], body: list[str], conclusion: list[str]) -> str:
    return "|".join(sorted(intro)) + ":" + "|".join(body) + ":" + "|".join(sorted(conclusion))


def _update_history(history_path: Path, combo_key: str) -> None:
    """최근 10개 조합을 blog_history.json에 기록합니다."""
    try:
        data = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
        history = data.get("pattern_combos", [])
        if combo_key not in history:
            history.insert(0, combo_key)
        data["pattern_combos"] = history[:10]
        history_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass  # 히스토리 실패해도 블로그 생성은 계속 진행


def _load_pattern_instructions() -> dict[str, str]:
    """blog_patterns.txt에서 [SECTION:ID] 블록을 파싱하여 지시문을 로드합니다."""
    patterns_file = Path(__file__).parent.parent / "prompts" / "blog_patterns.txt"
    if not patterns_file.exists():
        return {}

    text = patterns_file.read_text(encoding="utf-8")
    result: dict[str, str] = {}

    # [INTRO:ID], [BODY:ID], [PIVOT:ID], [CONCLUSION:ID] 섹션 파싱
    block_pattern = re.compile(r"\[(?:INTRO|BODY|PIVOT|CONCLUSION):([^\]]+)\]\n(.*?)(?=\n\[|\Z)", re.DOTALL)
    for match in block_pattern.finditer(text):
        pid = match.group(1).strip()
        content = match.group(2).strip()
        # 작성지시: 줄만 추출
        instruction_match = re.search(r"작성지시: (.+?)(?:\n예시:|$)", content, re.DOTALL)
        if instruction_match:
            result[pid] = instruction_match.group(1).strip()
        else:
            # 첫 번째 줄 (설명)만 사용
            result[pid] = content.split("\n")[0].strip()

    return result


def _build_prompt_block(
    intro: list[str],
    body: list[str],
    pivots: list[str],
    conclusion: list[str],
    history_path: Optional[Path],
) -> str:
    """Claude 시스템 프롬프트에 삽입할 패턴 지시 블록을 생성합니다."""
    instructions = _load_pattern_instructions()

    # 히스토리에서 최근 조합 정보 로드 (다양성 안내용)
    history_note = ""
    if history_path and history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            count = len(data.get("pattern_combos", []))
            if count >= 3:
                history_note = f"\n※ 이 블로그는 이전 {count}편과 다른 구조 조합으로 작성됩니다. 유사한 흐름이 반복되지 않도록 주의하세요."
        except Exception:
            pass

    lines = [
        "## 이번 블로그 구조 패턴",
        "아래 지정된 패턴과 순서를 따라 글을 구성하세요.",
        history_note,
        "",
        "### 서론",
    ]

    for pid in intro:
        inst = instructions.get(pid, "")
        lines.append(f"- **{pid.replace('_', ' ')}**: {inst}")

    lines += ["", "### 본론 (번호 순서대로 작성)"]

    pivot_positions = _distribute_pivots(len(body), len(pivots))
    pivot_index = 0

    for i, pid in enumerate(body):
        inst = instructions.get(pid, "")
        lines.append(f"- **섹션 {i + 1} — {pid.replace('_', ' ')}**: {inst}")
        # 화제 전환 삽입 (지정 위치 이후)
        if pivot_index < len(pivots) and i in pivot_positions:
            pvt = pivots[pivot_index]
            pvt_desc = PIVOT_PATTERNS.get(pvt, "")
            lines.append(f"  → **[화제 전환: {pvt.replace('_', ' ')}]** {pvt_desc}")
            pivot_index += 1

    lines += ["", "### 결론"]

    for pid in conclusion:
        inst = instructions.get(pid, "")
        lines.append(f"- **{pid.replace('_', ' ')}**: {inst}")

    return "\n".join(line for line in lines if line is not None)


def _distribute_pivots(n_body: int, n_pivots: int) -> list[int]:
    """본론 섹션 인덱스 중 화제 전환을 삽입할 위치를 균등 분배합니다."""
    if n_body <= 1 or n_pivots == 0:
        return []
    # 첫 번째 피벗: 전반부 마지막 섹션 이후
    # 두 번째 피벗: 후반부 마지막 섹션 이후
    if n_pivots == 1:
        return [max(0, n_body // 2 - 1)]
    return [max(0, n_body // 3 - 1), max(1, (n_body * 2) // 3 - 1)]
