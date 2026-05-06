"""Task 9.6 — UI 执行权限常量 + 角色映射的 sanity 测试。

防止"加了权限常量但忘了赋给任何角色"或"误删某角色的执行权限"之类的疏漏。
"""

from __future__ import annotations

from app.modules.auth.permissions import ALL_PERMISSIONS, SYSTEM_ROLES, Permissions


def test_exec_perms_registered() -> None:
    for p in ("ui_exec:view", "ui_exec:run", "ui_exec:stop", "ui_exec:debug"):
        assert p in ALL_PERMISSIONS, f"{p} not registered"


def test_admin_has_all_exec_perms() -> None:
    admin = set(SYSTEM_ROLES["admin"]["permissions"])
    for p in (
        Permissions.UI_EXEC_VIEW,
        Permissions.UI_EXEC_RUN,
        Permissions.UI_EXEC_STOP,
        Permissions.UI_EXEC_DEBUG,
    ):
        assert p in admin, p


def test_project_manager_can_run_stop_debug() -> None:
    pm = set(SYSTEM_ROLES["project_manager"]["permissions"])
    assert Permissions.UI_EXEC_VIEW in pm
    assert Permissions.UI_EXEC_RUN in pm
    assert Permissions.UI_EXEC_STOP in pm
    assert Permissions.UI_EXEC_DEBUG in pm


def test_tester_can_run_stop_debug() -> None:
    """测试人员是 UI 测试的核心使用者：必须能 RUN + DEBUG，否则功能形同虚设。"""
    tester = set(SYSTEM_ROLES["tester"]["permissions"])
    assert Permissions.UI_EXEC_VIEW in tester
    assert Permissions.UI_EXEC_RUN in tester
    assert Permissions.UI_EXEC_STOP in tester
    assert Permissions.UI_EXEC_DEBUG in tester


def test_viewer_view_only() -> None:
    """只读用户：只能看历史，不能触发执行也不能停别人，更不能调试。"""
    viewer = set(SYSTEM_ROLES["viewer"]["permissions"])
    assert Permissions.UI_EXEC_VIEW in viewer
    assert Permissions.UI_EXEC_RUN not in viewer
    assert Permissions.UI_EXEC_STOP not in viewer
    assert Permissions.UI_EXEC_DEBUG not in viewer
