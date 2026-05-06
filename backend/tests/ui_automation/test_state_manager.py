"""Task 8.1 验证：state_manager 路径计算 + 文件治理。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.modules.ui_automation import state_manager
from app.modules.ui_automation.state_manager import (
    _sanitize_filename_segment,
    ensure_state_dir,
    load_state_or_none,
    mark_state_stale,
    state_path_for,
)


@pytest.fixture
def tmp_state_root(tmp_path: Path, monkeypatch) -> Path:
    """每条测试一个独立的 state 根目录，避免互相污染。"""
    monkeypatch.setattr(state_manager.settings, "UI_STATE_DIR", str(tmp_path / "ui_state"))
    return tmp_path / "ui_state"


def test_state_path_for_uses_env_id_when_no_session(tmp_state_root: Path) -> None:
    eid = uuid.uuid4()
    p = state_path_for(eid)
    assert p.name == f"env_{eid}.json"
    assert p.parent == tmp_state_root


def test_state_path_for_uses_session_when_provided(tmp_state_root: Path) -> None:
    eid = uuid.uuid4()
    p = state_path_for(eid, session_name="staging-admin")
    assert "session_staging-admin.json" in p.name
    # 不应混入 env_id（session 命名意味着多个环境共享）
    assert str(eid) not in p.name


def test_state_path_for_sanitizes_dangerous_session_names(tmp_state_root: Path) -> None:
    """防路径注入：``../`` / 路径分隔符 / 控制字符必须被剥掉。"""
    eid = uuid.uuid4()
    p = state_path_for(eid, session_name="../../etc/passwd")
    # 实际生成的 path 必须仍在 state_root 之下，没逃出去
    assert tmp_state_root in p.parents
    assert ".." not in p.name
    assert "/" not in p.name.replace(p.suffix, "")


def test_load_state_returns_none_when_missing(tmp_state_root: Path) -> None:
    assert load_state_or_none(uuid.uuid4()) is None


def test_load_state_returns_str_path_when_exists(tmp_state_root: Path) -> None:
    eid = uuid.uuid4()
    tmp_state_root.mkdir(parents=True, exist_ok=True)
    target = state_path_for(eid)
    target.write_text('{"cookies": []}')
    out = load_state_or_none(eid)
    assert out == str(target)


async def test_mark_state_stale_removes_existing_file(tmp_state_root: Path) -> None:
    eid = uuid.uuid4()
    tmp_state_root.mkdir(parents=True, exist_ok=True)
    target = state_path_for(eid)
    target.write_text("{}")

    existed, removed = await mark_state_stale(eid)
    assert existed is True
    assert removed is True
    assert not target.exists()


async def test_mark_state_stale_idempotent_when_no_file(tmp_state_root: Path) -> None:
    existed, removed = await mark_state_stale(uuid.uuid4())
    assert existed is False
    assert removed is False


async def test_mark_state_stale_calls_db_callback(tmp_state_root: Path) -> None:
    """db_clear_callback 应当被 await 一次（即便文件不存在）。"""
    called = {"n": 0}

    async def cb() -> None:
        called["n"] += 1

    await mark_state_stale(uuid.uuid4(), db_clear_callback=cb)
    assert called["n"] == 1


async def test_mark_state_stale_handles_session_name(tmp_state_root: Path) -> None:
    """带 session_name 的 stale 必须删对应 session 文件，不动 env 文件。"""
    eid = uuid.uuid4()
    tmp_state_root.mkdir(parents=True, exist_ok=True)
    env_file = state_path_for(eid)
    session_file = state_path_for(eid, session_name="prod")
    env_file.write_text("env-state")
    session_file.write_text("session-state")

    existed, removed = await mark_state_stale(eid, session_name="prod")
    assert existed and removed
    assert not session_file.exists()
    assert env_file.exists(), "env 文件不应被误删"


def test_ensure_state_dir_creates_directory(tmp_state_root: Path) -> None:
    assert not tmp_state_root.exists()
    out = ensure_state_dir()
    assert out == tmp_state_root
    assert tmp_state_root.exists() and tmp_state_root.is_dir()


def test_ensure_state_dir_idempotent(tmp_state_root: Path) -> None:
    ensure_state_dir()
    ensure_state_dir()  # 二次调用不抛错
    assert tmp_state_root.exists()


# ─── _sanitize_filename_segment 边界 ──────────────────────────────────


def test_sanitize_basic_alnum() -> None:
    assert _sanitize_filename_segment("admin-2") == "admin-2"


def test_sanitize_strips_path_separators() -> None:
    assert "/" not in _sanitize_filename_segment("a/b/c")
    assert "\\" not in _sanitize_filename_segment("a\\b\\c")


def test_sanitize_replaces_dots_with_underscores() -> None:
    out = _sanitize_filename_segment("../../etc/passwd")
    assert ".." not in out
    assert "/" not in out


def test_sanitize_falls_back_to_default_for_all_garbage() -> None:
    assert _sanitize_filename_segment("...") == "default"
    assert _sanitize_filename_segment("___") == "default"


def test_sanitize_truncates_very_long_names() -> None:
    long_name = "a" * 500
    out = _sanitize_filename_segment(long_name)
    assert len(out) <= 120
