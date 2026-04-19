"""
module_manager.py — 역할 기반 모듈 접근 권한 관리

역할 계층 (상위 → 하위):
  chief_director(대표원장) > director(원장) > manager(매니저)
  > team_leader(팀장) > team_member(팀원)

우선순위:
  1. data/rbac_permissions.json (위자드 저장 값)
  2. config.yaml default_roles (fallback)
  3. data/staff_permissions.json (직원 개별 override)
"""

import json
from pathlib import Path
from typing import List, Optional

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
PERMISSIONS_PATH = ROOT / "data" / "staff_permissions.json"
RBAC_PATH = ROOT / "data" / "rbac_permissions.json"

# 역할 계층: 인덱스가 낮을수록 상위 권한
ROLE_HIERARCHY = [
    "chief_director",
    "director",
    "manager",
    "team_leader",
    "team_member",
]

# 하위 호환: 구버전 owner/staff → 새 역할 매핑
LEGACY_ROLE_MAP = {
    "owner": "director",
    "staff": "team_member",
}


def _normalize_role(role: str) -> str:
    """구버전 역할 키를 새 역할 키로 변환"""
    return LEGACY_ROLE_MAP.get(role, role)


def _role_level(role: str) -> int:
    """역할의 계층 레벨 반환 (없으면 최하위)"""
    role = _normalize_role(role)
    try:
        return ROLE_HIERARCHY.index(role)
    except ValueError:
        return len(ROLE_HIERARCHY)


def role_has_access(user_role: str, required_roles: List[str]) -> bool:
    """
    user_role이 required_roles 중 하나 이상의 권한을 보유하는지 확인.
    상위 역할은 하위 역할 권한을 자동 포함 (누적 상속).
    """
    user_level = _role_level(user_role)
    for r in required_roles:
        if user_level <= _role_level(r):
            return True
    return False


def get_all_module_ids() -> List[str]:
    """config.yaml에 정의된 전체 모듈 ID 목록 반환"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return list(config.get("modules", {}).keys())


def _get_rbac_allowed_modules(role: str) -> Optional[List[str]]:
    """
    rbac_permissions.json 기준으로 role에 허용된 모듈 목록 반환.
    파일이 없으면 None 반환 (→ config.yaml fallback 사용).
    """
    if not RBAC_PATH.exists():
        return None

    with open(RBAC_PATH, encoding="utf-8") as f:
        rbac = json.load(f)

    role = _normalize_role(role)
    module_perms = rbac.get("module_permissions", {})
    allowed = []
    for module_id, perm in module_perms.items():
        if role_has_access(role, perm.get("allowed_roles", [])):
            allowed.append(module_id)
    return allowed


def get_allowed_modules(role: str, staff_id: Optional[str] = None) -> List[str]:
    """
    역할과 직원 ID에 따라 허용된 모듈 목록 반환.

    우선순위:
      1. staff_permissions.json에 해당 staff_id가 있으면 modules 직접 사용
      2. rbac_permissions.json 기준 역할별 허용 목록
      3. config.yaml default_roles fallback
    """
    role = _normalize_role(role)

    # 직원 개별 override 확인
    if staff_id and PERMISSIONS_PATH.exists():
        with open(PERMISSIONS_PATH, encoding="utf-8") as f:
            permissions = json.load(f)
        staff_data = permissions.get(staff_id, {})
        if "modules" in staff_data:
            return staff_data["modules"]

    # RBAC 파일 기준
    rbac_modules = _get_rbac_allowed_modules(role)
    if rbac_modules is not None:
        return rbac_modules

    # config.yaml fallback
    return _get_default_modules_for_role(role)


def _get_default_modules_for_role(role: str) -> List[str]:
    """config.yaml의 default_roles 기준으로 허용 모듈 반환"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    modules = config.get("modules", {})
    return [
        module_id
        for module_id, module_cfg in modules.items()
        if role_has_access(role, module_cfg.get("default_roles", []))
    ]


def save_staff_permissions(staff_id: str, name: str, modules: List[str],
                            role: str = "team_member", team_id: str = "") -> dict:
    """원장/매니저가 직원 모듈 권한 저장"""
    permissions = {}
    if PERMISSIONS_PATH.exists():
        with open(PERMISSIONS_PATH, encoding="utf-8") as f:
            permissions = json.load(f)

    permissions[staff_id] = {
        "name": name,
        "role": role,
        "team_id": team_id,
        "modules": modules,
    }

    with open(PERMISSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(permissions, f, ensure_ascii=False, indent=2)

    return permissions[staff_id]


def get_module_info() -> dict:
    """모든 모듈의 id, name, default_roles 반환 (설정 화면용)"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("modules", {})


def get_rbac_config() -> dict:
    """rbac_permissions.json 전체 반환 (설정 화면용). 없으면 빈 dict."""
    if not RBAC_PATH.exists():
        return {}
    with open(RBAC_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_rbac_config(module_permissions: dict, settings_permissions: dict) -> None:
    """위자드에서 확인된 권한 설정을 rbac_permissions.json에 저장"""
    existing = get_rbac_config()
    existing["module_permissions"] = module_permissions
    existing["settings_permissions"] = settings_permissions

    with open(RBAC_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
