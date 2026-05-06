"""Task 9.7 — replayer.py 单测。

不连真 DB —— monkeypatch ``_load_execution`` 返回手搓的 fake row。
覆盖：
1. 事件序列与 Engine 实时跑严格同构（execution_started → cases →
   execution_complete → done）
2. 每个事件 payload 都带 ``replay=true`` 标记
3. step_complete 在持久化了 ``screenshot_path`` 时带 ``screenshot_url``
4. 用例按 ``sort_order`` 升序，步骤按 ``step_number`` 升序
5. ``inter_step_delay_seconds`` > 0 时确实 sleep（粗略时间断言）
6. NotFoundException 在 execution 不存在时上抛
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import pytest

from app.core.exceptions import NotFoundException
from app.modules.ui_automation import replayer as replayer_mod
from app.modules.ui_automation.replayer import replay

# ─── fake row builder ────────────────────────────────────────────────


@dataclass
class _FakeStep:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    step_number: int = 1
    description: str = "点击登录按钮"
    status: str = "passed"
    tool_calls: list = field(default_factory=list)
    tokens_used: int = 100
    duration_ms: int = 250
    error_message: str | None = None
    snapshot_after: str | None = "<a>1</a>"
    ai_reasoning: str | None = "我点了登录"
    assertion_passed: bool | None = True
    assertion_reason: str | None = "URL 包含 /home"
    assertion_evidence: str | None = "url=/home"
    screenshot_path: str | None = None


@dataclass
class _FakeCase:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    testcase_id: uuid.UUID | None = field(default_factory=uuid.uuid4)
    sort_order: int = 0
    status: str = "passed"
    data_confidence: str = "reliable"
    duration_ms: int = 1234
    tokens_used: int = 500
    error_message: str | None = None
    synthesized_data: list = field(default_factory=list)
    data_failures: list = field(default_factory=list)
    test_data_used: list = field(default_factory=list)
    created_at: object = field(default_factory=lambda: 0)
    step_results: list = field(default_factory=list)


@dataclass
class _FakeExecution:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "completed"
    mode: str = "normal"
    total_cases: int = 1
    passed_cases: int = 1
    failed_cases: int = 0
    skipped_cases: int = 0
    duration_ms: int | None = 5000
    tokens_total: int = 1500
    error_message: str | None = None
    test_data_snapshot: dict | None = None
    case_results: list = field(default_factory=list)


def _parse_chunks(chunks: list[str]) -> list[dict]:
    out = []
    for c in chunks:
        c = c.strip()
        assert c.startswith("data:"), f"bad SSE frame: {c!r}"
        out.append(json.loads(c[5:].strip()))
    return out


# ─── tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replay_event_sequence_matches_engine(monkeypatch) -> None:
    step1 = _FakeStep(step_number=1)
    step2 = _FakeStep(step_number=2, status="failed", assertion_passed=False)
    case = _FakeCase(step_results=[step2, step1])  # 故意倒序，验证 replayer 排序
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)

    types = [e["type"] for e in events]
    assert types == [
        "execution_started",
        "case_started",
        "step_started",
        "step_complete",
        "step_started",
        "step_complete",
        "case_complete",
        "execution_complete",
        "done",
    ]

    # 步骤按 step_number 升序（不是 list 给的顺序）
    step_starts = [e for e in events if e["type"] == "step_started"]
    assert [e["step_number"] for e in step_starts] == [1, 2]


@pytest.mark.asyncio
async def test_replay_marks_all_events_with_replay_flag(monkeypatch) -> None:
    case = _FakeCase(step_results=[_FakeStep()])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    # done 是收尾标记，也带 replay 字段方便前端区分
    for evt in events:
        assert evt.get("replay") is True, f"missing replay flag: {evt}"


@pytest.mark.asyncio
async def test_replay_includes_screenshot_url_when_persisted(
    monkeypatch, tmp_path,
) -> None:
    """**关键回归**：``screenshot_url`` 必须走 nginx 静态路径
    （``/uploads/ui_artifacts/...``），而非鉴权 API 路径——HTML ``<img src>``
    不会自动带 Authorization header，鉴权 API 必 401，回放页会显示"截图加载
    失败"。同 video 故障的根因（2026-05 修复）。"""
    from app.config import settings as cfg_settings

    monkeypatch.setattr(cfg_settings, "UI_ARTIFACTS_DIR", str(tmp_path), raising=False)
    abs_screen = str(tmp_path / "exec-id" / "steps" / "abc.png")
    step = _FakeStep(screenshot_path=abs_screen)
    case = _FakeCase(step_results=[step])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    step_complete = next(e for e in events if e["type"] == "step_complete")
    assert step_complete["screenshot_url"] == "/uploads/ui_artifacts/exec-id/steps/abc.png"
    # 不能再回退到 ``/api/ui-executions/steps/.../screenshot`` 鉴权路径
    assert "/api/" not in step_complete["screenshot_url"]


@pytest.mark.asyncio
async def test_replay_omits_screenshot_url_when_not_persisted(monkeypatch) -> None:
    step = _FakeStep(screenshot_path=None)
    case = _FakeCase(step_results=[step])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    step_complete = next(e for e in events if e["type"] == "step_complete")
    assert "screenshot_url" not in step_complete


@pytest.mark.asyncio
async def test_replay_emits_data_snapshot_when_present(monkeypatch) -> None:
    snapshot = {"loaded_sets": ["users.csv"], "manual_overrides": {"foo": "bar"}}
    execution = _FakeExecution(test_data_snapshot=snapshot, case_results=[])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    snap_evt = next(e for e in events if e["type"] == "data_snapshot")
    assert snap_evt["snapshot"] == snapshot


@pytest.mark.asyncio
async def test_replay_orders_cases_by_sort_order(monkeypatch) -> None:
    case_b = _FakeCase(sort_order=1, step_results=[_FakeStep()])
    case_a = _FakeCase(sort_order=0, step_results=[_FakeStep()])
    execution = _FakeExecution(total_cases=2, case_results=[case_b, case_a])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    case_starts = [e for e in events if e["type"] == "case_started"]
    assert [e["sort_order"] for e in case_starts] == [0, 1]


@pytest.mark.asyncio
async def test_replay_inter_step_delay_actually_sleeps(monkeypatch) -> None:
    """delay > 0 时回放总时长应明显大于 0。粗略时间断言够用。"""
    case = _FakeCase(step_results=[_FakeStep(), _FakeStep(step_number=2)])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    t0 = time.monotonic()
    chunks = [c async for c in replay(execution.id, inter_step_delay_seconds=0.05)]
    elapsed = time.monotonic() - t0
    # 2 步 × 0.05s = 0.1s 起步；不要写死上限避免 CI 抖动
    assert elapsed >= 0.08, f"expected ≥0.08s, got {elapsed:.3f}s"
    assert chunks  # 别只看时间不看结果


@pytest.mark.asyncio
async def test_replay_raises_not_found(monkeypatch) -> None:
    async def fake_load(_):
        raise NotFoundException("执行记录不存在")

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    with pytest.raises(NotFoundException):
        async for _ in replay(uuid.uuid4()):
            pass


@pytest.mark.asyncio
async def test_replay_empty_execution_still_yields_envelope(monkeypatch) -> None:
    """没用例的执行也应发 execution_started + execution_complete + done，
    让前端能展示"该执行没用例"。"""
    execution = _FakeExecution(case_results=[])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    assert [e["type"] for e in events] == [
        "execution_started", "execution_complete", "done",
    ]


# ─── 用例元数据注入：让前端展示 ``TC-0061 标题`` 而非 ``用例 24835e6d`` ──


@pytest.mark.asyncio
async def test_case_started_carries_testcase_no_and_module_from_meta(
    monkeypatch,
) -> None:
    """**关键回归**：``case_started`` SSE 事件必须带 ``testcase_no`` /
    ``title`` / ``testcase_module_name``——前端用以渲染 ``TC-0061 创作者ID
    查询`` 形式的可读标识；缺这些字段时前端只能显示 case_result_id 前 8 位
    hash（实际故障：用户看到 "用例 24835e6d"）。"""
    case = _FakeCase(step_results=[_FakeStep()])
    execution = _FakeExecution(case_results=[case])
    testcase_meta = {
        case.testcase_id: {
            "case_no": 61,
            "title": "创作者ID查询",
            "module_id": uuid.uuid4(),
            "module_name": "创作者中台",
        },
    }

    async def fake_load(_):
        return execution, testcase_meta

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    case_started = next(e for e in events if e["type"] == "case_started")
    assert case_started["testcase_no"] == 61
    assert case_started["title"] == "创作者ID查询"
    assert case_started["testcase_module_name"] == "创作者中台"


@pytest.mark.asyncio
async def test_case_started_handles_deleted_testcase_gracefully(monkeypatch) -> None:
    """用例已被删除（``testcase_meta`` 里没有对应条目）时回放不能崩；事件
    里相关字段降级为 None / 空串，前端展示 ``TC-?`` / ``用例 <hash>``。"""
    case = _FakeCase(step_results=[_FakeStep()])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}  # meta 全空

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    case_started = next(e for e in events if e["type"] == "case_started")
    assert case_started["testcase_no"] is None
    assert case_started["title"] == ""
    assert case_started["testcase_module_name"] is None


@pytest.mark.asyncio
async def test_case_started_handles_orphan_case_without_testcase_id(
    monkeypatch,
) -> None:
    """``ui_case_results.testcase_id`` 为 None（极旧数据 / 异常情况）时 meta
    查不到，事件依然要能发出，相关字段降级 None。"""
    case = _FakeCase(testcase_id=None, step_results=[_FakeStep()])
    execution = _FakeExecution(case_results=[case])

    async def fake_load(_):
        return execution, {}

    monkeypatch.setattr(replayer_mod, "_load_execution", fake_load)

    chunks = [c async for c in replay(execution.id)]
    events = _parse_chunks(chunks)
    case_started = next(e for e in events if e["type"] == "case_started")
    assert case_started["testcase_id"] is None
    assert case_started["testcase_no"] is None
