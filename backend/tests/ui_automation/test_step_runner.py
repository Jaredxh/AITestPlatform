"""Task 9.4 — StepRunner 单测。

策略：
- 完全 mock LLM（``chat_round_fn``）—— 不依赖任何真实 provider
- mock TOOL_REGISTRY（通过 ``tool_runner`` 注入）—— 不依赖 MCP 子进程
- 重点验证：tool-calling 循环、SecurityGuard 拦截、Budget 超限、data_manifest
  注入、secret 脱敏、platform_* 工具与 MCP 工具的合并
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.modules.ui_automation.security import (
    BudgetExceededError,
    SecurityError,
    TokenBudget,
)
from app.modules.ui_automation.step_runner import (
    ChatRound,
    StepRunner,
    ToolCallEmit,
)

# ─── helpers ─────────────────────────────────────────────────────────


def make_env(*, allowed_hosts: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        base_url="https://app.example.com",
        allowed_hosts=allowed_hosts or ["app.example.com"],
        token_budget=50_000,
        enable_browser_evaluate=False,
    )


def make_llm() -> SimpleNamespace:
    return SimpleNamespace(
        provider="openai",
        model="gpt-4o-mini",
        temperature=0.0,
        max_tokens=2048,
        base_url=None,
        api_key="sk-test",
    )


def chat_rounds(*rounds: ChatRound):
    """构造一个有限响应的 chat_round_fn；用尽后再调直接抛错。"""
    queue = list(rounds)
    captured: list[dict[str, Any]] = []

    async def fn(*, messages, tools, tool_choice):  # noqa: ANN001
        captured.append({"messages": list(messages), "tools": list(tools or []), "tool_choice": tool_choice})
        if not queue:
            raise AssertionError("chat_round_fn called more times than expected")
        return queue.pop(0)

    return fn, captured


@dataclass
class FakeTool:
    """注入给 StepRunner 的"伪 TOOL_REGISTRY"。"""

    handlers: dict[str, Any]

    async def __call__(self, name: str, args_json: str) -> str:
        if name not in self.handlers:
            return json.dumps({"error": f"unknown tool {name}"})
        result = self.handlers[name](json.loads(args_json or "{}"))
        return json.dumps(result, ensure_ascii=False)


# ─── 1) 单步骤 + 1 次 tool_call → 完成 ─────────────────────────────


@pytest.mark.asyncio
async def test_run_one_single_tool_call_then_finish() -> None:
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="call_1",
                    name=f"{exec_id}__browser_click",
                    arguments_json='{"ref": "e15"}',
                ),
            ],
            finish_reason="tool_calls",
            usage_total=120,
        ),
        ChatRound(
            content="已点击登录按钮，页面跳转至首页。",
            finish_reason="stop",
            usage_total=80,
        ),
    )

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({
            f"{exec_id}__browser_click": lambda a: {"ok": True, "snapshot": "- main\n  - heading 'Welcome'"},
        }),
    )

    out = await runner.run_one(
        step_description="点击 ref=e15 的登录按钮",
        expected="跳转到首页",
    )
    assert out.success is True
    assert out.error is None
    assert out.iterations == 2
    assert out.tokens_used == 200
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].raw_name == "browser_click"
    assert out.tool_calls[0].blocked is False
    assert out.last_snapshot_text is not None and "Welcome" in out.last_snapshot_text
    assert "已点击登录按钮" in out.final_message

    # 第二轮 tool_choice 不强制（因为不是 max_iterations 边界）
    assert captured[1]["tool_choice"] is None


# ─── 2) 安全拦截：跨域 navigate ──────────────────────────────────────


@pytest.mark.asyncio
async def test_security_guard_blocks_cross_domain_navigate() -> None:
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="call_1",
                    name=f"{exec_id}__browser_navigate",
                    arguments_json='{"url": "https://attacker.example/steal"}',
                ),
            ],
            finish_reason="tool_calls",
            usage_total=100,
        ),
        ChatRound(
            content="放弃跨域跳转。",
            finish_reason="stop",
            usage_total=60,
        ),
    )

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(allowed_hosts=["app.example.com"]),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({}),  # 不应被调到——guard 先拦了
    )

    out = await runner.run_one(step_description="跳转到 https://attacker.example/steal")
    assert out.success is True  # 整个 step 还是收尾了，只是某个 tool 被拦
    assert len(out.tool_calls) == 1
    rec = out.tool_calls[0]
    assert rec.blocked is True
    assert "不在 environment.allowed_hosts" in (rec.error or "")
    assert rec.result.get("blocked_by_security") is True


# ─── 3) Budget 耗尽 → 终止 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_exhausted_terminates_step() -> None:
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="call_1",
                    name=f"{exec_id}__browser_click",
                    arguments_json="{}",
                ),
            ],
            finish_reason="tool_calls",
            usage_total=10_000,  # 单轮就直接打爆
        ),
    )

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=5_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({f"{exec_id}__browser_click": lambda _: {"ok": True}}),
    )

    out = await runner.run_one(step_description="点登录")
    assert out.success is False
    assert out.error_kind == "budget_exceeded"
    assert out.tokens_used >= 5_000


@pytest.mark.asyncio
async def test_budget_exhausted_before_first_round() -> None:
    exec_id = uuid.uuid4()
    budget = TokenBudget(limit=1_000)
    budget.add(5_000)  # 入场就超

    fn, _ = chat_rounds()  # 不应该被调到

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=budget,
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    out = await runner.run_one(step_description="任意步骤")
    assert out.error_kind == "budget_exceeded"
    assert out.success is False


# ─── 4) data_manifest 注入 system prompt ─────────────────────────────


@pytest.mark.asyncio
async def test_data_manifest_injected_into_system_prompt() -> None:
    fn, captured = chat_rounds(
        ChatRound(content="OK", finish_reason="stop", usage_total=50),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    manifest = "| key | value | type |\n|---|---|---|\n| username | admin | text |"
    out = await runner.run_one(
        step_description="输入用户名 admin",
        data_manifest=manifest,
    )
    assert out.success is True

    sys_msg = captured[0]["messages"][0]
    assert sys_msg["role"] == "system"
    assert "可用测试物料" in sys_msg["content"]
    assert "username" in sys_msg["content"]
    assert "admin" in sys_msg["content"]


# ─── 5) data_resolver 提供时合并 platform_* tools ───────────────────


@pytest.mark.asyncio
async def test_platform_tools_merged_when_resolver_present() -> None:
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(content="done", finish_reason="stop", usage_total=10),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    resolver = MagicMock()  # 只用作"非 None"标记
    mcp_specs = [
        {
            "type": "function",
            "function": {"name": f"{exec_id}__browser_click", "description": "x", "parameters": {}},
        },
    ]
    await runner.run_one(
        step_description="x",
        mcp_tool_specs=mcp_specs,
        data_resolver=resolver,
    )
    tool_names = {t["function"]["name"] for t in captured[0]["tools"]}
    assert f"{exec_id}__browser_click" in tool_names
    assert f"{exec_id}__platform_get_test_data" in tool_names
    assert f"{exec_id}__platform_get_secret" in tool_names


# ─── 6) Secret 工具：plaintext 不出现在写回的 messages 里 ─────────────


@pytest.mark.asyncio
async def test_secret_tool_result_redacted_in_messages_history() -> None:
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="call_1",
                    name=f"{exec_id}__platform_get_secret",
                    arguments_json='{"key": "password"}',
                ),
            ],
            finish_reason="tool_calls",
            usage_total=80,
        ),
        ChatRound(content="完成", finish_reason="stop", usage_total=20),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({
            f"{exec_id}__platform_get_secret": lambda a: {
                "key": "password",
                "value": "Sup3rS3cretP@ss",
                "_test_data_secret_used": True,
            },
        }),
    )
    out = await runner.run_one(step_description="读取密码后填入密码框")
    assert out.success is True
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0].result.get("value") == "Sup3rS3cretP@ss"

    # 第二轮 messages 里不应含 plaintext
    second_round_messages = captured[1]["messages"]
    serialized = json.dumps(second_round_messages, ensure_ascii=False)
    assert "Sup3rS3cretP@ss" not in serialized


# ─── 7) prev_snapshot 注入 + diff 体现在 system prompt ──────────────


@pytest.mark.asyncio
async def test_initial_snapshot_clipped_into_system_prompt() -> None:
    fn, captured = chat_rounds(
        ChatRound(content="ok", finish_reason="stop", usage_total=10),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    big = "- main\n" + "\n".join(f"  - link 'item-{i}' [ref=e{i}]" for i in range(20))
    await runner.run_one(
        step_description="选第一项",
        initial_snapshot_text=big,
        current_url="https://app.example.com/list",
        page_title="Items",
    )
    sys_msg = captured[0]["messages"][0]
    assert "Accessibility 快照" in sys_msg["content"]
    assert "https://app.example.com/list" in sys_msg["content"]
    assert "item-0" in sys_msg["content"]


# ─── 8) Tool runner 抛错 → 记录到 ToolCallRecord，不挂掉整个 step ────


@pytest.mark.asyncio
async def test_tool_runner_exception_captured_not_raised() -> None:
    exec_id = uuid.uuid4()

    async def boom(name: str, args: str) -> str:
        raise RuntimeError(f"network down for {name}")

    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(id="c1", name=f"{exec_id}__browser_click", arguments_json="{}"),
            ],
            finish_reason="tool_calls",
            usage_total=50,
        ),
        ChatRound(content="收到错误，放弃。", finish_reason="stop", usage_total=20),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=boom,
    )
    out = await runner.run_one(step_description="点击")
    assert out.success is True
    assert len(out.tool_calls) == 1
    rec = out.tool_calls[0]
    assert rec.blocked is False
    assert "network down" in (rec.error or "")


# ─── 9) chat_round_fn 抛 SecurityError → 终止并标 security_blocked ───


@pytest.mark.asyncio
async def test_chat_round_security_error_propagates() -> None:
    async def fn(*, messages, tools, tool_choice):  # noqa: ANN001
        raise SecurityError("某个 tool 名非法")

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    out = await runner.run_one(step_description="x")
    assert out.success is False
    assert out.error_kind == "security_blocked"


@pytest.mark.asyncio
async def test_chat_round_budget_error_propagates() -> None:
    async def fn(*, messages, tools, tool_choice):  # noqa: ANN001
        raise BudgetExceededError("over")

    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    out = await runner.run_one(step_description="x")
    assert out.success is False
    assert out.error_kind == "budget_exceeded"


# ─── 10) max_iterations 末轮强制 tool_choice="none" ───────────────────


@pytest.mark.asyncio
async def test_last_iteration_forces_tool_choice_none() -> None:
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(
            tool_calls=[ToolCallEmit(id="c1", name=f"{exec_id}__browser_click", arguments_json="{}")],
            finish_reason="tool_calls",
            usage_total=30,
        ),
        ChatRound(
            tool_calls=[ToolCallEmit(id="c2", name=f"{exec_id}__browser_click", arguments_json="{}")],
            finish_reason="tool_calls",
            usage_total=30,
        ),
        ChatRound(content="收尾。", finish_reason="stop", usage_total=10),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({f"{exec_id}__browser_click": lambda _: {"ok": True}}),
        max_iterations=3,
    )
    out = await runner.run_one(step_description="重复点击直到收尾")
    assert out.success is True
    assert out.iterations == 3
    # 第 3（末）轮 tool_choice 必须是 "none"
    assert captured[2]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_reasoning_content_is_passed_back_to_next_round() -> None:
    """火山方舟 / 智谱 GLM 等 thinking 模型契约：上一轮返回的 reasoning_content
    必须随下一轮 assistant message 回传，否则 400 ``The reasoning_content in
    the thinking mode must be passed back to the API``。

    这是真实碰到的 bug —— 之前 step_runner 组装 assistant_msg 时只塞 content +
    tool_calls 就丢了 reasoning_content，第二轮就被网关打回。这个回归测试守住
    "reasoning 必须回传"这个不变量。
    """
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(
            content="我先点击搜索按钮。",
            reasoning="用户想搜索北京天气；先定位输入框 ref，输入查询，再点搜索。",
            tool_calls=[
                ToolCallEmit(
                    id="c1",
                    name=f"{exec_id}__browser_click",
                    arguments_json="{}",
                ),
            ],
            finish_reason="tool_calls",
            usage_total=50,
        ),
        ChatRound(content="点击完成。", finish_reason="stop", usage_total=20),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({f"{exec_id}__browser_click": lambda _: {"ok": True}}),
        max_iterations=3,
    )

    out = await runner.run_one(step_description="搜索北京天气")
    assert out.success is True

    # 第二轮请求里的 messages 应该包含一条 assistant message，且带回了
    # reasoning_content（与 content / tool_calls 同级），原值不能被截断或丢失。
    second_round_messages = captured[1]["messages"]
    assistant_msgs = [m for m in second_round_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1, (
        f"应有且仅有 1 条 assistant message 喂回去，实际：{assistant_msgs}"
    )
    am = assistant_msgs[0]
    assert "reasoning_content" in am, (
        "assistant message 必须带 reasoning_content（火山方舟 thinking 契约）"
    )
    assert am["reasoning_content"] == (
        "用户想搜索北京天气；先定位输入框 ref，输入查询，再点搜索。"
    )
    # 同时也得保留 tool_calls，否则下一轮 tool 结果无法 reference
    assert am["tool_calls"] and am["tool_calls"][0]["id"] == "c1"


@pytest.mark.asyncio
async def test_reasoning_content_not_added_when_empty() -> None:
    """没有 reasoning 时不要往 assistant message 里塞空串，避免污染上下文 /
    被某些严格 provider 当成无效字段拒绝。"""
    exec_id = uuid.uuid4()
    fn, captured = chat_rounds(
        ChatRound(
            content="",
            reasoning="",  # 标准 GPT-4 等无 thinking 模式 → reasoning 永远为空
            tool_calls=[
                ToolCallEmit(
                    id="c1",
                    name=f"{exec_id}__browser_click",
                    arguments_json="{}",
                ),
            ],
            finish_reason="tool_calls",
            usage_total=30,
        ),
        ChatRound(content="完成。", finish_reason="stop", usage_total=10),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({f"{exec_id}__browser_click": lambda _: {"ok": True}}),
        max_iterations=3,
    )

    await runner.run_one(step_description="点击")

    second_round_messages = captured[1]["messages"]
    assistant_msgs = [m for m in second_round_messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert "reasoning_content" not in assistant_msgs[0]


# ─── auto-finalize browser_snapshot（修复 #f6513ebb）─────────────────


@pytest.mark.asyncio
async def test_auto_finalize_after_mutating_tool_refreshes_snapshot() -> None:
    """**关键回归**：mutating 工具（browser_type / click 等）调用之后，
    StepRunner 必须自动再调一次 browser_snapshot，确保 ``last_snapshot_text``
    反映"操作之后"的页面状态——否则后续 AssertionJudge 看到的还是操作之前
    的快照，会出现"在 X 文本框输入 9999"通过了但断言说"快照看不到 9999"
    这种假阳性失败（实际故障 #f6513ebb / #aecbde45）。"""
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="c1",
                    name=f"{exec_id}__browser_type",
                    arguments_json='{"ref": "e60", "text": "9999"}',
                ),
            ],
            finish_reason="tool_calls",
            usage_total=10,
        ),
        ChatRound(content="已输入 9999。", finish_reason="stop", usage_total=5),
    )

    # 关键：browser_type 的 result 里**没有 snapshot 字段**（playwright-mcp 0.x
    # 的真实行为）；browser_snapshot 才会返回 a11y 树。
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({
            f"{exec_id}__browser_type": lambda _: {
                "content": "### Ran Playwright code\n```js\nawait page.fill('9999');\n```",
            },
            f"{exec_id}__browser_snapshot": lambda _: {
                "snapshot": "- main\n  - textbox '创作者ID' [ref=e60]: '9999'\n",
            },
        }),
    )

    out = await runner.run_one(
        step_description="在创作者ID文本框中输入9999",
        expected="文本框显示9999",
    )
    assert out.success is True
    raw_names = [tc.raw_name for tc in out.tool_calls]
    assert raw_names == ["browser_type", "browser_snapshot"], (
        f"auto-finalize 必须在最后追加一次 browser_snapshot，实际 tool_calls={raw_names}"
    )
    # 最后那次 snapshot 是平台兜底刷的，应有标记位
    last_call = out.tool_calls[-1]
    assert last_call.arguments == {"_auto_finalize": True}
    # last_snapshot_text 应反映"操作之后"的状态（含 9999）
    assert out.last_snapshot_text is not None
    assert "9999" in out.last_snapshot_text


@pytest.mark.asyncio
async def test_auto_finalize_skipped_when_last_call_is_already_snapshot() -> None:
    """如果 AI 最后一次主动调的就是 browser_snapshot，平台**不**再追加一次
    （避免冗余 / 工具序列虚长）。"""
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="c1",
                    name=f"{exec_id}__browser_snapshot",
                    arguments_json="{}",
                ),
            ],
            finish_reason="tool_calls",
            usage_total=10,
        ),
        ChatRound(content="完成。", finish_reason="stop", usage_total=5),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({
            f"{exec_id}__browser_snapshot": lambda _: {"snapshot": "- main\n  - heading 'Hi'"},
        }),
    )
    out = await runner.run_one(step_description="读快照", expected="看到 Hi")
    assert [tc.raw_name for tc in out.tool_calls] == ["browser_snapshot"]


@pytest.mark.asyncio
async def test_auto_finalize_failure_does_not_break_step() -> None:
    """auto-finalize 调用失败 / mcp_unavailable 时不能阻塞 step；保留之前的
    snapshot 让 AssertionJudge 至少有材料判定。"""
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(
            tool_calls=[
                ToolCallEmit(
                    id="c1",
                    name=f"{exec_id}__browser_click",
                    arguments_json='{"ref": "btn1"}',
                ),
            ],
            finish_reason="tool_calls",
            usage_total=10,
        ),
        ChatRound(content="点击完成。", finish_reason="stop", usage_total=5),
    )

    # browser_click 给一个 inline snapshot；browser_snapshot 故意没注册 → auto-
    # finalize 调用会拿到 ``unknown tool`` error 字典，提取不到 a11y 树就放弃。
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({
            f"{exec_id}__browser_click": lambda _: {"snapshot": "- main\n  - text '点击前的页面'"},
        }),
    )
    out = await runner.run_one(step_description="点击", expected="完成")
    assert out.success is True
    # 失败的 auto-finalize 不应 append 到 tool_calls
    assert [tc.raw_name for tc in out.tool_calls] == ["browser_click"]
    # 保留 click 之前的 snapshot 而不是清空
    assert out.last_snapshot_text is not None and "点击前的页面" in out.last_snapshot_text


@pytest.mark.asyncio
async def test_auto_finalize_skipped_when_no_tool_calls() -> None:
    """模型直接回答没调任何工具的 step（typical 信息类描述步骤），不触发兜底
    snapshot —— 没有需要刷新的页面状态。"""
    exec_id = uuid.uuid4()
    fn, _ = chat_rounds(
        ChatRound(content="无需操作。", finish_reason="stop", usage_total=10),
    )
    runner = StepRunner(
        llm=make_llm(),
        environment=make_env(),
        budget=TokenBudget(limit=10_000),
        execution_id=exec_id,
        chat_round_fn=fn,
        tool_runner=FakeTool({}),
    )
    out = await runner.run_one(step_description="确认操作无需执行", expected=None)
    assert out.success is True
    assert out.tool_calls == []
    assert out.last_snapshot_text is None
