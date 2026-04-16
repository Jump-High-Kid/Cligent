"""
module_manager.py — 역할 기반 모듈 접근 권한 관리

역할:
  owner (원장) → config.yaml의 모든 모듈 접근 가능
  staff (직원) → data/staff_permissions.json에 정의된 모듈만 접근
"""

import json
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
PERMISSIONS_PATH = ROOT / "data" / "staff_permissions.json"


def get_all_module_ids() -> List[str]:
    """config.yaml에 정의된 전체 모듈 ID 목록 반환"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return list(config.get("modules", {}).keys())


def get_allowed_modules(role: str, staff_id: Optional[str] = None) -> List[str]:
    """
    역할과 직원 ID에 따라 허용된 모듈 목록 반환

    Args:
        role: "owner" 또는 "staff"
        staff_id: 직원 ID (role이 staff일 때만 사용)

    Returns:
        허용된 모듈 ID 목록
    """
    # 원장은 전체 모듈 접근
    if role == "owner":
        return get_all_module_ids()

    # 직원: staff_permissions.json 확인
    if role == "staff" and staff_id:
        if PERMISSIONS_PATH.exists():
            with open(PERMISSIONS_PATH, encoding="utf-8") as f:
                permissions = json.load(f)
            staff_data = permissions.get(staff_id, {})
            return staff_data.get("modules", [])

    # staff_id 없거나 설정 없으면 default_roles 기준으로 반환
    return _get_default_modules_for_role(role)


def _get_default_modules_for_role(role: str) -> List[str]:
    """config.yaml의 default_roles 기준으로 허용 모듈 반환"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    modules = config.get("modules", {})
    return [
        module_id
        for module_id, module_cfg in modules.items()
        if role in module_cfg.get("default_roles", [])
    ]


def save_staff_permissions(staff_id: str, name: str, modules: List[str]) -> dict:
    """원장이 직원 모듈 권한을 저장"""
    permissions = {}
    if PERMISSIONS_PATH.exists():
        with open(PERMISSIONS_PATH, encoding="utf-8") as f:
            permissions = json.load(f)

    # _comment 키 보존
    permissions[staff_id] = {"name": name, "modules": modules}

    with open(PERMISSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(permissions, f, ensure_ascii=False, indent=2)

    return permissions[staff_id]


def get_module_info() -> dict:
    """모든 모듈의 id, name, default_roles 반환 (설정 화면용)"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("modules", {})
