"""Task 9.7 — ExecutionEngine 在 mode=debug 下的暂停-继续行为单测。

复用 test_engine.py 的同款 fake 工具（_FakePersistence / _FakeStream / etc）。
重点验证：
1. 每个 step 完成后发 step_paused，并 await debug_controller.wait_for_continue
2. wait 返回 "continue" → 发 step_resumed → 推进下一步
3. wait 返回 "timeout" → 当前用例 status=skipped，execution.status=stopped，
   后续用例不再跑
4. wait 返回 "stopped" → 同上但 reason 是 user_stop_during_debug
5. mode=normal 时不暂停（不调 wait_for_continue）

stub 策略：自定义 ``_FakeDebugController``，按预设序列 pop outcome，
完全替代真 hub。
"""

from __future__ import annotations

import uuid
from collections import deque
from contextlib import asynccontextmanager  # noqa: F401
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.ui_automation.assertion_judge import AssertionVerdict  # noqa: F401
from app.modules.ui_automation.execution_engine import (
    EngineDeps,
    ExecutionEngine,
    ExecutionInputs,
)
from app.modules.ui_automation.step_runner import StepRunResult

# ─── 复用 test_engine 同款 fake（精简） ────────────────────────────


@dataclass
class _Step:
    step_number: int
    action: str
    expected_result: str | None = None


@dataclass
class _Testcase:
    id: uuid.UUID
    title: str
    steps: list[_Step]
    default_data_set_ids: list[str] = field(default_factory=list)


class _FakeSessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _FakePersistence:
    def __init__(self):
        self.execution_init: list[dict] = []
        self.execution_running: list[dict] = []
        self.executions_flushed: list[dict] = []
        self.cases_created: list[Any] = []
        self.cases_flushed: list[dict] = []
        self.steps_flushed: list[dict] = []
        self.stopped_for: set[uuid.UUID] = set()

    async def init_execution_record(self, **kw):
        self.execution_init.append(kw)
        return SimpleNamespace(id=kw["execution_id"])

    async def mark_execution_running(self, **kw):
        self.execution_running.append(kw)

    async def is_execution_stopped(self, execution_id):
        return execution_id in self.stopped_for

    async def create_case_result(self, **kw):
        case_id = uuid.uuid4()
        row = SimpleNamespace(
            id=case_id,
            execution_id=kw["execution_id"],
            testcase_id=kw["testcase_id"],
            sort_order=kw["sort_order"],
        )
        self.cases_created.append(row)
        return row

    async def flush_step(self, **kw):
        self.steps_flushed.append(kw)
        return uuid.uuid4()

    async def flush_case(self, **kw):
        self.cases_flushed.append(kw)

    async def flush_execution(self, **kw):
        self.executions_flushed.append(kw)


class _FakeStream:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []
        self.done: bool = False

    async def append(self, event, data):
        self.events.append((event, data))

    async def mark_done(self):
        self.done = True


class _FakeStreamHub:
    def __init__(self):
        self.streams: dict[uuid.UUID, _FakeStream] = {}

    async def register(self, execution_id):
        s = _FakeStream()
        self.streams[execution_id] = s
        return s

    def get(self, execution_id):
        return self.streams.get(execution_id)


class _FakeBundle:
    def __init__(self):
        self.execution_id = uuid.uuid4()
        self.mcp_unavailable = True
        self.closed = False

    async def register_mcp_tools_for_agent(self):
        return []

    async def close(self):
        self.closed = True


class _FakeDebugController:
    """按预设 outcome 序列依次返回。"""

    def __init__(self, outcomes: list[str]):
        self.queue: deque[str] = deque(outcomes)
        self.registered: list[uuid.UUID] = []
        self.unregistered: list[uuid.UUID] = []
        self.wait_calls: list[uuid.UUID] = []
        self.signals: list[uuid.UUID] = []

    async def register(self, execution_id):
        self.registered.append(execution_id)
        return SimpleNamespace()

    async def unregister(self, execution_id):
        self.unregistered.append(execution_id)

    async def signal_continue(self, execution_id):
        self.signals.append(execution_id)
        return True

    async def wait_for_continue(self, execution_id, *, timeout, stop_check=None,
                                 poll_interval=0.05):
        self.wait_calls.append(execution_id)
        if not self.queue:
            return "continue"
        return self.queue.popleft()


# ─── 引擎依赖 patch helper ───────────────────────────────────────────


def _patch_resolver_and_loaders(monkeypatch, testcases):
    import app.modules.ui_automation.execution_engine as engine_mod

    state = {"failures": [], "synth": []}

    class _Resolver:
        def __init__(self):
            self.data: dict[str, Any] = {}

        def serialize_for_audit(self, *, configured_set_ids=None):
            return {}

        async def with_case_overrides(self, testcase_id):
            return self

        def reset_case_state(self):
            state["failures"].clear()
            state["synth"].clear()

        def render_template(self, text):
            return text or ""

        def render_manifest_markdown(self):
            return ""

        @property
        def _case_failures(self):
            return state["failures"]

        def finalize_case(self):
            return {
                "synthesized_data": list(state["synth"]),
                "data_failures": list(state["failures"]),
                "data_confidence": (
                    "data_failure" if state["failures"]
                    else "synthesized" if state["synth"]
                    else "reliable"
                ),
            }

    resolver = _Resolver()

    def _coro_return(value):
        async def _():
            return value
        return _()

    monkeypatch.setattr(engine_mod.TestDataResolver, "build", classmethod(
        lambda cls, *, db, execution, manual_overrides, loaded_set_ids:
        _coro_return(resolver),
    ))
    monkeypatch.setattr(engine_mod, "preflight_data_check", AsyncMock(return_value=[]))
    monkeypatch.setattr(engine_mod, "register_data_tools", lambda *a, **kw: [])
    monkeypatch.setattr(engine_mod, "unregister_data_tools", lambda *a, **kw: 0)

    monkeypatch.setattr(
        engine_mod, "_load_environment", AsyncMock(return_value=SimpleNamespace(
            base_url="https://app.example.com",
            allowed_hosts=["app.example.com"],
            token_budget=10_000,
            enable_browser_evaluate=False,
            headless=True,
        )),
    )
    # 用 SimpleNamespace 模拟一个最小 LLMConfig orm —— _build_llm_proto 只看
    # provider/model/api_key_encrypted/base_url/temperature/max_tokens
    monkeypatch.setattr(
        engine_mod, "_load_llm_config",
        AsyncMock(return_value=SimpleNamespace(
            provider="openai",
            model="gpt-4o-mini",
            api_key_encrypted=None,
            base_url=None,
            temperature=0.0,
            max_tokens=2048,
        )),
    )
    monkeypatch.setattr(engine_mod, "_load_testcases", AsyncMock(return_value=list(testcases)))


def _step_run_ok() -> StepRunResult:
    return StepRunResult(
        success=True,
        iterations=1,
        tokens_used=10,
        reasoning="ok",
        final_message="完成",
        tool_calls=[],
        last_snapshot_text="<a/>",
        last_clipped=None,
        error=None,
        error_kind=None,
    )


class _RunnerOk:
    async def run_one(self, **_):
        return _step_run_ok()


class _JudgeOk:
    async def judge(self, **_):
        return AssertionVerdict(passed=True, reason="ok", method="text_search")


def _make_engine_with_debug(monkeypatch, *, outcomes, testcases):
    _patch_resolver_and_loaders(monkeypatch, testcases)
    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    runner = _RunnerOk()
    judge = _JudgeOk()
    debug = _FakeDebugController(outcomes)
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda env, llm, budget, eid: runner,
        assertion_judge_factory=lambda: judge,
        persistence=persistence,
        stream_hub=hub,
        debug_controller=debug,
        debug_timeout_seconds=0.5,  # 短超时，测试不会卡住
    )
    return ExecutionEngine(deps=deps), persistence, hub, debug, bundle


# ─── tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_pauses_after_each_step_and_continues(monkeypatch) -> None:
    """两步用例：每步 step_complete 之后应发 step_paused → wait → step_resumed。"""
    tc = _Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="a"),
        _Step(step_number=2, action="b"),
    ])
    engine, persistence, hub, debug, bundle = _make_engine_with_debug(
        monkeypatch,
        outcomes=["continue", "continue"],
        testcases=[tc],
    )

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
        mode="debug",
    )
    out = await engine.run(inputs)

    assert out.status == "completed"
    assert out.passed == 1
    # debug controller 用对了
    assert debug.registered == [inputs.execution_id]
    assert debug.unregistered == [inputs.execution_id]
    assert len(debug.wait_calls) == 2  # 两步两次

    # 事件链：每步出现 paused + resumed
    events = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert events.count("step_paused") == 2
    assert events.count("step_resumed") == 2
    # 顺序：step_complete → step_paused → step_resumed
    pause_idx = events.index("step_paused")
    complete_idx = events.index("step_complete")
    resume_idx = events.index("step_resumed")
    assert complete_idx < pause_idx < resume_idx


@pytest.mark.asyncio
async def test_debug_timeout_stops_execution(monkeypatch) -> None:
    """wait 第一次就 timeout：执行整体 status=stopped，当前用例 skipped，
    后续步骤不跑，剩余用例计入 skipped。"""
    tc1 = _Testcase(id=uuid.uuid4(), title="tc1", steps=[
        _Step(step_number=1, action="a"),
        _Step(step_number=2, action="b"),
    ])
    tc2 = _Testcase(id=uuid.uuid4(), title="tc2", steps=[_Step(step_number=1, action="x")])
    engine, persistence, hub, debug, _ = _make_engine_with_debug(
        monkeypatch,
        outcomes=["timeout"],
        testcases=[tc1, tc2],
    )

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc1.id, tc2.id],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
        mode="debug",
    )
    out = await engine.run(inputs)

    assert out.status == "stopped"
    # tc1 第一步跑完后 timeout → 第二步不再跑
    # tc1 状态 skipped，tc2 整条没跑也算 skipped
    assert out.skipped >= 1
    # 仅 1 个 case_result 创建（tc2 没机会跑）
    assert len(persistence.cases_created) == 1
    # 仅 1 个 step 落库（tc1.step1）
    assert len(persistence.steps_flushed) == 1

    events = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert "step_paused" in events
    assert "debug_timeout_pending" in events
    # 主流程也发了 debug_timeout 顶层事件
    assert "debug_timeout" in events
    assert "execution_complete" in events

    # 持久化为 stopped
    assert persistence.executions_flushed[-1]["status"] == "stopped"


@pytest.mark.asyncio
async def test_debug_user_stop_during_pause(monkeypatch) -> None:
    """wait 返回 stopped → execution.status=stopped，发 execution_stopped。"""
    tc = _Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="a"),
        _Step(step_number=2, action="b"),
    ])
    engine, persistence, hub, debug, _ = _make_engine_with_debug(
        monkeypatch,
        outcomes=["stopped"],
        testcases=[tc],
    )

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
        mode="debug",
    )
    out = await engine.run(inputs)

    assert out.status == "stopped"
    events = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert "debug_stopped" in events
    assert "execution_stopped" in events
    # case 标 skipped 不是 failed
    assert persistence.cases_flushed[-1]["status"] == "skipped"


@pytest.mark.asyncio
async def test_normal_mode_does_not_pause(monkeypatch) -> None:
    """mode=normal：完全不调 wait_for_continue，也不发 step_paused。"""
    tc = _Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="a"),
        _Step(step_number=2, action="b"),
    ])
    engine, _, hub, debug, _ = _make_engine_with_debug(
        monkeypatch,
        outcomes=[],
        testcases=[tc],
    )

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
        mode="normal",  # 关键
    )
    out = await engine.run(inputs)

    assert out.status == "completed"
    # 关键断言
    assert debug.wait_calls == []
    assert debug.registered == []  # normal 模式连 register 都不调
    events = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert "step_paused" not in events
    assert "step_resumed" not in events
