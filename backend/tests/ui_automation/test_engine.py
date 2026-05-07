"""Task 9.5 — ExecutionEngine 编排单测。

策略：
- ``EngineDeps`` 全替：``db_session_factory`` / ``open_browser_bundle`` /
  ``step_runner_factory`` / ``assertion_judge_factory`` / ``persistence``
  / ``stream_hub``
- 不连真 PG / 真 Playwright / 真 LLM
- 重点验证：
    1. 全用例通过 → execution.status=completed，passed 计数正确
    2. 单 case data_failure → 该 case error，但**整批继续**，下条 case 正常
    3. preflight 缺料 + strict_data_mode → 直接拒绝执行
    4. step token 超预算 → execution.status=aborted_budget，剩余 case 跳过
    5. assertion_passed=False → case status=failed，整批继续
    6. SSE 事件链完整：execution_started / case_started / step_complete /
       case_complete / execution_complete
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.ui_automation.assertion_judge import AssertionVerdict
from app.modules.ui_automation.execution_engine import (
    EngineDeps,
    ExecutionEngine,
    ExecutionInputs,
)
from app.modules.ui_automation.preflight import MissingDataAlert
from app.modules.ui_automation.step_runner import StepRunResult

# ─── 通用 stub 工具 ──────────────────────────────────────────────────


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
    """只把任何 with-block 的 db 包成"什么都没"。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _FakePersistence:
    """收集所有写动作，最后比对。"""

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


def _patch_resolver(monkeypatch, resolver_factory):
    """把 ``TestDataResolver.build`` / ``preflight_data_check`` 全部替换成桩。"""
    import app.modules.ui_automation.execution_engine as engine_mod

    async def fake_build(*, db, execution, manual_overrides, loaded_set_ids):
        return resolver_factory()

    monkeypatch.setattr(engine_mod.TestDataResolver, "build", classmethod(
        lambda cls, *, db, execution, manual_overrides, loaded_set_ids:
        _coro_return(resolver_factory()),
    ))
    monkeypatch.setattr(engine_mod, "preflight_data_check", AsyncMock(return_value=[]))
    # 物料工具注册成 no-op，避免污染 TOOL_REGISTRY
    monkeypatch.setattr(engine_mod, "register_data_tools", lambda *a, **kw: [])
    monkeypatch.setattr(engine_mod, "unregister_data_tools", lambda *a, **kw: 0)


def _coro_return(value):
    async def _():
        return value
    return _()


def _make_resolver_stub(*, with_case_overrides_self_ok: bool = True):
    """构造一个支持本测试需要的最小 resolver 桩。"""
    state = {"failures": [], "synth": []}

    class _Resolver:
        def __init__(self):
            self.data: dict[str, Any] = {}

        def serialize_for_audit(self, *, configured_set_ids=None):
            return {}

        async def with_case_overrides(self, testcase_id):
            if with_case_overrides_self_ok:
                return self
            raise RuntimeError("with_case_overrides forbidden by test")

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

    return _Resolver(), state


def _make_llm_config_stub():
    """最小 LLMConfig stub —— ``_build_llm_proto`` 只读这几个属性。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        provider="openai",
        model="gpt-4o-mini",
        api_key_encrypted=None,
        base_url=None,
        temperature=0.0,
        max_tokens=2048,
    )


def _patch_db_loaders(monkeypatch, *, env=None, llm_config=None, testcases=None):
    import app.modules.ui_automation.execution_engine as engine_mod

    monkeypatch.setattr(
        engine_mod, "_load_environment", AsyncMock(return_value=env or _make_env_stub()),
    )
    monkeypatch.setattr(
        engine_mod, "_load_llm_config",
        AsyncMock(return_value=llm_config if llm_config is not None else _make_llm_config_stub()),
    )
    monkeypatch.setattr(
        engine_mod, "_load_testcases", AsyncMock(return_value=list(testcases or [])),
    )


def _make_env_stub(*, token_budget=10_000):
    return SimpleNamespace(
        base_url="https://app.example.com",
        allowed_hosts=["app.example.com"],
        token_budget=token_budget,
        enable_browser_evaluate=False,
        headless=True,
    )


def _step_run_ok(*, snapshot="- main\n  - text 'ok'", tokens=10) -> StepRunResult:
    return StepRunResult(
        success=True,
        iterations=1,
        tokens_used=tokens,
        reasoning="reasoned",
        final_message="完成",
        tool_calls=[],
        last_snapshot_text=snapshot,
        last_clipped=None,
        error=None,
        error_kind=None,
    )


class _FakeStepRunner:
    """按预设响应。可记录调用入参。"""

    __test__ = False

    def __init__(self, results: list[StepRunResult] | None = None):
        self._results = list(results or [])
        self.calls: list[dict] = []

    async def run_one(self, **kwargs):
        self.calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return _step_run_ok()


class _FakeJudge:
    """按预设响应；默认全部通过。"""

    __test__ = False

    def __init__(self, verdicts: list[AssertionVerdict] | None = None):
        self._verdicts = list(verdicts or [])
        self.calls: list[dict] = []

    async def judge(self, **kwargs):
        self.calls.append(kwargs)
        if self._verdicts:
            return self._verdicts.pop(0)
        return AssertionVerdict(passed=True, reason="ok", method="text_search")


# ─── 1) 全部用例通过 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_all_cases_pass(monkeypatch) -> None:
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action=f"action {i}", expected_result="ok"),
        ])
        for i in range(2)
    ]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    runner = _FakeStepRunner()
    judge = _FakeJudge()

    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda env, llm, budget, eid: runner,
        assertion_judge_factory=lambda: judge,
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await engine.run(inputs)

    assert out.status == "completed"
    assert out.passed == 2
    assert out.failed == 0
    assert out.skipped == 0
    assert len(persistence.cases_created) == 2
    assert len(persistence.cases_flushed) == 2
    assert all(c["status"] == "passed" for c in persistence.cases_flushed)
    assert all(c["data_confidence"] == "reliable" for c in persistence.cases_flushed)
    assert bundle.closed is True

    # 事件链至少包含主要节点
    event_names = [e for e, _ in hub.streams[inputs.execution_id].events]
    expected_events = (
        "execution_started",
        "bundle_ready",
        "case_started",
        "step_complete",
        "case_complete",
        "execution_complete",
    )
    for name in expected_events:
        assert name in event_names


# ─── 多用例切换：reset_for_next_case 行为 ────────────────────────────


@pytest.mark.asyncio
async def test_reset_called_between_cases_only(monkeypatch) -> None:
    """3 条用例 → reset_for_next_case 必须被调用 2 次（在第 2、3 条之前）。

    第一条用例不需要 reset（bundle 刚 open，浏览器是干净的）；从第二条起每次
    切换前都要清掉残留 page 状态，否则上条用例的弹窗 / SPA 路由会污染下条。
    """
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action=f"action {i}", expected_result="ok"),
        ])
        for i in range(3)
    ]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    class _BundleWithReset(_FakeBundle):
        def __init__(self):
            super().__init__()
            self.reset_calls: list[int] = []  # 记录调用时刻（按顺序的下标）

        async def reset_for_next_case(self):
            self.reset_calls.append(len(self.reset_calls))
            return {"closed_extra_pages": 0, "navigated_to_blank": True, "errors": []}

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _BundleWithReset()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda env, llm, budget, eid: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)

    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await engine.run(inputs)

    assert out.status == "completed"
    assert len(bundle.reset_calls) == 2, (
        f"3 条用例应触发 2 次 reset（在第 2、3 条之前），实际 {len(bundle.reset_calls)} 次"
    )

    # 事件流里必须出现对应数量的 case_reset
    event_names = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert event_names.count("case_reset") == 2

    # 重要时序约束：每个 case_reset 必须紧邻在对应 case_started 之前
    # （否则前端 timeline 会乱序，且语义上 reset 应该是"为下一条用例准备"）
    seq = [
        e for e, _ in hub.streams[inputs.execution_id].events
        if e in ("case_reset", "case_started")
    ]
    # 期望：[case_started, case_reset, case_started, case_reset, case_started]
    assert seq == [
        "case_started", "case_reset",
        "case_started", "case_reset",
        "case_started",
    ], f"事件时序异常：{seq}"


@pytest.mark.asyncio
async def test_reset_skipped_for_single_case_run(monkeypatch) -> None:
    """单条用例 → reset_for_next_case **不**应该被调用（没有"下一条"要清）。"""
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tc = _Testcase(id=uuid.uuid4(), title="solo", steps=[
        _Step(step_number=1, action="x", expected_result="ok"),
    ])
    _patch_db_loaders(monkeypatch, testcases=[tc])

    class _BundleWithReset(_FakeBundle):
        def __init__(self):
            super().__init__()
            self.reset_count = 0

        async def reset_for_next_case(self):
            self.reset_count += 1
            return {}

    bundle = _BundleWithReset()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda env, llm, budget, eid: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=_FakePersistence(),
        stream_hub=_FakeStreamHub(),
    )
    engine = ExecutionEngine(deps=deps)
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )

    out = await engine.run(inputs)
    assert out.status == "completed"
    assert bundle.reset_count == 0, "单条用例不应触发 reset_for_next_case"


@pytest.mark.asyncio
async def test_reset_failure_does_not_abort_batch(monkeypatch) -> None:
    """reset_for_next_case 抛异常 → 整批不中断；step prompt 兜底 navigate。

    回归保护：未来给 reset 加新动作时如果不小心让异常逃出，整批用例不会被
    一次小故障打挂——下条用例 step 1 仍会按 prompt 引导自己 navigate。
    """
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action=f"action {i}", expected_result="ok"),
        ])
        for i in range(2)
    ]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    class _BundleResetThrows(_FakeBundle):
        async def reset_for_next_case(self):
            raise RuntimeError("simulated reset failure")

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=_BundleResetThrows()),
        step_runner_factory=lambda env, llm, budget, eid: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )

    out = await engine.run(inputs)
    # 第二条用例正常被执行，整批 completed
    assert out.status == "completed"
    assert out.passed == 2
    # case_reset 事件依然发出，但带 errors（前端可据此告警）
    reset_events = [
        payload for ev, payload in hub.streams[inputs.execution_id].events
        if ev == "case_reset"
    ]
    assert len(reset_events) == 1
    assert reset_events[0]["errors"], "reset 失败时事件里必须带 errors"


# ─── 2) 单 case data_failure → 后续 case 继续 ─────────────────────────


@pytest.mark.asyncio
async def test_data_failure_does_not_abort_batch(monkeypatch) -> None:
    resolver, state = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action=f"action {i}", expected_result="ok"),
        ])
        for i in range(2)
    ]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    runner_calls = {"count": 0}

    class _RunnerWithFailure:
        async def run_one(self, **kwargs):
            runner_calls["count"] += 1
            # 第一条用例的步骤完成时手动注入 data_failure，让 finalize_case 给 data_failure
            if runner_calls["count"] == 1:
                state["failures"].append({"key": "username", "reason": "账号不存在"})
            return _step_run_ok()

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda env, llm, budget, eid: _RunnerWithFailure(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await engine.run(inputs)

    # 整批应仍然 completed（不被单 case 阻断）
    assert out.status == "completed"
    assert out.failed == 1  # 第一条 case 因 data_failure 计入 failed 桶
    assert out.passed == 1
    assert len(persistence.cases_flushed) == 2

    confidences = {c["status"]: c["data_confidence"] for c in persistence.cases_flushed}
    assert "error" in {c["status"] for c in persistence.cases_flushed}
    assert "data_failure" in confidences.values()


# ─── 3) preflight 缺料 + strict_data_mode → 拒绝执行 ────────────────


@pytest.mark.asyncio
async def test_strict_data_mode_blocks_when_missing(monkeypatch) -> None:
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    import app.modules.ui_automation.execution_engine as engine_mod

    # 让 preflight 报 1 条缺料告警
    monkeypatch.setattr(
        engine_mod,
        "preflight_data_check",
        AsyncMock(return_value=[
            MissingDataAlert(key="username", detected_in_steps=[]),
        ]),
    )

    tcs = [_Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="login {{username}}", expected_result="ok"),
    ])]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
        strict_data_mode=True,
    )
    out = await engine.run(inputs)

    assert out.status == "failed"
    assert out.passed == 0
    assert "缺料" in (out.error_message or "")
    # bundle 不应启动（strict 模式直接 return）
    assert bundle.closed is False
    # case 一条都不应建
    assert persistence.cases_created == []

    event_names = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert "missing_data_warning" in event_names


# ─── 4) Token 预算耗尽 → aborted_budget ────────────────────────────


@pytest.mark.asyncio
async def test_budget_exceeded_aborts_remaining_cases(monkeypatch) -> None:
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action="x", expected_result="ok"),
        ])
        for i in range(3)
    ]
    _patch_db_loaders(
        monkeypatch,
        env=_make_env_stub(token_budget=100),
        testcases=tcs,
    )

    runner_invocations = {"n": 0}

    def runner_factory(env, llm, budget, eid):
        async def run_one(**kw):
            runner_invocations["n"] += 1
            # 第一步直接吃光预算 → 通过 StepRunResult.error_kind="budget_exceeded"
            budget.add(200)
            return StepRunResult(
                success=False,
                iterations=1,
                tokens_used=200,
                reasoning="",
                final_message="",
                tool_calls=[],
                last_snapshot_text=None,
                last_clipped=None,
                error="预算超限",
                error_kind="budget_exceeded",
            )
        return SimpleNamespace(run_one=run_one)

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=runner_factory,
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    engine = ExecutionEngine(deps=deps)
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await engine.run(inputs)

    assert out.status == "aborted_budget"
    # 第一条 case 走完了 1 步（budget_exceeded），剩余 2 条 skipped
    assert runner_invocations["n"] == 1
    assert out.skipped == 2
    event_names = [e for e, _ in hub.streams[inputs.execution_id].events]
    assert "budget_exceeded" in event_names


# ─── 5) Assertion failed → case=failed，整批继续 ─────────────────────


@pytest.mark.asyncio
async def test_assertion_failed_marks_case_failed(monkeypatch) -> None:
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)
    tcs = [_Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="x", expected_result="not-found"),
    ])]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(verdicts=[
            AssertionVerdict(passed=False, reason="未命中", method="text_search"),
        ]),
        persistence=persistence,
        stream_hub=hub,
    )
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await ExecutionEngine(deps=deps).run(inputs)
    assert out.status == "completed"  # 整批 OK，单 case 失败不阻断
    assert out.failed == 1
    assert persistence.cases_flushed[0]["status"] == "failed"


# ─── 6) 用户主动停止 → execution.status=stopped ───────────────────


@pytest.mark.asyncio
async def test_user_stop_stops_remaining_cases(monkeypatch) -> None:
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)
    tcs = [
        _Testcase(id=uuid.uuid4(), title=f"tc{i}", steps=[
            _Step(step_number=1, action="x", expected_result="ok"),
        ])
        for i in range(3)
    ]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    runner = _FakeStepRunner()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: runner,
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )

    exec_id = uuid.uuid4()
    persistence.stopped_for = {exec_id}  # 第一次 case 循环检查就触发 stop

    inputs = ExecutionInputs(
        execution_id=exec_id,
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await ExecutionEngine(deps=deps).run(inputs)
    assert out.status == "stopped"
    assert out.skipped == 3  # 所有用例都被 skip
    assert persistence.cases_created == []
    assert "execution_stopped" in {e for e, _ in hub.streams[exec_id].events}


# ─── 7) Engine 入口异常 → execution.status=failed + flush_execution 仍调 ─


@pytest.mark.asyncio
async def test_engine_inner_exception_marks_failed_and_flushes(monkeypatch) -> None:
    import app.modules.ui_automation.execution_engine as engine_mod

    async def boom(db, environment_id):  # noqa: ARG001
        raise RuntimeError("环境加载炸了")

    monkeypatch.setattr(engine_mod, "_load_environment", boom)
    monkeypatch.setattr(engine_mod, "_load_llm_config", AsyncMock(return_value=None))
    monkeypatch.setattr(engine_mod, "_load_testcases", AsyncMock(return_value=[]))

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(),
        step_runner_factory=lambda *a, **kw: _FakeStepRunner(),
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    out = await ExecutionEngine(deps=deps).run(inputs)
    assert out.status == "failed"
    assert "环境加载炸了" in (out.error_message or "")
    assert persistence.executions_flushed
    assert persistence.executions_flushed[-1]["status"] == "failed"
    assert hub.streams[inputs.execution_id].done is True


# ─── 8) Step 间页面状态接续（修复 #3c95cf69 步骤不连贯） ──────────


@pytest.mark.asyncio
async def test_step_runner_receives_previous_url_and_snapshot(monkeypatch) -> None:
    """**关键回归（修复 #3c95cf69）**：ExecutionEngine 必须把上一步收尾的
    ``current_url`` / ``page_title`` / ``a11y snapshot`` 传给下一步的 prompt，
    否则 AI 在每个 step 开头都看到 ``current_url=(未知)`` + 空 snapshot，会
    保险性 ``browser_navigate`` 重置页面 —— 把前一步在表单里输入的内容冲掉，
    导致 step 间不连贯。"""
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    # 一条用例 2 步：step 1 输入 9999，step 2 点查询
    tcs = [_Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="在创作者ID输入9999", expected_result="文本框显示9999"),
        _Step(step_number=2, action="点击查询", expected_result="列表为空"),
    ])]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    # step 1 返回的 snapshot 模拟 playwright-mcp 实际格式（带 Page URL/Title）
    step1_snapshot = (
        "### Page\n"
        "- Page URL: https://app.example.com/list?q=9999\n"
        "- Page Title: 创作者列表\n"
        "### Snapshot\n"
        "- main\n"
        "  - textbox '创作者ID' [ref=e60]: '9999'\n"
        "  - button '查询' [ref=e67]\n"
    )
    runner = _FakeStepRunner(results=[
        _step_run_ok(snapshot=step1_snapshot),
        _step_run_ok(),  # step 2，不关心返回，只看入参
    ])

    persistence = _FakePersistence()
    hub = _FakeStreamHub()
    bundle = _FakeBundle()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: runner,
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=persistence,
        stream_hub=hub,
    )
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    await ExecutionEngine(deps=deps).run(inputs)

    assert len(runner.calls) == 2

    step1_kw = runner.calls[0]
    step2_kw = runner.calls[1]

    # step 1 是用例首步：current_url 还是默认 (未知)，snapshot 也无
    assert step1_kw.get("current_url", "(未知)") == "(未知)"
    assert step1_kw.get("initial_snapshot_text") in (None, "")

    # step 2 必须接续 step 1 的状态：URL / title 来自 step 1 的 snapshot 文本
    assert step2_kw["current_url"] == "https://app.example.com/list?q=9999", (
        "step 2 必须从上一步 snapshot 抽出 Page URL 注入 prompt（避免 AI 保险 navigate）"
    )
    assert step2_kw["page_title"] == "创作者列表", (
        "step 2 的 page_title 必须来自上一步 snapshot 的 Page Title"
    )
    # step 2 的 initial_snapshot_text 应为 step 1 的最终 snapshot
    assert step2_kw["initial_snapshot_text"] == step1_snapshot
    # prev_snapshot 也是同一份（裁剪算法对照用）
    assert step2_kw["prev_snapshot"] == step1_snapshot


@pytest.mark.asyncio
async def test_step_runner_falls_back_to_bundle_url_when_snapshot_lacks_it(
    monkeypatch,
) -> None:
    """如果上一步 snapshot 文本里没有 Page URL（playwright-mcp 偶尔不带），
    走 bundle.get_current_url_via_mcp() 兜底。"""
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [_Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="x", expected_result="ok"),
        _Step(step_number=2, action="y", expected_result="ok"),
    ])]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    # snapshot 不带 Page URL —— 强制走 bundle fallback
    bare_snapshot = "- main\n  - heading 'Welcome'\n"
    runner = _FakeStepRunner(results=[
        _step_run_ok(snapshot=bare_snapshot),
        _step_run_ok(),
    ])

    class _BundleWithMCPUrl(_FakeBundle):
        def __init__(self):
            super().__init__()
            self.url_calls = 0

        async def get_current_url_via_mcp(self):
            self.url_calls += 1
            return "https://app.example.com/from-mcp"

    bundle = _BundleWithMCPUrl()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: runner,
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=_FakePersistence(),
        stream_hub=_FakeStreamHub(),
    )
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    await ExecutionEngine(deps=deps).run(inputs)

    # step 2 收到的 URL 必须来自 bundle.get_current_url_via_mcp
    assert runner.calls[1]["current_url"] == "https://app.example.com/from-mcp"
    assert bundle.url_calls >= 1


@pytest.mark.asyncio
async def test_step_runner_keeps_last_url_when_refresh_fails(monkeypatch) -> None:
    """URL 刷新失败时（snapshot 里没 URL + bundle MCP 调用抛错）应保留上一步
    已知的 URL，而不是退化为 ``(未知)`` —— 让 AI 仍能避免冗余 navigate。"""
    resolver, _ = _make_resolver_stub()
    _patch_resolver(monkeypatch, lambda: resolver)

    tcs = [_Testcase(id=uuid.uuid4(), title="tc", steps=[
        _Step(step_number=1, action="x", expected_result="ok"),
        _Step(step_number=2, action="y", expected_result="ok"),
        _Step(step_number=3, action="z", expected_result="ok"),
    ])]
    _patch_db_loaders(monkeypatch, testcases=tcs)

    snap_with_url = (
        "### Page\n- Page URL: https://app.example.com/x\n- Page Title: X\n"
        "### Snapshot\n- main\n"
    )
    snap_no_url = "- main\n  - text 'oops 没 URL'\n"
    runner = _FakeStepRunner(results=[
        _step_run_ok(snapshot=snap_with_url),
        _step_run_ok(snapshot=snap_no_url),
        _step_run_ok(),
    ])

    class _BundleAlwaysFails(_FakeBundle):
        async def get_current_url_via_mcp(self):
            raise RuntimeError("MCP not available")

    bundle = _BundleAlwaysFails()
    deps = EngineDeps(
        db_session_factory=lambda: _FakeSessionContext(),
        open_browser_bundle=AsyncMock(return_value=bundle),
        step_runner_factory=lambda *a, **kw: runner,
        assertion_judge_factory=lambda: _FakeJudge(),
        persistence=_FakePersistence(),
        stream_hub=_FakeStreamHub(),
    )
    inputs = ExecutionInputs(
        execution_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        testcase_ids=[tc.id for tc in tcs],
        llm_config_id=None,
        triggered_by=uuid.uuid4(),
    )
    await ExecutionEngine(deps=deps).run(inputs)

    # step 2 拿到 step 1 的 URL（从 snapshot 抽）
    assert runner.calls[1]["current_url"] == "https://app.example.com/x"
    # step 3：snapshot 里没 URL + bundle 调用抛错 → 应保留 step 2 时确定的旧值
    assert runner.calls[2]["current_url"] == "https://app.example.com/x", (
        "URL 刷新失败时不能退化为 '(未知)'，保留上一步已知 URL"
    )


# 让 lint 不报 unused
_ = asynccontextmanager
