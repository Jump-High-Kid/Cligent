"""
daily_report.py — 데일리 리포트 생성

하루치 에러 로그 + 사용자 피드백을 취합하여 Claude Haiku로 요약한 뒤
~/obsidian-vault/Cligent/버그리포트/YYYY-MM-DD.md 에 저장한다.

사용:
  from daily_report import generate_daily_report
  generate_daily_report("2026-04-25")   # 동기 함수, asyncio.to_thread 로 호출 권장
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
_VAULT = Path.home() / "obsidian-vault" / "Cligent" / "버그리포트"


def _load_errors(date_str: str) -> list:
    log_path = ROOT / "data" / "error_logs" / f"{date_str}.jsonl"
    if not log_path.exists():
        return []
    items = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items


def _load_feedbacks(date_str: str) -> list:
    log_path = ROOT / "data" / "feedback.jsonl"
    if not log_path.exists():
        return []
    items = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if item.get("ts", "").startswith(date_str):
                    items.append(item)
            except Exception:
                pass
    return items


def _format_errors(errors: list) -> str:
    if not errors:
        return "없음"
    lines = []
    for e in errors[:30]:
        lines.append(
            f"- [{e.get('ts', '')}] {e.get('method', '')} {e.get('path', '')} "
            f"— {e.get('error_type', '')}: {e.get('error_msg', '')[:200]}"
        )
    if len(errors) > 30:
        lines.append(f"… 외 {len(errors) - 30}건")
    return "\n".join(lines)


def _format_feedbacks(feedbacks: list) -> str:
    if not feedbacks:
        return "없음"
    page_labels = {"blog": "블로그", "dashboard": "대시보드", "help": "도움말",
                   "chat": "AI도우미", "settings": "설정", "youtube": "YouTube"}
    lines = []
    for fb in feedbacks[:30]:
        page = page_labels.get(fb.get("page", ""), fb.get("page", "?"))
        lines.append(f"- [{fb.get('ts', '')}] [{page}] {fb.get('message', '')[:300]}")
    if len(feedbacks) > 30:
        lines.append(f"… 외 {len(feedbacks) - 30}건")
    return "\n".join(lines)


def _build_summary_prompt(date_str: str, errors: list, feedbacks: list) -> str:
    error_block = _format_errors(errors)
    feedback_block = _format_feedbacks(feedbacks)
    return f"""다음은 Cligent(한의원 관리 플랫폼) {date_str} 하루치 서버 오류 및 사용자 피드백 로그입니다.

## 서버 오류 ({len(errors)}건)
{error_block}

## 사용자 피드백 ({len(feedbacks)}건)
{feedback_block}

아래 형식으로 한국어 데일리 리포트를 작성해 주세요:

**1. 요약** (2~3문장, 오늘 하루 전반적인 상황)

**2. 주요 오류** (패턴/빈도 중심, 없으면 "이상 없음")

**3. 사용자 피드백 핵심** (주요 불만/요청 요약, 없으면 "피드백 없음")

**4. 개선 제안** (최대 3가지, 구체적으로)"""


def generate_daily_report(date_str: str) -> str:
    """
    날짜별 리포트 생성 후 Obsidian 볼트에 저장.
    반환값: 저장된 파일 경로
    """
    import anthropic

    errors = _load_errors(date_str)
    feedbacks = _load_feedbacks(date_str)

    if not errors and not feedbacks:
        summary = "오류 및 피드백 없음 — 정상 운영"
        ai_section = f"**1. 요약** {summary}\n\n**2. 주요 오류** 이상 없음\n\n**3. 사용자 피드백 핵심** 피드백 없음\n\n**4. 개선 제안** 해당 없음"
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1200,
            messages=[{"role": "user", "content": _build_summary_prompt(date_str, errors, feedbacks)}],
        )
        ai_section = response.content[0].text

    content = f"""# Cligent 데일리 리포트 — {date_str}

> 생성: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

## AI 요약

{ai_section}

---

## 원본 데이터

### 서버 오류 ({len(errors)}건)

{_format_errors(errors)}

### 사용자 피드백 ({len(feedbacks)}건)

{_format_feedbacks(feedbacks)}
"""

    _VAULT.mkdir(parents=True, exist_ok=True)
    out_path = _VAULT / f"{date_str}.md"
    out_path.write_text(content, encoding="utf-8")
    return str(out_path)
