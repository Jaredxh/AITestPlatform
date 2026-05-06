"""Task 11.2 — 周期清理调度器（asyncio 版，无外部依赖）。

为什么不用 APScheduler / ARQ：

- 二期文档明确允许"APScheduler **或** asyncio.create_task 周期循环"。当前
  只有"每 N 小时跑一次清理"这一种 cron 需求，APScheduler 的 cron 表达式 /
  Job Store / Trigger 体系是 over-engineering。
- 进程内 task 跟 FastAPI 生命周期天然耦合：startup 起一个、shutdown 取消，
  没有外部依赖。多 worker 部署时每个 worker 都跑会重复清理，但清理操作是
  幂等的（删已删的文件 = no-op），多跑无害。
- ARQ 留给 Task 11.4（用户主动选择启用，需要"任务跨进程恢复"才有意义）。

未来如果要切到 ARQ / APScheduler，只需把 ``run_cleanup_once`` 提走作为
job entrypoint 即可，业务逻辑不动。

设计要点：
- ``CLEANUP_INTERVAL_HOURS=0`` → 不启动循环。这样压力测试 / 排错可以单独
  关掉，不影响 admin API 的手动触发。
- ``CLEANUP_RUN_ON_STARTUP=False`` 默认。重启风暴时（K8s rolling update）
  避免每个新 pod 都立刻跑一遍清理打挂 DB；想要"首次部署立刻跑"的运维
  显式打开这个开关。
- 单次清理失败不退出循环，只 log + 等下一轮。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.database import async_session_factory
from app.modules.test_data.cleanup import cleanup_orphan_data_files
from app.modules.ui_automation.cleanup import run_ui_cleanup

logger = logging.getLogger(__name__)

# 进程级单例。startup hook 多次调用是 idempotent 的（已有 task 就不再起新的）。
_cleanup_task: asyncio.Task[None] | None = None


async def run_cleanup_once() -> dict[str, Any]:
    """跑一次完整清理（UI media + state + test-data 文件），返回合并 stats。

    给两个 caller 用：
    - ``_cleanup_loop`` 周期调用
    - ``POST /api/admin/ui-media/cleanup`` 手动触发

    每次都开新的 session（避免长 session 持有 PG 连接）。失败不抛，让
    cron 循环和 API 层各自决定怎么响应。
    """
    result: dict[str, Any] = {
        "ui_automation": {},
        "test_data": {},
        "ok": True,
    }

    # ─ UI 自动化媒体 + state ──────────────────────────────────
    try:
        async with async_session_factory() as session:
            ui_counters = await run_ui_cleanup(session)
        result["ui_automation"] = ui_counters.to_dict()
    except Exception as exc:  # noqa: BLE001 — 顶层兜底，cron 不该 crash
        logger.exception("UI cleanup failed")
        result["ui_automation"] = {"error": repr(exc)}
        result["ok"] = False

    # ─ 测试物料文件 ───────────────────────────────────────────
    try:
        async with async_session_factory() as session:
            td_counters = await cleanup_orphan_data_files(session)
            await session.commit()
        result["test_data"] = td_counters.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("test-data cleanup failed")
        result["test_data"] = {"error": repr(exc)}
        result["ok"] = False

    return result


async def _cleanup_loop(interval_seconds: float) -> None:
    """循环主体。第一轮根据 ``CLEANUP_RUN_ON_STARTUP`` 决定是立刻跑还是先 sleep。

    用 ``asyncio.CancelledError`` 触发干净退出（shutdown 时 `task.cancel()`
    会抛进来），其它异常都吞掉 + log，循环继续。
    """
    if settings.CLEANUP_RUN_ON_STARTUP:
        logger.info("Cleanup loop running first cycle on startup")
        try:
            stats = await run_cleanup_once()
            logger.info("Cleanup first cycle: %s", stats)
        except Exception:  # noqa: BLE001
            logger.exception("Cleanup first cycle raised")

    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Cleanup loop cancelled, exiting")
            raise

        try:
            stats = await run_cleanup_once()
            logger.info(
                "Cleanup cycle done: ui=%s td=%s",
                stats.get("ui_automation"),
                stats.get("test_data"),
            )
        except asyncio.CancelledError:
            logger.info("Cleanup loop cancelled mid-cycle, exiting")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Cleanup cycle raised, will retry next interval")


def start_cleanup_scheduler() -> bool:
    """startup 钩子调用。返回 True 表示真的起了 task。

    无副作用条件：
    - ``CLEANUP_INTERVAL_HOURS <= 0`` → 不启动
    - 已经存在活跃 task → 不重复
    """
    global _cleanup_task

    if settings.CLEANUP_INTERVAL_HOURS <= 0:
        logger.info(
            "Cleanup scheduler disabled (CLEANUP_INTERVAL_HOURS=%s)",
            settings.CLEANUP_INTERVAL_HOURS,
        )
        return False

    if _cleanup_task is not None and not _cleanup_task.done():
        logger.debug("Cleanup scheduler already running, skipping start")
        return False

    interval_s = float(settings.CLEANUP_INTERVAL_HOURS) * 3600.0
    _cleanup_task = asyncio.create_task(
        _cleanup_loop(interval_s),
        name="cleanup-cron",
    )
    logger.info(
        "Cleanup scheduler started (interval=%sh, run_on_startup=%s)",
        settings.CLEANUP_INTERVAL_HOURS,
        settings.CLEANUP_RUN_ON_STARTUP,
    )
    return True


async def stop_cleanup_scheduler() -> None:
    """shutdown 钩子。安静地取消 task；已经停了就 no-op。"""
    global _cleanup_task
    task = _cleanup_task
    if task is None or task.done():
        _cleanup_task = None
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        # CancelledError 是预期；其它异常已经在 loop 里 log 过
        pass
    finally:
        _cleanup_task = None
        logger.info("Cleanup scheduler stopped")


__all__ = [
    "run_cleanup_once",
    "start_cleanup_scheduler",
    "stop_cleanup_scheduler",
]
