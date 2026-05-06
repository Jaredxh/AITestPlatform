"""Task 11.2 — UI 自动化清理 cron 单测。

聚焦"可单测"的两块：
1. **纯函数**：``parse_state_file_target`` / ``is_state_file_orphan_or_expired``
   / ``safe_unlink`` —— 不需要 DB / 不需要 mock
2. **文件系统逻辑**：用 ``tmp_path`` 真建文件再调 cleanup helpers，验证
   "改 mtime 的边界 / 引用 vs 孤立 / 不识别命名 fallthrough" 这些规则

DB 聚合（``cleanup_old_media`` 跑真实 SELECT/UPDATE）走集成测试 + 部署后
烟雾验证；这里走文件系统就够覆盖 11.2 的"安全护栏"语义。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.ui_automation.cleanup import (
    is_state_file_orphan_or_expired,
    parse_state_file_target,
    safe_unlink,
)

# ─── parse_state_file_target ────────────────────────────────────────


def test_parse_env_filename() -> None:
    kind, target = parse_state_file_target(
        "env_a1b2c3d4-e5f6-7890-abcd-ef1234567890.json",
    )
    assert kind == "env"
    assert target == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_parse_session_filename() -> None:
    kind, target = parse_state_file_target("session_dev_alice.json")
    assert kind == "session"
    assert target == "dev_alice"


@pytest.mark.parametrize(
    "name",
    [
        "random_garbage.json",
        "env.json",                # 缺 uuid 部分
        "env_abc.txt",             # 后缀错
        "session.json",            # 缺 name
        "..",
        "",
    ],
)
def test_parse_unknown_filename_returns_none(name: str) -> None:
    kind, target = parse_state_file_target(name)
    assert kind is None
    assert target is None


# ─── safe_unlink ─────────────────────────────────────────────────────


def test_safe_unlink_removes_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text("hi")
    assert safe_unlink(f) is True
    assert not f.exists()


def test_safe_unlink_nonexistent_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "missing.txt"
    errors: list[str] = []
    assert safe_unlink(f, on_error=errors) is False
    # 不存在不算错；errors 不应该被填
    assert errors == []


def test_safe_unlink_directory_skipped(tmp_path: Path) -> None:
    """目录不应该被 safe_unlink 删（防止误删父目录）。"""
    d = tmp_path / "subdir"
    d.mkdir()
    assert safe_unlink(d) is False
    assert d.exists()


# ─── is_state_file_orphan_or_expired ─────────────────────────────────


def _set_mtime_days_ago(path: Path, days: float) -> None:
    """把文件 mtime 向过去推 N 天，方便测试边界。"""
    target = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (target, target))


def test_state_file_unrecognized_is_orphan(tmp_path: Path) -> None:
    f = tmp_path / "garbage.json"
    f.write_text("{}")
    cutoff = datetime.now(timezone.utc)
    should_delete, reason = is_state_file_orphan_or_expired(
        f, referenced_env_ids=set(), referenced_sessions=set(), cutoff=cutoff,
    )
    assert should_delete is True
    assert "unrecognized" in reason


def test_state_env_referenced_and_fresh_is_kept(tmp_path: Path) -> None:
    env_id = str(uuid.uuid4())
    f = tmp_path / f"env_{env_id}.json"
    f.write_text("{}")
    _set_mtime_days_ago(f, 0.1)  # 几小时前

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    should_delete, reason = is_state_file_orphan_or_expired(
        f,
        referenced_env_ids={env_id},
        referenced_sessions=set(),
        cutoff=cutoff,
    )
    assert should_delete is False
    assert reason == "active"


def test_state_env_unreferenced_is_orphan(tmp_path: Path) -> None:
    f = tmp_path / f"env_{uuid.uuid4()}.json"
    f.write_text("{}")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    should_delete, reason = is_state_file_orphan_or_expired(
        f,
        referenced_env_ids={"some-other-id"},
        referenced_sessions=set(),
        cutoff=cutoff,
    )
    assert should_delete is True
    assert "no environment" in reason


def test_state_env_referenced_but_expired_is_deleted(tmp_path: Path) -> None:
    """关键边界：被 DB 引用，但 mtime 太老 → 仍要删（让登录态强制刷新）。"""
    env_id = str(uuid.uuid4())
    f = tmp_path / f"env_{env_id}.json"
    f.write_text("{}")
    _set_mtime_days_ago(f, 30)

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    should_delete, reason = is_state_file_orphan_or_expired(
        f,
        referenced_env_ids={env_id},
        referenced_sessions=set(),
        cutoff=cutoff,
    )
    assert should_delete is True
    assert "expired" in reason


def test_state_session_referenced_is_kept(tmp_path: Path) -> None:
    f = tmp_path / "session_dev_alice.json"
    f.write_text("{}")
    _set_mtime_days_ago(f, 0.1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    should_delete, reason = is_state_file_orphan_or_expired(
        f,
        referenced_env_ids=set(),
        referenced_sessions={"dev_alice"},
        cutoff=cutoff,
    )
    assert should_delete is False
    assert reason == "active"


def test_state_session_unreferenced_is_orphan(tmp_path: Path) -> None:
    f = tmp_path / "session_unknown_user.json"
    f.write_text("{}")
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    should_delete, reason = is_state_file_orphan_or_expired(
        f,
        referenced_env_ids=set(),
        referenced_sessions={"someone_else"},
        cutoff=cutoff,
    )
    assert should_delete is True
    assert "session_name" in reason
