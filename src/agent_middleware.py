import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# claude-sonnet-4-6 기준 (USD/1M tokens)
PRICE_INPUT_PER_M = 3.0
PRICE_OUTPUT_PER_M = 15.0
KRW_RATE = 1350

HALLUCINATION_PATTERNS = [
    r"반드시 효과",
    r"100% 치료",
    r"완치 보장",
    r"부작용 없음",
    r"임상적으로 증명",
]


class AgentMiddleware:
    def __init__(self, log_path: str = "data/agent_log.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_request(self, user_id: str, agent_name: str, message: str,
                    input_tokens: int, output_tokens: int):
        cost = self.calculate_cost(input_tokens, output_tokens)
        # 환자 메시지 원문 비저장 — SHA-256 해시값만 기록 (개인정보보호법 준수)
        message_hash = hashlib.sha256(message.encode()).hexdigest()[:16]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "agent_name": agent_name,
            "message_hash": message_hash,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_krw": round(cost["total_krw"], 4),
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> dict:
        input_krw = PRICE_INPUT_PER_M * input_tokens / 1_000_000 * KRW_RATE
        output_krw = PRICE_OUTPUT_PER_M * output_tokens / 1_000_000 * KRW_RATE
        return {
            "input_krw": input_krw,
            "output_krw": output_krw,
            "total_krw": input_krw + output_krw,
        }

    def check_hallucination_risk(self, text: str) -> bool:
        for pattern in HALLUCINATION_PATTERNS:
            if re.search(pattern, text):
                return True
        return False
