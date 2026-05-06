"""Task 11.2 — 测试物料文件清理单测。

聚焦"双条件 AND"语义（孤立 *且* 过期才删）+ 路径规范化匹配。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.test_data.cleanup import is_test_data_file_orphan_expired


def _set_mtime_days_ago(path: Path, days: float) -> None:
    target = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (target, target))


def test_referenced_recent_kept(tmp_path: Path) -> None:
    f = tmp_path / "ref_recent.bin"
    f.write_bytes(b"x")
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    should_delete, reason = is_test_data_file_orphan_expired(
        f, referenced_paths={str(f.resolve())}, cutoff=cutoff,
    )
    assert should_delete is False
    assert "still referenced" in reason


def test_referenced_old_still_kept(tmp_path: Path) -> None:
    """关键安全护栏：物料文件即使非常老，只要还被条目引用，绝不能删。"""
    f = tmp_path / "ref_old.bin"
    f.write_bytes(b"x")
    _set_mtime_days_ago(f, 365)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    should_delete, reason = is_test_data_file_orphan_expired(
        f, referenced_paths={str(f.resolve())}, cutoff=cutoff,
    )
    assert should_delete is False
    assert "still referenced" in reason


def test_orphan_recent_kept(tmp_path: Path) -> None:
    """孤立但还在 retention 内 → 暂留，给"误删想恢复"留窗口。"""
    f = tmp_path / "orphan_recent.bin"
    f.write_bytes(b"x")
    _set_mtime_days_ago(f, 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    should_delete, reason = is_test_data_file_orphan_expired(
        f, referenced_paths=set(), cutoff=cutoff,
    )
    assert should_delete is False
    assert "orphan but recent" in reason


def test_orphan_expired_deleted(tmp_path: Path) -> None:
    """孤立 + 过期 → 删除（这是 cleanup 的本职工作）。"""
    f = tmp_path / "orphan_expired.bin"
    f.write_bytes(b"x")
    _set_mtime_days_ago(f, 120)
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    should_delete, reason = is_test_data_file_orphan_expired(
        f, referenced_paths=set(), cutoff=cutoff,
    )
    assert should_delete is True
    assert "orphan + expired" in reason


def test_path_normalization_relative_vs_absolute(tmp_path: Path) -> None:
    """模拟真实场景：DB 存相对路径（``uploads/test-data/...``），磁盘扫到的是
    绝对路径。这里验证"虽然字符串不同，但 resolve 后能匹配上"——这是
    cleanup 不误删活跃文件的关键防线。"""
    d = tmp_path / "uploads" / "test-data" / "proj1" / "set1"
    d.mkdir(parents=True)
    f = d / "abc.bin"
    f.write_bytes(b"x")
    _set_mtime_days_ago(f, 365)

    # DB 里"存的"路径（相对原 cwd 的形式，这里直接用 absolute 模拟 resolve 后）
    referenced = {str(f.resolve())}

    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    should_delete, reason = is_test_data_file_orphan_expired(
        f, referenced_paths=referenced, cutoff=cutoff,
    )
    assert should_delete is False
    assert "still referenced" in reason
