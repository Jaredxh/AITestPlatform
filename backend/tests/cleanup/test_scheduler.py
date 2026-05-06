"""Task 11.2 — cleanup scheduler 启停语义测试。

不连真 DB 也不真等周期；用 monkeypatch 把 ``run_cleanup_once`` 替成
即返回的 stub，断言：
- ``CLEANUP_INTERVAL_HOURS=0`` 时不起 task
- 启动时若打开 ``RUN_ON_STARTUP`` 会立刻调一次 stub
- ``stop_cleanup_scheduler`` 能干净地取消 task，没有 pending warning
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.modules.ui_automation import cleanup_scheduler


@pytest.fixture(autouse=True)
def _reset_global_task():
    """每个测试开始前确保 _cleanup_task 是干净的。
    （pytest-asyncio 的 event_loop fixture 会回收 task，这里只是显式标注语义。）"""
    cleanup_scheduler._cleanup_task = None
    yield
    cleanup_scheduler._cleanup_task = None


async def test_disabled_when_interval_zero(monkeypatch):
    monkeypatch.setattr(settings, "CLEANUP_INTERVAL_HOURS", 0)
    started = cleanup_scheduler.start_cleanup_scheduler()
    assert started is False
    assert cleanup_scheduler._cleanup_task is None


async def test_start_then_stop_no_run_on_startup(monkeypatch):
    """interval > 0 + run_on_startup=False：起 task，但因为没到 interval 就被
    立刻 stop，stub 不应该被调过。"""
    monkeypatch.setattr(settings, "CLEANUP_INTERVAL_HOURS", 1)  # 1 小时
    monkeypatch.setattr(settings, "CLEANUP_RUN_ON_STARTUP", False)

    call_count = {"n": 0}

    async def fake_run_once():
        call_count["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(cleanup_scheduler, "run_cleanup_once", fake_run_once)

    started = cleanup_scheduler.start_cleanup_scheduler()
    assert started is True
    assert cleanup_scheduler._cleanup_task is not None

    # 立刻取消；stub 不应该被调（loop 还在第一个 sleep 里）
    await cleanup_scheduler.stop_cleanup_scheduler()
    assert cleanup_scheduler._cleanup_task is None
    assert call_count["n"] == 0


async def test_run_on_startup_invokes_once(monkeypatch):
    """RUN_ON_STARTUP=True：loop 一启动就跑一次 stub，然后再进入 sleep。"""
    monkeypatch.setattr(settings, "CLEANUP_INTERVAL_HOURS", 1)
    monkeypatch.setattr(settings, "CLEANUP_RUN_ON_STARTUP", True)

    invoked = asyncio.Event()
    call_count = {"n": 0}

    async def fake_run_once():
        call_count["n"] += 1
        invoked.set()
        return {"ok": True}

    monkeypatch.setattr(cleanup_scheduler, "run_cleanup_once", fake_run_once)

    cleanup_scheduler.start_cleanup_scheduler()

    # 等 stub 被调过一次（最多 1 秒，快路径毫秒级返回）
    await asyncio.wait_for(invoked.wait(), timeout=1.0)
    assert call_count["n"] == 1

    await cleanup_scheduler.stop_cleanup_scheduler()
    assert cleanup_scheduler._cleanup_task is None


async def test_double_start_idempotent(monkeypatch):
    """同一进程内重复 start（比如 main.py 被 import 两次）应是 idempotent。"""
    monkeypatch.setattr(settings, "CLEANUP_INTERVAL_HOURS", 1)
    monkeypatch.setattr(settings, "CLEANUP_RUN_ON_STARTUP", False)

    async def fake_run_once():
        return {"ok": True}

    monkeypatch.setattr(cleanup_scheduler, "run_cleanup_once", fake_run_once)

    assert cleanup_scheduler.start_cleanup_scheduler() is True
    first_task = cleanup_scheduler._cleanup_task
    # 第二次应当返回 False，且 task 还是同一个
    assert cleanup_scheduler.start_cleanup_scheduler() is False
    assert cleanup_scheduler._cleanup_task is first_task

    await cleanup_scheduler.stop_cleanup_scheduler()


async def test_stop_when_never_started_is_noop():
    """没起过的情况下 stop 也不应该抛。"""
    assert cleanup_scheduler._cleanup_task is None
    await cleanup_scheduler.stop_cleanup_scheduler()  # no raise
