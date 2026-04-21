import yaml
from pathlib import Path
from typing import Optional


class AgentRouter:
    def __init__(self, agents_dir: str = "agents/runtime", prompts_dir: str = "prompts/agents"):
        self.agents_dir = Path(agents_dir)
        self.prompts_dir = Path(prompts_dir)
        self._agents: dict = {}
        self._load_agents()

    def _load_agents(self):
        for yaml_file in self.agents_dir.glob("*.yaml"):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                if config and "name" in config:
                    self._agents[config["name"]] = config
            except (yaml.YAMLError, KeyError) as e:
                # 잘못된 YAML은 건너뜀 — 앱 크래시 방지
                print(f"[AgentRouter] YAML 로드 실패 {yaml_file}: {e}")

    def get_all_agents(self) -> dict:
        return self._agents

    def get_available_agents(self, role: str) -> list:
        result = []
        for agent in self._agents.values():
            if role in agent.get("allowed_roles", []):
                result.append({
                    "name": agent["name"],
                    "display_name": agent["display_name"],
                    "description": agent["description"],
                    # prompt_file, keywords는 노출하지 않음 (보안)
                })
        return result

    def classify_intent(self, message: str) -> Optional[str]:
        """매칭 키워드 수가 가장 많은 에이전트를 반환 (동점 시 YAML 로드 순)"""
        message_lower = message.lower()
        scores: dict[str, int] = {}
        for name, agent in self._agents.items():
            count = sum(1 for kw in agent.get("keywords", []) if kw in message_lower)
            if count > 0:
                scores[name] = count
        if not scores:
            return None
        return max(scores, key=lambda k: scores[k])

    def get_agent_config(self, agent_name: str) -> dict:
        """화이트리스트 검증 — 등록된 에이전트만 허용 (Path Traversal 방지)"""
        agent = self._agents.get(agent_name)
        if not agent:
            raise ValueError(f"Unknown agent: {agent_name}")
        return agent

    def get_system_prompt(self, agent_name: str) -> str:
        agent = self.get_agent_config(agent_name)  # 화이트리스트 통과 후만 파일 접근
        prompt_path = Path(agent["prompt_file"])
        try:
            with open(prompt_path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            raise ValueError(f"System prompt not found for agent: {agent_name}")
