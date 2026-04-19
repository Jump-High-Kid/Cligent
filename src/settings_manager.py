"""
settings_manager.py — 설정 페이지 RBAC 접근 제어 및 초기 설정 상태 관리
"""

import json
from pathlib import Path
from typing import Optional

from module_manager import (
    RBAC_PATH,
    ROLE_HIERARCHY,
    get_rbac_config,
    save_rbac_config,
    role_has_access,
)

ROOT = Path(__file__).parent.parent


def is_rbac_initialized() -> bool:
    """rbac_permissions.json이 존재하면 위자드 완료로 간주"""
    return RBAC_PATH.exists()


def get_settings_sections_for_role(role: str) -> list[str]:
    """
    해당 역할이 접근 가능한 설정 섹션 ID 목록 반환.
    rbac_permissions.json 기준, 없으면 빈 리스트.
    """
    rbac = get_rbac_config()
    settings_perms = rbac.get("settings_permissions", {})
    return [
        section_id
        for section_id, perm in settings_perms.items()
        if role_has_access(role, perm.get("allowed_roles", []))
    ]


def can_access_settings(role: str, section: str) -> bool:
    """특정 설정 섹션에 해당 역할이 접근 가능한지 확인"""
    rbac = get_rbac_config()
    settings_perms = rbac.get("settings_permissions", {})
    perm = settings_perms.get(section, {})
    return role_has_access(role, perm.get("allowed_roles", []))


def get_setup_wizard_data() -> dict:
    """
    초기 설정 위자드에 필요한 데이터 반환:
    - roles: 역할 정의 (계층순)
    - module_permissions: 현재 설정 (recommended 기준)
    - settings_permissions: 현재 설정 (recommended 기준)
    """
    rbac = get_rbac_config()
    roles_def = rbac.get("roles", {})
    module_perms = rbac.get("module_permissions", {})
    settings_perms = rbac.get("settings_permissions", {})

    # 역할을 계층 순서로 정렬
    roles_ordered = sorted(
        roles_def.items(),
        key=lambda x: x[1].get("level", 99),
    )

    return {
        "roles": [
            {
                "id": role_id,
                "label": info["label"],
                "level": info["level"],
                "description": info["description"],
            }
            for role_id, info in roles_ordered
        ],
        "role_ids": ROLE_HIERARCHY,
        "module_permissions": module_perms,
        "settings_permissions": settings_perms,
    }


def save_wizard_result(module_permissions: dict, settings_permissions: dict) -> None:
    """위자드 Step 4 저장: 확인된 권한을 rbac_permissions.json에 반영"""
    save_rbac_config(module_permissions, settings_permissions)


def get_team_members(manager_role: str, manager_team_id: Optional[str] = None) -> list[dict]:
    """
    팀 & 권한 관리 화면용 직원 목록 반환.
    매니저는 같은 team_id 직원만, 원장 이상은 전체 반환.
    """
    staff_path = ROOT / "data" / "staff_permissions.json"
    if not staff_path.exists():
        return []

    with open(staff_path, encoding="utf-8") as f:
        data = json.load(f)

    members = []
    for staff_id, info in data.items():
        if staff_id.startswith("_"):
            continue
        # 매니저는 자기 팀만
        if manager_role == "manager" and manager_team_id:
            if info.get("team_id") != manager_team_id:
                continue
        members.append({
            "id": staff_id,
            "name": info.get("name", ""),
            "role": info.get("role", "team_member"),
            "team_id": info.get("team_id", ""),
            "modules": info.get("modules", []),
        })
    return members
