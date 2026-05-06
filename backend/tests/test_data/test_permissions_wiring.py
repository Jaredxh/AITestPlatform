"""验证 test_data:* 权限常量 + 预置角色配置。

这些 sanity 级别的断言防止"加了权限但忘了赋给任何角色"之类的疏漏。
"""

from __future__ import annotations

from app.modules.auth.permissions import ALL_PERMISSIONS, SYSTEM_ROLES, Permissions


def test_all_test_data_perms_registered() -> None:
    for p in (
        "test_data:view",
        "test_data:edit",
        "test_data:reveal",
        "test_data:import",
    ):
        assert p in ALL_PERMISSIONS, f"{p} not registered"


def test_admin_has_all_test_data_perms() -> None:
    admin_perms = set(SYSTEM_ROLES["admin"]["permissions"])
    for p in (
        Permissions.TEST_DATA_VIEW,
        Permissions.TEST_DATA_EDIT,
        Permissions.TEST_DATA_REVEAL,
        Permissions.TEST_DATA_IMPORT,
    ):
        assert p in admin_perms, p


def test_project_manager_can_reveal() -> None:
    # PM 需要看 / 改 / reveal / 导入
    pm_perms = set(SYSTEM_ROLES["project_manager"]["permissions"])
    assert Permissions.TEST_DATA_REVEAL in pm_perms
    assert Permissions.TEST_DATA_VIEW in pm_perms
    assert Permissions.TEST_DATA_EDIT in pm_perms
    assert Permissions.TEST_DATA_IMPORT in pm_perms


def test_tester_cannot_reveal_by_default() -> None:
    # 测试人员可看 / 改 / 导入，但 reveal 明文需要 admin / PM 级
    tester_perms = set(SYSTEM_ROLES["tester"]["permissions"])
    assert Permissions.TEST_DATA_VIEW in tester_perms
    assert Permissions.TEST_DATA_EDIT in tester_perms
    assert Permissions.TEST_DATA_IMPORT in tester_perms
    assert Permissions.TEST_DATA_REVEAL not in tester_perms


def test_viewer_is_view_only() -> None:
    viewer_perms = set(SYSTEM_ROLES["viewer"]["permissions"])
    assert Permissions.TEST_DATA_VIEW in viewer_perms
    assert Permissions.TEST_DATA_EDIT not in viewer_perms
    assert Permissions.TEST_DATA_IMPORT not in viewer_perms
    assert Permissions.TEST_DATA_REVEAL not in viewer_perms
