"""Task 11.2 — 测试物料文件清理。

仅清理"孤立物理文件"——DB 里 ``test_data_items.file_path`` 引用过、但条目
被删后磁盘文件还在的那种。删除条目时 service 层并不会立刻删文件（防止
误操作 / 撤销）；这里给一个延迟回收窗口。

孤立判定：
1. 文件位于 ``TEST_DATA_UPLOAD_DIR`` 下
2. 不在任何活跃 ``test_data_items.file_path`` 集合里
3. mtime 超过 ``TEST_DATA_FILE_RETENTION_DAYS`` 天

设计取舍：
- **不**删整个空目录（虽然方便，但物料 set 删除时业务层有自己的子目录清理
  钩子；这里再插手容易跟它冲突）。
- 路径口径用 ``Path.resolve()`` 后比对，避免相对/绝对差异导致误判活跃文件
  也被删。这个细节非常关键 —— service 层存的是相对仓库根的路径
  （``uploads/test-data/...``），cron 跑起来时 cwd 不一定是仓库根。
- 跟 ui media 一样：失败不抛，错误进 counters。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.test_data.models import TestDataItem
from app.modules.ui_automation.cleanup import safe_unlink

logger = logging.getLogger(__name__)


@dataclass
class TestDataCleanupCounters:
    """物料清理统计；admin API 直接 return to_dict()。"""

    files_scanned: int = 0
    files_deleted: int = 0
    files_kept_referenced: int = 0
    """虽然过期，但仍被某条 ``test_data_items.file_path`` 引用 → 不动。"""
    files_kept_recent: int = 0
    """没被引用，但 mtime 还在 retention 内 → 暂留，给"误删想恢复"留窗口。"""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_deleted": self.files_deleted,
            "files_kept_referenced": self.files_kept_referenced,
            "files_kept_recent": self.files_kept_recent,
            "errors": self.errors[:50],
            "error_count": len(self.errors),
        }


def _utcnow() -> datetime:
    """单独抽出来方便测试 monkeypatch。"""
    return datetime.now(timezone.utc)


def _normalize_for_match(p: str | Path) -> str:
    """规范化路径用于"是否被引用"匹配。

    DB 里存的是相对路径（``uploads/test-data/...``），磁盘扫到的是绝对
    路径。统一 ``Path.resolve()`` 转绝对再做字符串比较，避免 cwd 不一致
    导致的误判。
    """
    try:
        return str(Path(p).resolve())
    except OSError:
        return str(p)


def _iter_files_recursive(root: Path) -> Iterable[Path]:
    """递归列出所有普通文件。``Path.rglob('*')`` 会带目录，过滤一下。
    OSError（权限 / IO）静默跳过单条目，不让整轮 cleanup 挂掉。"""
    try:
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    yield p
            except OSError:
                continue
    except OSError as exc:
        logger.warning("test-data dir walk failed: %s (%s)", root, exc)


def is_test_data_file_orphan_expired(
    file_path: Path,
    *,
    referenced_paths: set[str],
    cutoff: datetime,
) -> tuple[bool, str]:
    """判断单个物料文件是否该被清理。

    返回 ``(should_delete, reason)``。两个条件**与**关系：必须既无引用 *又*
    过期。被引用的"过期"文件不能删（条目还有用）；未过期的"孤立"文件也
    不能删（用户可能刚误删条目想恢复）。
    """
    norm = _normalize_for_match(file_path)
    if norm in referenced_paths:
        return (False, "still referenced by test_data_items")

    try:
        mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        # stat 失败按"过期"处理，让 unlink 再试一次（unlink 失败会被记录）
        return (True, "stat failed")

    if mtime >= cutoff:
        return (False, f"orphan but recent (mtime={mtime.isoformat()})")
    return (True, f"orphan + expired (mtime={mtime.isoformat()})")


async def cleanup_orphan_data_files(
    db: AsyncSession,
    *,
    retention_days: int | None = None,
    upload_dir: Path | None = None,
    counters: TestDataCleanupCounters | None = None,
) -> TestDataCleanupCounters:
    """扫物料上传目录，删孤立 + 过期的物理文件。

    Args:
        retention_days: 保留天数；None 取 settings.TEST_DATA_FILE_RETENTION_DAYS
        upload_dir: 上传根目录；None 取 settings.TEST_DATA_UPLOAD_DIR
    """
    counters = counters or TestDataCleanupCounters()
    days = (
        retention_days
        if retention_days is not None
        else settings.TEST_DATA_FILE_RETENTION_DAYS
    )
    root = upload_dir or Path(settings.TEST_DATA_UPLOAD_DIR)
    if not root.exists() or not root.is_dir():
        # 还没人传过文件 → no-op
        return counters

    # DB 中所有 file_path 不空的条目；构造已规范化的引用集合
    file_q = select(TestDataItem.file_path).where(
        TestDataItem.file_path.is_not(None),
    )
    raw_paths = (await db.execute(file_q)).scalars().all()
    referenced: set[str] = {
        _normalize_for_match(p) for p in raw_paths if p
    }

    cutoff = _utcnow() - timedelta(days=days)

    for f in _iter_files_recursive(root):
        counters.files_scanned += 1
        should_delete, reason = is_test_data_file_orphan_expired(
            f, referenced_paths=referenced, cutoff=cutoff,
        )
        if should_delete:
            if safe_unlink(f, on_error=counters.errors):
                counters.files_deleted += 1
        elif reason.startswith("still referenced"):
            counters.files_kept_referenced += 1
        else:
            counters.files_kept_recent += 1

    return counters


__all__ = [
    "TestDataCleanupCounters",
    "cleanup_orphan_data_files",
    "is_test_data_file_orphan_expired",
]
