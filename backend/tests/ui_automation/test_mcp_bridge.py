"""Task 7.2 验证：MCPBridge — tool discovery、命名空间隔离、生命周期。

策略：
1. **Mock 单测**：替换 ``ClientSession`` 的 ``list_tools`` / ``call_tool``
   验证 bridge 的"schema 转 OpenAI 格式 + 注册到 TOOL_REGISTRY + 命名空间
   清理"逻辑（不依赖真子进程，跑得快、不挑环境）。
2. **真实 stdio 集成测试**：起 ``_fake_mcp_server.py`` 子进程，端到端走一
   遍 MCPBridge.start → discover → call → close 流程，验证 stdio 协议层
   也通的（默认开启，CI 也能跑，因为只用 Python，不需要 Node + npx）。

不在这一关测的：
- 真实 ``npx @playwright/mcp``（需要 Node + 浏览器，留给 Task 7.3 的 e2e）
- 失败重试 3 次（间接通过 ``MCPNotStartedError`` + start 失败路径覆盖）
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.llm.agent_tools import (
    TOOL_REGISTRY,
    register_tool,
    unregister_namespace,
    unregister_tool,
)
from app.modules.ui_automation.mcp_bridge import (
    MCPBridge,
    MCPBridgeError,
    MCPNotStartedError,
    _normalize_input_schema,
    serialize_call_result,
)

FAKE_SERVER_PATH = Path(__file__).parent / "_fake_mcp_server.py"


# ─── Fixture：每条测试都拿到干净的 TOOL_REGISTRY 切片 ────────────────────


@pytest.fixture(autouse=True)
def isolate_tool_registry():
    """测试期间动态注册的 tool 一律隔离，结束时恢复 TOOL_REGISTRY 到测试前状态。

    一期 ``web_search`` 等模块级 tool 不能被误删。
    """
    snapshot = dict(TOOL_REGISTRY)
    yield
    extra = set(TOOL_REGISTRY) - set(snapshot)
    for name in extra:
        TOOL_REGISTRY.pop(name, None)
    # 恢复可能被覆盖的 tool（比如某条测试 register 了同名 tool）
    for name, fn in snapshot.items():
        TOOL_REGISTRY[name] = fn


# ─────────────────── 1. Mock 单测：bridge 行为契约 ───────────────────


class _FakeTool(SimpleNamespace):
    """模拟 mcp.types.Tool（只用到 .name / .description / .inputSchema）。"""


class _FakeContent(SimpleNamespace):
    """模拟 mcp.types.TextContent（只用到 .type / .text）。"""


def _make_mock_session(tools: list[_FakeTool], call_result_content: str = "ok") -> MagicMock:
    """构造一个 ClientSession mock，list_tools / call_tool 都能 await。"""
    session = MagicMock()
    session.list_tools = AsyncMock(return_value=SimpleNamespace(tools=tools))
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[_FakeContent(type="text", text=call_result_content)],
            isError=False,
        )
    )
    return session


def _patch_bridge_session(bridge: MCPBridge, session: MagicMock) -> None:
    """直接把 bridge 的内部 session 设置为 mock，跳过 stdio_client 子进程。

    这是为了不在单测里起 npx；我们已经有 stdio 集成测试覆盖那条路径了。
    """
    bridge._session = session
    bridge._stack = MagicMock()
    bridge._stack.aclose = AsyncMock()


async def test_discover_tools_converts_to_openai_format() -> None:
    bridge = MCPBridge(command="dummy")
    fake_tools = [
        _FakeTool(
            name="browser_navigate",
            description="Open a URL",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        _FakeTool(
            name="browser_click",
            description="Click an element",
            inputSchema={
                "properties": {"selector": {"type": "string"}}
            },  # 故意缺 type，验证 _normalize 兜底
        ),
    ]
    _patch_bridge_session(bridge, _make_mock_session(fake_tools))

    specs = await bridge.discover_tools()
    assert len(specs) == 2

    nav = specs[0]
    assert nav["type"] == "function"
    assert nav["function"]["name"] == "browser_navigate"
    assert nav["function"]["description"] == "Open a URL"
    assert nav["function"]["parameters"]["type"] == "object"
    assert nav["function"]["parameters"]["required"] == ["url"]

    click = specs[1]
    # 缺 type 的 inputSchema 应被补上 "object"
    assert click["function"]["parameters"]["type"] == "object"


async def test_register_into_agent_tools_applies_namespace() -> None:
    bridge = MCPBridge(command="dummy")
    fake_tools = [_FakeTool(name="browser_navigate", description="x", inputSchema={})]
    _patch_bridge_session(bridge, _make_mock_session(fake_tools))

    specs = await bridge.discover_tools()
    exec_id = uuid.uuid4()
    namespaced = bridge.register_into_agent_tools(specs, execution_id=exec_id)

    expected_name = f"{exec_id}__browser_navigate"
    assert namespaced[0]["function"]["name"] == expected_name
    assert expected_name in TOOL_REGISTRY
    # 原始名（无 namespace）不应注册
    assert "browser_navigate" not in TOOL_REGISTRY


async def test_namespaced_tool_executor_routes_to_call_tool() -> None:
    """注册的 executor 被 run_tool 调用时，应该转发到 MCPBridge.call_tool。"""
    bridge = MCPBridge(command="dummy")
    fake_tools = [_FakeTool(name="browser_navigate", description="x", inputSchema={})]
    session = _make_mock_session(fake_tools, call_result_content="OK navigated")
    _patch_bridge_session(bridge, session)

    specs = await bridge.discover_tools()
    exec_id = uuid.uuid4()
    bridge.register_into_agent_tools(specs, execution_id=exec_id)

    executor = TOOL_REGISTRY[f"{exec_id}__browser_navigate"]
    result = await executor({"url": "https://example.com"})

    assert result["is_error"] is False
    assert result["content"] == "OK navigated"
    session.call_tool.assert_awaited_once_with(
        "browser_navigate", arguments={"url": "https://example.com"}
    )


async def test_unregister_clears_only_target_namespace() -> None:
    """两个 execution 并发注册 → 各自 unregister 不会污染对方。"""
    bridge = MCPBridge(command="dummy")
    fake_tools = [
        _FakeTool(name="browser_navigate", description="x", inputSchema={}),
        _FakeTool(name="browser_click", description="y", inputSchema={}),
    ]
    _patch_bridge_session(bridge, _make_mock_session(fake_tools))

    exec_a = uuid.uuid4()
    exec_b = uuid.uuid4()
    specs = await bridge.discover_tools()
    bridge.register_into_agent_tools(specs, execution_id=exec_a)
    bridge.register_into_agent_tools(specs, execution_id=exec_b)

    a_keys = [k for k in TOOL_REGISTRY if k.startswith(f"{exec_a}__")]
    b_keys = [k for k in TOOL_REGISTRY if k.startswith(f"{exec_b}__")]
    assert len(a_keys) == 2 and len(b_keys) == 2

    removed = await bridge.unregister(execution_id=exec_a)
    assert removed == 2
    assert not any(k.startswith(f"{exec_a}__") for k in TOOL_REGISTRY)
    # exec_b 的 tools 仍在
    assert all(k.startswith(f"{exec_b}__") for k in [
        k for k in TOOL_REGISTRY if k.startswith(str(exec_b))
    ])


async def test_close_clears_all_namespaces() -> None:
    """bridge.close() 必须兜底清掉**所有**注册过的命名空间。"""
    bridge = MCPBridge(command="dummy")
    fake_tools = [_FakeTool(name="browser_navigate", description="x", inputSchema={})]
    _patch_bridge_session(bridge, _make_mock_session(fake_tools))

    exec_a, exec_b = uuid.uuid4(), uuid.uuid4()
    specs = await bridge.discover_tools()
    bridge.register_into_agent_tools(specs, execution_id=exec_a)
    bridge.register_into_agent_tools(specs, execution_id=exec_b)

    await bridge.close()
    assert not any("__" in k and (str(exec_a) in k or str(exec_b) in k) for k in TOOL_REGISTRY)


async def test_call_tool_before_start_raises() -> None:
    bridge = MCPBridge(command="dummy")
    with pytest.raises(MCPNotStartedError):
        await bridge.call_tool("browser_navigate", {"url": "x"})


async def test_call_tool_executor_handles_bridge_errors() -> None:
    """executor 必须把异常转成结构化 dict，不能 raise 把 agent loop 干掉。"""
    bridge = MCPBridge(command="dummy")
    fake_tools = [_FakeTool(name="t", description="x", inputSchema={})]
    session = _make_mock_session(fake_tools)
    session.call_tool = AsyncMock(side_effect=RuntimeError("boom"))
    _patch_bridge_session(bridge, session)
    specs = await bridge.discover_tools()
    exec_id = uuid.uuid4()
    bridge.register_into_agent_tools(specs, execution_id=exec_id)

    executor = TOOL_REGISTRY[f"{exec_id}__t"]
    result = await executor({})
    assert result["is_error"] is True
    assert "boom" in result["error"]


async def test_call_tool_normalizes_image_content() -> None:
    """图像内容只统计长度，不进 text，避免污染 token 预算。"""
    bridge = MCPBridge(command="dummy")
    fake_tools = [_FakeTool(name="screenshot", description="x", inputSchema={})]
    session = _make_mock_session(fake_tools)
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            content=[
                _FakeContent(type="text", text="screenshot ok"),
                _FakeContent(type="image", mimeType="image/png", data="A" * 1000),
            ],
            isError=False,
        )
    )
    _patch_bridge_session(bridge, session)
    result = await bridge.call_tool("screenshot", {})
    assert result["content"] == "screenshot ok"
    assert any(r["type"] == "image" for r in result["raw"])
    image = next(r for r in result["raw"] if r["type"] == "image")
    assert image["data_len"] == 1000


async def test_normalize_input_schema_handles_none() -> None:
    """部分 MCP server 会返回 None 或非 dict 的 inputSchema，要兜底。"""
    assert _normalize_input_schema(None) == {"type": "object", "properties": {}}
    assert _normalize_input_schema("garbage") == {"type": "object", "properties": {}}
    schema = _normalize_input_schema({"properties": {"x": {"type": "string"}}})
    assert schema["type"] == "object"


def test_serialize_call_result_truncates_long_content() -> None:
    long_content = "x" * 20000
    out = serialize_call_result({"content": long_content, "is_error": False})
    assert len(out) < 9000  # 8000 + 少量 JSON 框架
    assert "x" in out


def test_serialize_call_result_marks_errors() -> None:
    out = serialize_call_result({"content": "boom", "is_error": True})
    assert '"error": true' in out


# ─────────────────── 2. start() 重试逻辑 ───────────────────


async def test_start_retries_then_raises_after_max_attempts(monkeypatch) -> None:
    """模拟 stdio_client 一直失败：bridge 应重试 3 次后抛 MCPBridgeError。"""
    from app.modules.ui_automation import mcp_bridge as mb

    call_count = {"n": 0}

    class _BoomCtx:
        async def __aenter__(self):
            call_count["n"] += 1
            raise OSError("simulated stdio failure")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(mb, "stdio_client", lambda params: _BoomCtx())
    # 把 backoff 缩到 0，让测试不等真等
    monkeypatch.setattr(mb, "_START_BACKOFF_BASE", 0.0)

    bridge = MCPBridge(command="dummy")
    with pytest.raises(MCPBridgeError) as ei:
        await bridge.start()
    assert "failed to start" in str(ei.value).lower()
    assert call_count["n"] == 3  # 3 次重试都被消耗


async def test_start_succeeds_on_second_attempt(monkeypatch) -> None:
    """第二次成功：start 应该静默恢复，不抛错。"""
    from app.modules.ui_automation import mcp_bridge as mb

    state = {"attempt": 0}

    class _Ctx:
        async def __aenter__(self):
            state["attempt"] += 1
            if state["attempt"] == 1:
                raise OSError("first try fails")
            # 第二次返回一对 stream stub
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc):
            return False

    class _SessionCtx:
        async def __aenter__(self):
            sess = MagicMock()
            sess.initialize = AsyncMock(return_value=None)
            return sess

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(mb, "stdio_client", lambda params: _Ctx())
    monkeypatch.setattr(mb, "ClientSession", lambda r, w: _SessionCtx())
    monkeypatch.setattr(mb, "_START_BACKOFF_BASE", 0.0)

    bridge = MCPBridge(command="dummy")
    await bridge.start()
    assert bridge.is_started
    await bridge.close()


# ─────────────────── 3. 真实 stdio 集成测试 ───────────────────


async def test_integration_real_stdio_fake_mcp_server() -> None:
    """端到端：起 _fake_mcp_server.py 子进程，走完整 MCP 握手 + tool 调用。

    用 ``sys.executable`` 而不是 Node，确保 CI / 任何机器都能跑（不依赖
    ``npx @playwright/mcp`` 这套 Node 链路）。这一关通过即证明：
      - StdioServerParameters / stdio_client 装好了
      - ClientSession.initialize → list_tools → call_tool 链路通
      - bridge 的 schema 转换 + namespace 注册和真实 MCP 协议兼容
    """
    bridge = MCPBridge(
        command=sys.executable,
        args=[str(FAKE_SERVER_PATH)],
        name="fake-stdio",
    )
    await bridge.start()
    try:
        specs = await bridge.discover_tools()
        names = {s["function"]["name"] for s in specs}
        assert names == {"browser_navigate", "browser_click"}

        exec_id = uuid.uuid4()
        bridge.register_into_agent_tools(specs, execution_id=exec_id)

        nav_executor = TOOL_REGISTRY[f"{exec_id}__browser_navigate"]
        result = await nav_executor({"url": "https://example.com"})
        assert result["is_error"] is False
        assert "navigated: https://example.com" in result["content"]

        click_executor = TOOL_REGISTRY[f"{exec_id}__browser_click"]
        click_result = await click_executor({"selector": "#btn", "force": True})
        assert "clicked: #btn force=True" in click_result["content"]
    finally:
        await bridge.close()


# ─────────────────── 4. agent_tools helpers 直测 ───────────────────


def test_register_and_unregister_namespace_helpers() -> None:
    async def dummy(args):  # noqa: ARG001
        return {"ok": True}

    register_tool("ns_x__tool_a", dummy)
    register_tool("ns_x__tool_b", dummy)
    register_tool("ns_y__tool_c", dummy)

    assert unregister_namespace("ns_x") == 2
    assert "ns_y__tool_c" in TOOL_REGISTRY
    assert "ns_x__tool_a" not in TOOL_REGISTRY
    assert unregister_namespace("ns_x") == 0  # 二次调用幂等


def test_unregister_tool_returns_bool() -> None:
    async def dummy(args):  # noqa: ARG001
        return {}

    register_tool("nope:foo", dummy)
    assert unregister_tool("nope:foo") is True
    assert unregister_tool("nope:foo") is False
