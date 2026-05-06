"""管理员专用 API（Task 11.2 起步）。

目前只有清理触发；后续可以放迁移触发、用户/角色批量管理等需要 superuser
保护的运维入口。

约定：
- 所有 endpoint 走 ``/api/admin/...`` 前缀
- 全部要求 ``current_user.is_superuser``（用 ``require_superuser`` 守卫）
- 失败响应仍走全局 AppException → 标准 ``{success, code, message}``

不放 ui_automation/router.py 的原因：admin 类 endpoint 跟具体业务模块解耦，
集中放一个文件更便于审计 + 给运维写 README。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.core.exceptions import PermissionDeniedException
from app.core.response import success_response
from app.modules.auth.models import User
from app.modules.ui_automation.cleanup_scheduler import run_cleanup_once

router = APIRouter(prefix="/api/admin", tags=["管理员"])


def require_superuser(current_user: User = Depends(get_current_user)) -> User:
    """只有 ``is_superuser`` 才能进——比 require_permission 更严苛，因为
    admin endpoint 的"破坏面"通常是全局的（删媒体、清 state），不希望某个
    被授予了细粒度权限的角色误触发。"""
    if not current_user.is_superuser:
        raise PermissionDeniedException("仅超级管理员可执行此操作")
    return current_user


@router.post("/ui-media/cleanup", response_model=dict)
async def trigger_ui_media_cleanup(
    media_days: int | None = Query(
        None, ge=0,
        description="覆盖默认 UI_MEDIA_RETENTION_DAYS；0 = 立刻清理所有",
    ),
    snapshot_days: int | None = Query(
        None, ge=0,
        description="覆盖默认 UI_SNAPSHOT_RETENTION_DAYS",
    ),
    state_days: int | None = Query(
        None, ge=0,
        description="覆盖默认 UI_STATE_RETENTION_DAYS",
    ),
    test_data_days: int | None = Query(
        None, ge=0,
        description="覆盖默认 TEST_DATA_FILE_RETENTION_DAYS",
    ),
    _user: User = Depends(require_superuser),
):
    """手动触发清理任务（与 cron 同入口）。

    用法（典型）：
    - 不传参 → 用 .env 配置的 retention 跑一次
    - ``?media_days=0`` → 强制清光所有视频/截图（运维灾难恢复）
    - ``?test_data_days=0`` → 立刻回收所有孤立物料文件

    返回：
    ```json
    {
      "success": true,
      "data": {
        "ui_automation": { ... CleanupCounters ... },
        "test_data": { ... TestDataCleanupCounters ... },
        "ok": true,
      }
    }
    ```
    任意一类失败 ``ok`` 会变 false，但 HTTP 仍 200——避免 cron 客户端
    脚本因为单条记录删失败而把整次清理重试。
    """
    # 注意：query 参数只覆盖 ui media + snapshot + state；test_data_days 走
    # cleanup_orphan_data_files 的 ``retention_days``，但目前 run_cleanup_once
    # 不接受参数，统一走 settings。下面在 schedule 之外二次调用一次让
    # test_data_days 生效。
    from app.database import async_session_factory
    from app.modules.test_data.cleanup import cleanup_orphan_data_files
    from app.modules.ui_automation.cleanup import run_ui_cleanup

    result: dict = {"ui_automation": {}, "test_data": {}, "ok": True}

    try:
        async with async_session_factory() as session:
            ui_counters = await run_ui_cleanup(
                session,
                media_days=media_days,
                snapshot_days=snapshot_days,
                state_days=state_days,
            )
        result["ui_automation"] = ui_counters.to_dict()
    except Exception as exc:  # noqa: BLE001
        result["ui_automation"] = {"error": repr(exc)}
        result["ok"] = False

    try:
        async with async_session_factory() as session:
            td_counters = await cleanup_orphan_data_files(
                session, retention_days=test_data_days,
            )
            await session.commit()
        result["test_data"] = td_counters.to_dict()
    except Exception as exc:  # noqa: BLE001
        result["test_data"] = {"error": repr(exc)}
        result["ok"] = False

    return success_response(data=result)


__all__ = ["router", "run_cleanup_once"]
