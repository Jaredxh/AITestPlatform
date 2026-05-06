"""Task 11.2 — UI 自动化媒体 / Snapshot / State 清理。

三件事，按"对生产风险递增"排序：

1. **媒体清理** —— 删超过 ``UI_MEDIA_RETENTION_DAYS`` 的 video / trace（execution
   级）+ 每步的 screenshot。删完文件还要把 DB 路径列置 NULL，否则后续
   FileResponse 会报 404 而非"无媒体"。

2. **Snapshot 清理** —— 不删行，只把超过 ``UI_SNAPSHOT_RETENTION_DAYS`` 的
   ``ui_step_results.snapshot_before / snapshot_after / tool_calls`` 大字段
   置 NULL / 空数组。这样 ExecutionDetail 还能看到 step 元信息（status /
   duration / 错误），但不再"重放"详细 a11y 树和 tool 序列。

3. **State 清理** —— 扫 ``UI_STATE_DIR`` 目录，跟 ``ui_test_environments``
   表对比，删除：
   - DB 中没有任何 environment 引用的 state 文件（孤立）
   - mtime 超过 ``UI_STATE_RETENTION_DAYS`` 的 state 文件（即使被引用，也认为
     登录态过期；下次执行会重新走 login）

设计取舍：
- 所有清理都设计成**幂等 + 不阻断主流程**：单个文件删失败只 warning + skip，
  整个清理任务不抛异常。这样 cron 跑挂不会影响业务请求。
- 时间口径用 ``ui_executions.completed_at`` 而非 ``created_at``：未完成的
  执行（可能 running 了几小时）我们不删它的媒体。完成时间是真正的"用完了"
  时刻，跟用户的 retention 直觉一致。
- 不递归删 ``ui_artifacts/`` 整个目录树，只按 DB 索引到的具体路径删。这样
  即使底层路径策略变了（比如以后改成 hash 分桶），cleanup 也不会误删别人的
  文件。代价是"DB 删了但路径没记"的孤立文件不会被回收 —— 这种情况由 Task
  11.3 的部署文档里的"年度物理重置"覆盖。
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.ui_automation.models import (
    TestEnvironment,
    UICaseResult,
    UIExecution,
    UIStepResult,
)

logger = logging.getLogger(__name__)


# ─── 结果数据结构 ────────────────────────────────────────────────────────


@dataclass
class CleanupCounters:
    """单次清理的统计；admin API 直接返回这个 dict 给前端展示。"""

    executions_scanned: int = 0
    """扫到的 execution 行（≤ retention 之前完成的）。"""

    videos_deleted: int = 0
    traces_deleted: int = 0
    screenshots_deleted: int = 0

    snapshot_steps_cleared: int = 0
    """snapshot_before/after + tool_calls 被清空的 step 数。"""

    state_files_deleted: int = 0
    state_files_kept: int = 0
    """state 目录里仍被 DB 引用 + 未过期的文件数。"""

    errors: list[str] = field(default_factory=list)
    """每个失败动作记一行；失败不会抛，只汇总。"""

    def to_dict(self) -> dict:
        return {
            "executions_scanned": self.executions_scanned,
            "videos_deleted": self.videos_deleted,
            "traces_deleted": self.traces_deleted,
            "screenshots_deleted": self.screenshots_deleted,
            "snapshot_steps_cleared": self.snapshot_steps_cleared,
            "state_files_deleted": self.state_files_deleted,
            "state_files_kept": self.state_files_kept,
            "errors": self.errors[:50],  # 截断防 payload 爆炸
            "error_count": len(self.errors),
        }


# ─── 纯文件系统 helper（独立可单测）─────────────────────────────────────


def safe_unlink(path: str | Path, *, on_error: list[str] | None = None) -> bool:
    """删一个文件，文件不存在 / OSError 都不抛，只记 log + 可选 errors 列表。

    返回 True 表示真的删除了一个存在的文件；False 表示要么文件不存在、
    要么删失败（已记录）。
    """
    p = Path(path)
    try:
        if not p.exists():
            return False
        if not p.is_file():
            # 不是普通文件就 skip（可能是 symlink 之外的特殊文件，避免误删）
            return False
        p.unlink()
        return True
    except OSError as exc:
        msg = f"unlink failed: {p} ({exc})"
        logger.warning(msg)
        if on_error is not None:
            on_error.append(msg)
        return False


def parse_state_file_target(filename: str) -> tuple[str | None, str | None]:
    """从 state 文件名反推它属于哪个 environment 或 session。

    state_manager 的命名规则：
    - ``env_<uuid>.json`` → ("env", uuid_str)
    - ``session_<safe_name>.json`` → ("session", safe_name)
    - 其他不识别 → (None, None)，调用方按"无主"处理

    返回的 uuid_str 不做合法性校验，调用方再 try/except UUID 解析。
    这样测试里可以构造任意串验证 fallthrough 行为。
    """
    m = re.match(r"^env_([0-9a-fA-F-]+)\.json$", filename)
    if m:
        return ("env", m.group(1))
    m = re.match(r"^session_(.+)\.json$", filename)
    if m:
        return ("session", m.group(1))
    return (None, None)


def is_state_file_orphan_or_expired(
    file_path: Path,
    *,
    referenced_env_ids: set[str],
    referenced_sessions: set[str],
    cutoff: datetime,
) -> tuple[bool, str]:
    """判断单个 state 文件是否该被清理。

    返回 ``(should_delete, reason)``。理由用于日志/统计，让运维知道为什么
    被删（"过期"还是"孤立"），而不是黑盒"反正没了"。

    优先级：先看是否孤立（DB 没引用）→ 再看是否过期。两者满足任一就删。
    """
    kind, target = parse_state_file_target(file_path.name)

    if kind is None:
        # 不识别命名格式 → 极可能是历史文件 / 手工放的，按孤立处理
        return (True, "unrecognized filename pattern")

    if kind == "env":
        if target not in referenced_env_ids:
            return (True, "no environment in DB references this state")
    elif kind == "session" and target not in referenced_sessions:
        return (True, "no environment in DB uses this session_name")

    # 文件存在 + 被引用 → 看 mtime 决定是否过期
    try:
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        # stat 失败按"过期"处理（更安全，下次重新建）
        return (True, "stat failed")

    if mtime < cutoff:
        return (True, f"expired (mtime={mtime.isoformat()})")
    return (False, "active")


# ─── DB + 文件系统组合：媒体 + snapshot ─────────────────────────────────


def _utcnow() -> datetime:
    """单独抽出来方便测试 monkeypatch；用 timezone-aware UTC 跟 DB 列保持一致。"""
    return datetime.now(timezone.utc)


async def cleanup_old_media(
    db: AsyncSession,
    *,
    media_retention_days: int | None = None,
    snapshot_retention_days: int | None = None,
    counters: CleanupCounters | None = None,
) -> CleanupCounters:
    """删过期视频 / trace / 截图，清空过期 snapshot / tool_calls 字段。

    Args:
        media_retention_days: 媒体文件保留天数；None 取 settings.UI_MEDIA_RETENTION_DAYS
        snapshot_retention_days: snapshot 字段保留天数；None 取 settings.UI_SNAPSHOT_RETENTION_DAYS

    Returns:
        CleanupCounters，含每类资源的统计。
    """
    counters = counters or CleanupCounters()
    media_days = (
        media_retention_days
        if media_retention_days is not None
        else settings.UI_MEDIA_RETENTION_DAYS
    )
    snap_days = (
        snapshot_retention_days
        if snapshot_retention_days is not None
        else settings.UI_SNAPSHOT_RETENTION_DAYS
    )

    now = _utcnow()
    media_cutoff = now - timedelta(days=media_days)
    snapshot_cutoff = now - timedelta(days=snap_days)

    # ─ 1. 媒体清理 ────────────────────────────────────────────
    # 只看已完成的执行；运行中的 execution 哪怕 created_at 很久也不动
    exec_q = select(UIExecution).where(
        UIExecution.completed_at.is_not(None),
        UIExecution.completed_at < media_cutoff,
    )
    executions = (await db.execute(exec_q)).scalars().all()
    counters.executions_scanned = len(executions)

    for exe in executions:
        if exe.video_path and safe_unlink(exe.video_path, on_error=counters.errors):
            counters.videos_deleted += 1
        if exe.trace_path and safe_unlink(exe.trace_path, on_error=counters.errors):
            counters.traces_deleted += 1
        # 路径列清空——即便文件原本就不存在；保持 DB 与磁盘一致
        if exe.video_path or exe.trace_path:
            exe.video_path = None
            exe.trace_path = None

    if executions:
        # step screenshot：通过 case_results → step_results 一次拉到所有路径
        exec_ids = [e.id for e in executions]
        step_q = (
            select(UIStepResult.id, UIStepResult.screenshot_path)
            .join(UICaseResult, UIStepResult.case_result_id == UICaseResult.id)
            .where(
                UICaseResult.execution_id.in_(exec_ids),
                UIStepResult.screenshot_path.is_not(None),
            )
        )
        step_rows = (await db.execute(step_q)).all()

        deleted_step_ids: list[uuid.UUID] = []
        for step_id, scr_path in step_rows:
            if scr_path and safe_unlink(scr_path, on_error=counters.errors):
                counters.screenshots_deleted += 1
            deleted_step_ids.append(step_id)

        if deleted_step_ids:
            await db.execute(
                update(UIStepResult)
                .where(UIStepResult.id.in_(deleted_step_ids))
                .values(screenshot_path=None),
            )

    # ─ 2. Snapshot / tool_calls 清空 ──────────────────────────
    # 这一步独立于"媒体过期"——snapshot 一般保留更短（默认 7 天），可能在媒体
    # 还在的时候就先清掉详细字段。覆盖范围跟媒体清理可能重叠；UPDATE 是
    # 幂等的（再清一次不会多坏），不做去重。
    snap_step_q = (
        select(UIStepResult.id)
        .join(UICaseResult, UIStepResult.case_result_id == UICaseResult.id)
        .join(UIExecution, UICaseResult.execution_id == UIExecution.id)
        .where(
            UIExecution.completed_at.is_not(None),
            UIExecution.completed_at < snapshot_cutoff,
            # 至少有一项不空才需要清，避免无脑 update 全表
            (
                UIStepResult.snapshot_before.is_not(None)
                | UIStepResult.snapshot_after.is_not(None)
            ),
        )
    )
    snap_step_ids = (await db.execute(snap_step_q)).scalars().all()
    if snap_step_ids:
        await db.execute(
            update(UIStepResult)
            .where(UIStepResult.id.in_(snap_step_ids))
            .values(
                snapshot_before=None,
                snapshot_after=None,
                # tool_calls 用 [] 而非 None 因为列是 NOT NULL（server_default '[]')
                tool_calls=[],
            ),
        )
        counters.snapshot_steps_cleared = len(snap_step_ids)

    return counters


# ─── State 文件清理（不依赖具体 ORM，可独立调用）──────────────────────


async def cleanup_orphan_state_files(
    db: AsyncSession,
    *,
    state_retention_days: int | None = None,
    state_dir: Path | None = None,
    counters: CleanupCounters | None = None,
) -> CleanupCounters:
    """扫 state 目录，删孤立 / 过期文件。

    判定见 ``is_state_file_orphan_or_expired``：要么 DB 没引用、要么 mtime
    超过 retention。被引用且未过期的文件不动。
    """
    counters = counters or CleanupCounters()
    days = (
        state_retention_days
        if state_retention_days is not None
        else settings.UI_STATE_RETENTION_DAYS
    )
    root = state_dir or Path(settings.UI_STATE_DIR)
    if not root.exists() or not root.is_dir():
        # 还没有 state 目录 = 还没人用 → no-op
        return counters

    # DB 中所有 environment 的 id + session_name；用 set 加速 O(1) 命中
    env_q = select(TestEnvironment.id, TestEnvironment.session_name)
    rows = (await db.execute(env_q)).all()
    referenced_env_ids: set[str] = {str(r[0]) for r in rows}
    referenced_sessions: set[str] = {
        _sanitize_session_for_match(r[1]) for r in rows if r[1]
    }

    cutoff = _utcnow() - timedelta(days=days)

    for child in _iter_state_files(root):
        should_delete, _reason = is_state_file_orphan_or_expired(
            child,
            referenced_env_ids=referenced_env_ids,
            referenced_sessions=referenced_sessions,
            cutoff=cutoff,
        )
        if should_delete:
            if safe_unlink(child, on_error=counters.errors):
                counters.state_files_deleted += 1
        else:
            counters.state_files_kept += 1

    return counters


def _iter_state_files(root: Path) -> Iterable[Path]:
    """state 目录下只看顶层 ``*.json`` 文件，不递归。state_manager 也不会
    在 root 下建子目录 —— 子目录视为外部内容、不动。"""
    try:
        return [p for p in root.iterdir() if p.is_file() and p.suffix == ".json"]
    except OSError as exc:
        logger.warning("state dir iter failed: %s (%s)", root, exc)
        return []


def _sanitize_session_for_match(name: str) -> str:
    """跟 state_manager._sanitize_filename_segment 同口径，让"DB 里的
    session_name → 文件名 token"匹配；防止口径漂移导致误删。"""
    safe = []
    for ch in name:
        if ch.isalnum() or ch in ("_", "-"):
            safe.append(ch)
        else:
            safe.append("_")
    cleaned = "".join(safe).strip("._")
    if not cleaned:
        cleaned = "default"
    return cleaned[:120]


# ─── 顶层调度入口 ────────────────────────────────────────────────────────


async def run_ui_cleanup(
    db: AsyncSession,
    *,
    media_days: int | None = None,
    snapshot_days: int | None = None,
    state_days: int | None = None,
) -> CleanupCounters:
    """一次跑完三件清理 + 提交事务。供 cron 与 admin API 共用。

    任意一个子任务抛异常都会被本函数兜住，错误进 counters.errors，整个
    事务最终仍然 commit（已经做了的清理不应该因为后面失败而回滚）。
    """
    counters = CleanupCounters()
    try:
        await cleanup_old_media(
            db,
            media_retention_days=media_days,
            snapshot_retention_days=snapshot_days,
            counters=counters,
        )
    except Exception as exc:  # noqa: BLE001 — cron 层兜底
        logger.exception("cleanup_old_media failed")
        counters.errors.append(f"media: {exc!r}")

    try:
        await cleanup_orphan_state_files(
            db,
            state_retention_days=state_days,
            counters=counters,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cleanup_orphan_state_files failed")
        counters.errors.append(f"state: {exc!r}")

    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("cleanup commit failed")
        counters.errors.append(f"commit: {exc!r}")
        await db.rollback()

    return counters


__all__ = [
    "CleanupCounters",
    "cleanup_old_media",
    "cleanup_orphan_state_files",
    "is_state_file_orphan_or_expired",
    "parse_state_file_target",
    "run_ui_cleanup",
    "safe_unlink",
]
