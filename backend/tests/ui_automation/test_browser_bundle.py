"""Task 7.3 验证：BrowserBundle 生命周期 + MCP 失败回退。

策略：完全 mock ``async_playwright`` 和 ``MCPBridge``，避免真启 Chromium /
真起 npx 子进程。Chromium 二进制要到 Task 11.3 才装入镜像；本 task 的代
码逻辑（端口分配、关闭顺序、失败回退标记、tools 注册转发）全部能用 mock
覆盖。

不在本测试覆盖：
- 真实 ``playwright install chromium`` 后的 e2e。这一关在 Task 9.5
  ``ExecutionEngine`` 端到端测试或人工冒烟时再做。
"""

from __future__ import annotations

import os
import socket
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.ui_automation import browser_bundle as bb_mod
from app.modules.ui_automation.browser_bundle import (
    BrowserBundle,
    BundleOptions,
    _allocate_free_port,
)
from app.modules.ui_automation.mcp_bridge import MCPBridgeError


def _make_env() -> SimpleNamespace:
    return SimpleNamespace(
        base_url="https://staging.foo.com",
        allowed_hosts=["staging.foo.com"],
        token_budget=50_000,
        enable_browser_evaluate=False,
    )


def _patch_playwright(monkeypatch) -> dict:
    """把 async_playwright().start() 这条链全 mock 掉，返回测试可观察的句柄。

    架构变更（2026-05）：BrowserBundle 改用 ``launch_persistent_context`` ——
    chromium 单进程只暴露一个 BrowserContext，SDK 与 MCP 通过 CDP 共享同一
    个 cookie store。mock 链路相应简化：``launch_persistent_context`` 直接返
    回 BrowserContext，不再有独立的 Browser 对象（``bundle.browser`` 为 None
    或退化值）。

    返回字典含：context / pw / chromium / start / browser_compat（仅作 close
    顺序断言用，实际架构里没有这个对象）。
    """
    mock_context = MagicMock()
    mock_context.close = AsyncMock()
    # context.browser 在 persistent context 模式下通常是 None。这里显式置 None
    # 让 ``bundle.browser`` 也是 None，匹配真实生产路径。
    mock_context.browser = None
    mock_context.add_cookies = AsyncMock()
    mock_context.cookies = AsyncMock(return_value=[])
    # storage_state 注入需要这两个 API（add_cookies 已 mock；add_init_script
    # 用于 localStorage 回放）。
    mock_context.add_init_script = AsyncMock()

    mock_chromium = MagicMock()
    mock_chromium.launch_persistent_context = AsyncMock(return_value=mock_context)

    mock_pw = MagicMock()
    mock_pw.chromium = mock_chromium
    mock_pw.stop = AsyncMock()

    class _PWHandle:
        async def start(self_inner):  # noqa: N805
            return mock_pw

    monkeypatch.setattr(bb_mod, "async_playwright", lambda: _PWHandle())
    # 跳过 CDP wait（避免 5s 真等）
    monkeypatch.setattr(bb_mod, "_wait_cdp_ready", AsyncMock(return_value=None))

    return {
        "pw": mock_pw,
        "chromium": mock_chromium,
        "context": mock_context,
    }


def _patch_mcp_bridge(monkeypatch, *, fail_start: bool = False) -> dict:
    """把 ``MCPBridge.for_playwright`` 替换成 mock 工厂。"""
    bridge = MagicMock()
    bridge.start = AsyncMock(side_effect=MCPBridgeError("boom") if fail_start else None)
    bridge.close = AsyncMock()
    bridge.unregister = AsyncMock(return_value=2)
    bridge.discover_tools = AsyncMock(return_value=[
        {"type": "function", "function": {"name": "browser_navigate", "description": "x", "parameters": {}}},
        {"type": "function", "function": {"name": "browser_click", "description": "y", "parameters": {}}},
    ])
    bridge.register_into_agent_tools = MagicMock(return_value=[
        {"type": "function", "function": {"name": "abc__browser_navigate", "description": "x", "parameters": {}}},
        {"type": "function", "function": {"name": "abc__browser_click", "description": "y", "parameters": {}}},
    ])

    factory = MagicMock(return_value=bridge)
    monkeypatch.setattr("app.modules.ui_automation.browser_bundle.MCPBridge.for_playwright", factory)
    return {"bridge": bridge, "factory": factory}


# ─── _allocate_free_port ──────────────────────────────────────────────


def test_allocate_free_port_returns_usable() -> None:
    port = _allocate_free_port()
    assert 1024 <= port <= 65535
    # 端口归还后应该立刻可重 bind（防 SO_REUSEADDR / TIME_WAIT 类问题）
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


# ─── BrowserBundle.open 正常路径 ──────────────────────────────────────


async def test_open_starts_chromium_and_mcp(monkeypatch) -> None:
    pw_handles = _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch)

    env = _make_env()
    exec_id = uuid.uuid4()
    bundle = await BrowserBundle.open(env, exec_id, options=BundleOptions(headless=True))

    try:
        assert bundle.cdp_endpoint and bundle.cdp_endpoint.startswith("http://127.0.0.1:")
        # bundle.browser 在 persistent_context 路径下通常为 None（mock 也设了 None）
        assert bundle.browser is None
        assert bundle.context is pw_handles["context"]
        assert bundle.mcp_bridge is mcp_handles["bridge"]
        assert bundle.mcp_unavailable is False

        # 启动时 chromium.launch_persistent_context 必须传 --remote-debugging-port
        launch_call = pw_handles["chromium"].launch_persistent_context.await_args
        args_passed = launch_call.kwargs.get("args", [])
        assert any("--remote-debugging-port=" in a for a in args_passed)
        assert any("--remote-debugging-address=127.0.0.1" in a for a in args_passed)
        # user_data_dir 必传（persistent_context 强制要求）
        assert launch_call.kwargs.get("user_data_dir")

        # MCP factory 收到了 cdp_endpoint
        factory_call = mcp_handles["factory"].call_args
        assert factory_call.kwargs["cdp_endpoint"] == bundle.cdp_endpoint
    finally:
        await bundle.close()


async def test_headed_container_sets_display_and_x11_chrome_flags(monkeypatch) -> None:
    """容器 + 有头模式：补 DISPLAY 防误判 headless；为 Xvfb 追加 GPU/Ozone 安全参数。"""
    monkeypatch.setattr(bb_mod, "_is_container_environment", lambda: True)
    monkeypatch.delenv("DISPLAY", raising=False)
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(
        _make_env(),
        uuid.uuid4(),
        options=BundleOptions(headless=False),
    )
    try:
        assert os.environ.get("DISPLAY") == ":99"
        kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert kwargs["headless"] is False
        args = kwargs.get("args", [])
        assert "--ozone-platform=x11" in args
        assert "--disable-gpu" in args
        assert "--disable-gpu-compositing" in args
    finally:
        await bundle.close()
        monkeypatch.delenv("DISPLAY", raising=False)


async def test_open_passes_record_options(monkeypatch) -> None:
    """``record_video_dir`` / ``record_har_path`` 走 launch_persistent_context kwargs。

    回归保护：launch 调用必须 **不** 带 ``storage_state``——Playwright 1.59 实测
    ``BrowserType.launch_persistent_context`` 不接受这个 kwarg，硬塞会抛
    ``TypeError`` 直接打挂前置 state_inject。Storage state 的注入路径走
    ``test_open_injects_storage_state_via_add_cookies_and_init_script``。
    """
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(
        _make_env(),
        uuid.uuid4(),
        options=BundleOptions(
            record_video_dir="/tmp/videos",
            record_har_path="/tmp/x.har",
        ),
    )
    try:
        kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert "storage_state" not in kwargs, (
            "launch_persistent_context 不接受 storage_state kwarg；注入需走 launch 后路径"
        )
        assert kwargs["record_video_dir"] == "/tmp/videos"
        assert kwargs["record_har_path"] == "/tmp/x.har"
    finally:
        await bundle.close()


async def test_open_injects_storage_state_via_add_cookies_and_init_script(
    monkeypatch, tmp_path
) -> None:
    """有 ``storage_state_path`` 时走 launch 后注入：cookies → add_cookies；
    localStorage → add_init_script（脚本里按 origin 匹配 setItem）。
    """
    import json as _json

    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    state_file = tmp_path / "state.json"
    state_payload = {
        "cookies": [
            {
                "name": "sess",
                "value": "abc",
                "domain": ".foo.com",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [
            {
                "origin": "https://staging.foo.com",
                "localStorage": [{"name": "ui-token", "value": "xyz"}],
            }
        ],
    }
    state_file.write_text(_json.dumps(state_payload), encoding="utf-8")

    bundle = await BrowserBundle.open(
        _make_env(),
        uuid.uuid4(),
        options=BundleOptions(storage_state_path=str(state_file)),
    )
    try:
        # launch 不能带 storage_state（这是触发线上 TypeError 的根因）
        kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert "storage_state" not in kwargs

        # cookies 通过 context.add_cookies 注入
        ctx = pw_handles["context"]
        ctx.add_cookies.assert_awaited_once()
        cookie_arg = ctx.add_cookies.await_args.args[0]
        assert cookie_arg == state_payload["cookies"]

        # localStorage 通过 add_init_script 注入；脚本内必须含 origin 与 key/value
        ctx.add_init_script.assert_awaited_once()
        script = ctx.add_init_script.await_args.args[0]
        assert "https://staging.foo.com" in script
        assert "ui-token" in script and "xyz" in script
        assert "localStorage.setItem" in script
    finally:
        await bundle.close()


async def test_open_skips_state_injection_when_file_missing(
    monkeypatch, tmp_path
) -> None:
    """``storage_state_path`` 指向不存在文件时静默跳过，不报错也不调 add_cookies。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    missing = tmp_path / "nope.json"
    bundle = await BrowserBundle.open(
        _make_env(),
        uuid.uuid4(),
        options=BundleOptions(storage_state_path=str(missing)),
    )
    try:
        ctx = pw_handles["context"]
        ctx.add_cookies.assert_not_awaited()
        ctx.add_init_script.assert_not_awaited()
    finally:
        await bundle.close()


async def test_open_skips_state_injection_on_invalid_json(
    monkeypatch, tmp_path
) -> None:
    """state 文件存在但不是合法 JSON → 仅记日志，bundle.open 仍然成功。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")

    bundle = await BrowserBundle.open(
        _make_env(),
        uuid.uuid4(),
        options=BundleOptions(storage_state_path=str(bad)),
    )
    try:
        ctx = pw_handles["context"]
        ctx.add_cookies.assert_not_awaited()
        ctx.add_init_script.assert_not_awaited()
        # bundle 本身仍可用（不会因为 state 损坏而打挂整个 execution）
        assert bundle.context is ctx
    finally:
        await bundle.close()


async def test_open_passes_browser_proxy_to_chromium_launch(monkeypatch) -> None:
    """VPN 场景：environment / settings 配了 browser_proxy 时必须透传给
    Playwright 的 ``chromium.launch(proxy=...)`` 参数。如果没透传，chromium 就
    走容器默认出口，VPN 内网永远连不通（这就是 macOS Docker Desktop 上的
    "宿主机能通、容器不通"问题，详见 docker-compose.vpn.yml docstring）。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(
        _make_env(), uuid.uuid4(),
        options=BundleOptions(
            browser_proxy="http://host.docker.internal:8118",
            browser_proxy_bypass="localhost,127.0.0.1,db",
        ),
    )
    try:
        launch_kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert "proxy" in launch_kwargs, (
            "browser_proxy 必须以 ``proxy=`` 形式传给 launch_persistent_context"
            "（让 Playwright 内部正确处理 CDP bypass + bypass 列表格式转换）"
        )
        assert launch_kwargs["proxy"] == {
            "server": "http://host.docker.internal:8118",
            "bypass": "localhost,127.0.0.1,db",
        }
    finally:
        await bundle.close()


async def test_open_omits_proxy_when_not_configured(monkeypatch) -> None:
    """没配 browser_proxy → launch 时**不**应该传 ``proxy=``（playwright 收到
    空 server 会报错），保持默认直连行为。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        launch_kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert "proxy" not in launch_kwargs
    finally:
        await bundle.close()


# ─── reset_for_next_case（用例间页面状态清理）─────────────────────────


async def test_reset_for_next_case_closes_extras_and_blanks_primary(monkeypatch) -> None:
    """两条用例之间：除主 page 外的多余 page 全 close，主 page 跳 about:blank。

    这是批量执行最常见污染源——上条用例打开了弹窗 / 新 tab 没关，下条用例
    进来 AI 看到的还是上条的页面状态。``reset_for_next_case`` 必须把这些
    残留收掉，但 **不** 动 cookies / localStorage / storage_state（那些是
    登录态载体，清掉每条用例都得重登）。
    """
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        primary = MagicMock()
        primary.is_closed = MagicMock(return_value=False)
        primary.close = AsyncMock()
        primary.goto = AsyncMock()

        popup = MagicMock()
        popup.is_closed = MagicMock(return_value=False)
        popup.close = AsyncMock()

        new_tab = MagicMock()
        new_tab.is_closed = MagicMock(return_value=False)
        new_tab.close = AsyncMock()

        ctx = pw_handles["context"]
        ctx.pages = [primary, popup, new_tab]
        # 登录态相关 API 显式插桩；reset 实现里 **绝对不能** 调它们
        ctx.clear_cookies = AsyncMock()
        ctx.clear_storage_state = AsyncMock()

        report = await bundle.reset_for_next_case()

        primary.close.assert_not_awaited()
        popup.close.assert_awaited_once()
        new_tab.close.assert_awaited_once()

        primary.goto.assert_awaited_once()
        called_args = primary.goto.await_args
        assert called_args.args[0] == "about:blank"
        assert called_args.kwargs.get("wait_until") in ("commit", "domcontentloaded")

        assert report["closed_extra_pages"] == 2
        assert report["navigated_to_blank"] is True
        assert report["errors"] == []

        # 关键：登录态相关 API 一律不能调用——清掉就等于强制每条用例都重登
        ctx.clear_cookies.assert_not_awaited()
        ctx.clear_storage_state.assert_not_awaited()
    finally:
        await bundle.close()


async def test_reset_for_next_case_swallows_errors(monkeypatch) -> None:
    """reset 期间任何步骤失败都不应抛——next case 还能靠 prompt 引导兜底。

    这条是回归保护：未来给 reset 加新动作时，没人能再"顺手"让它在异常路径上
    raise，否则一次小故障就会把整批用例打挂。
    """
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        primary = MagicMock()
        primary.is_closed = MagicMock(return_value=False)
        primary.goto = AsyncMock(side_effect=RuntimeError("fake nav fail"))

        popup = MagicMock()
        popup.is_closed = MagicMock(return_value=False)
        popup.close = AsyncMock(side_effect=RuntimeError("fake close fail"))

        pw_handles["context"].pages = [primary, popup]

        report = await bundle.reset_for_next_case()
        # 不抛
        assert isinstance(report, dict)
        assert len(report["errors"]) >= 1, "失败步骤必须落到 errors 列表"
    finally:
        await bundle.close()


async def test_reset_for_next_case_handles_empty_context(monkeypatch) -> None:
    """context 里完全没有 page（极端情况）→ 啥也不做、不抛。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        pw_handles["context"].pages = []
        report = await bundle.reset_for_next_case()
        assert report["closed_extra_pages"] == 0
        assert report["navigated_to_blank"] is False
    finally:
        await bundle.close()


async def test_open_omits_proxy_when_browser_proxy_is_empty_string(monkeypatch) -> None:
    """``UI_BROWSER_PROXY=""`` 这种环境变量未配但被 docker-compose 传入空串的
    情况，应该等价于"未配"，不传 proxy 给 chromium。否则 playwright 会因 server
    为空报错。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(
        _make_env(), uuid.uuid4(),
        options=BundleOptions(browser_proxy="   ", browser_proxy_bypass=""),
    )
    try:
        launch_kwargs = pw_handles["chromium"].launch_persistent_context.await_args.kwargs
        assert "proxy" not in launch_kwargs
    finally:
        await bundle.close()


async def test_open_skips_mcp_when_disabled(monkeypatch) -> None:
    """``mcp_enabled=False`` → 不触发 MCP 启动，bundle.mcp_bridge=None。"""
    _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(
        _make_env(), uuid.uuid4(),
        options=BundleOptions(mcp_enabled=False),
    )
    try:
        assert bundle.mcp_bridge is None
        assert bundle.mcp_unavailable is False  # 是被显式禁用，不算"不可用"
        mcp_handles["factory"].assert_not_called()
    finally:
        await bundle.close()


# ─── MCP 失败回退（关键设计！）────────────────────────────────────────


async def test_mcp_start_failure_falls_back_to_sdk_only(monkeypatch) -> None:
    """文档 §3.2：MCP 启动失败时 bundle 仍可用，标记 mcp_unavailable=True。

    这条很关键 — 如果 MCP 子进程挂了直接 raise，整个二期模块就因为
    Node / npx 装不上而瘫痪。设计要求：浏览器仍在线，留给 Engine 选择
    "纯 SDK 兜底"或"明确终止"。
    """
    _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch, fail_start=True)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        assert bundle.mcp_unavailable is True
        assert bundle.mcp_unavailable_reason is not None
        assert "boom" in bundle.mcp_unavailable_reason
        assert bundle.mcp_bridge is None  # 启动失败的 bridge 不应被持有
        # 浏览器（context）本身还在；persistent_context 模式 ``bundle.browser`` 通常 None
        assert bundle.context is not None
        # 失败的 bridge 也被 close 了一次（清子进程）
        mcp_handles["bridge"].close.assert_awaited()
    finally:
        await bundle.close()


async def test_mcp_start_failure_with_filenotfound(monkeypatch) -> None:
    """系统没装 npx → FileNotFoundError，应被同样路径捕获。"""
    _patch_playwright(monkeypatch)
    bridge = MagicMock()
    bridge.start = AsyncMock(side_effect=FileNotFoundError("npx not found"))
    bridge.close = AsyncMock()
    monkeypatch.setattr(
        "app.modules.ui_automation.browser_bundle.MCPBridge.for_playwright",
        MagicMock(return_value=bridge),
    )

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        assert bundle.mcp_unavailable is True
        assert "npx" in bundle.mcp_unavailable_reason
    finally:
        await bundle.close()


# ─── register_mcp_tools_for_agent ─────────────────────────────────────


async def test_register_mcp_tools_returns_namespaced_specs(monkeypatch) -> None:
    _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        specs = await bundle.register_mcp_tools_for_agent()
        assert len(specs) == 2
        # 名字应被 namespace 化（mock 的 register_into_agent_tools 返回 abc__ 前缀）
        # 分隔符是 ``__``：OpenAI tool name 必须匹配 ``^[a-zA-Z0-9_-]+$``，``:`` 非法。
        assert all("__" in s["function"]["name"] for s in specs)
        mcp_handles["bridge"].discover_tools.assert_awaited_once()
        mcp_handles["bridge"].register_into_agent_tools.assert_called_once()
    finally:
        await bundle.close()


async def test_register_mcp_tools_returns_empty_when_unavailable(monkeypatch) -> None:
    _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch, fail_start=True)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        specs = await bundle.register_mcp_tools_for_agent()
        assert specs == []
    finally:
        await bundle.close()


# ─── close 行为 ───────────────────────────────────────────────────────


async def test_close_is_idempotent(monkeypatch) -> None:
    pw_handles = _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    await bundle.close()
    await bundle.close()  # 重复调不应抛错
    # 每个资源只被关一次（不会因为重复调用 close 把 mock count 涨成 2）
    assert pw_handles["context"].close.await_count == 1
    assert pw_handles["pw"].stop.await_count == 1
    assert mcp_handles["bridge"].close.await_count == 1


async def test_close_continues_on_partial_failure(monkeypatch) -> None:
    """如果 context.close 抛错，pw.stop 仍要执行（防资源泄漏）。"""
    pw_handles = _patch_playwright(monkeypatch)
    pw_handles["context"].close = AsyncMock(side_effect=RuntimeError("ctx fail"))
    _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    await bundle.close()
    assert pw_handles["pw"].stop.await_count == 1


async def test_async_context_manager(monkeypatch) -> None:
    _patch_playwright(monkeypatch)
    mcp_handles = _patch_mcp_bridge(monkeypatch)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    async with bundle:
        assert bundle.context is not None
    # 出 with 应已 close
    mcp_handles["bridge"].close.assert_awaited()


async def test_open_partial_failure_cleans_up(monkeypatch) -> None:
    """launch_persistent_context 失败时不应留下 pw runtime / 半开 browser 占用。"""
    pw_handles = _patch_playwright(monkeypatch)
    pw_handles["chromium"].launch_persistent_context = AsyncMock(
        side_effect=RuntimeError("launch fail")
    )
    _patch_mcp_bridge(monkeypatch)

    with pytest.raises(RuntimeError):
        await BrowserBundle.open(_make_env(), uuid.uuid4())
    # pw.stop 应该被调用（即便 launch 失败也清理 playwright runtime）
    pw_handles["pw"].stop.assert_awaited()


# ─── persistent_context 架构验证 ──────────────────────────────────────


async def test_persistent_context_uses_unique_user_data_dir(monkeypatch) -> None:
    """每次 open 必须分配独立的 user_data_dir，防 cookie / cache 跨 execution 污染。

    回归保护：早期实现共享 user_data_dir 时，并发执行会出现一个 execution 的
    登录态被另一个误用的诡异问题。
    """
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)
    b1 = await BrowserBundle.open(_make_env(), uuid.uuid4())
    dir1 = pw_handles["chromium"].launch_persistent_context.await_args.kwargs["user_data_dir"]
    await b1.close()

    pw_handles2 = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)
    b2 = await BrowserBundle.open(_make_env(), uuid.uuid4())
    dir2 = pw_handles2["chromium"].launch_persistent_context.await_args.kwargs["user_data_dir"]
    await b2.close()

    assert dir1 != dir2, "每次 execution 必须独立的 user_data_dir"


async def test_close_cleans_up_user_data_dir(monkeypatch, tmp_path) -> None:
    """close 时 user_data_dir 必须被删掉，避免容器 disk 越用越大。"""
    pw_handles = _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch)
    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    user_data_dir = pw_handles["chromium"].launch_persistent_context.await_args.kwargs["user_data_dir"]
    import os as _os
    assert _os.path.isdir(user_data_dir), "tempdir 应在 launch 时被创建"
    await bundle.close()
    assert not _os.path.exists(user_data_dir), "close 后 tempdir 必须被清理"


# ─── _scan_first_url ──────────────────────────────────────────────────


def test_scan_first_url_extracts_from_yaml() -> None:
    from app.modules.ui_automation.browser_bundle import _scan_first_url
    text = 'tabs:\n  - url: "https://x.com/home/dashboard"\n    title: "x"\n'
    assert _scan_first_url(text) == "https://x.com/home/dashboard"


def test_scan_first_url_extracts_from_plain_text() -> None:
    from app.modules.ui_automation.browser_bundle import _scan_first_url
    assert _scan_first_url("当前 URL: https://a.b/login") == "https://a.b/login"


def test_scan_first_url_handles_no_url() -> None:
    from app.modules.ui_automation.browser_bundle import _scan_first_url
    assert _scan_first_url("no url here") is None
    assert _scan_first_url("") is None


def test_scan_first_url_strips_trailing_punctuation() -> None:
    """URL 末尾的引号 / 括号 / 逗号不该被吞进来 —— 影响 in 子串匹配判定。"""
    from app.modules.ui_automation.browser_bundle import _scan_first_url
    assert _scan_first_url('"https://x.com/path",') == "https://x.com/path"
    assert _scan_first_url("(https://x.com/p)") == "https://x.com/p"


# ─── get_current_url_via_mcp ──────────────────────────────────────────


async def test_get_current_url_via_mcp_returns_none_when_unavailable(monkeypatch) -> None:
    """MCP 不可用时不该抛错，直接返回 None 让上层兜底。"""
    _patch_playwright(monkeypatch)
    _patch_mcp_bridge(monkeypatch, fail_start=True)
    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        url = await bundle.get_current_url_via_mcp()
        assert url is None
    finally:
        await bundle.close()


async def test_get_current_url_via_mcp_uses_tabs_list(monkeypatch) -> None:
    """browser_tabs_list 成功 → 取里面第一条 URL，后续 evaluate / snapshot 不再调。"""
    _patch_playwright(monkeypatch)
    handles = _patch_mcp_bridge(monkeypatch)

    async def _call_tool(name, args):
        if name in ("browser_tabs_list", "browser_tabs"):
            return {
                "content": '1. "https://app.example.com/home/dashboard" — Dashboard\n',
                "is_error": False,
                "raw": [],
            }
        # 不应被调到——本测试断言短路在第一条路径
        raise AssertionError(f"unexpected mcp call: {name}")

    handles["bridge"].call_tool = AsyncMock(side_effect=_call_tool)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        url = await bundle.get_current_url_via_mcp()
        assert url == "https://app.example.com/home/dashboard"
    finally:
        await bundle.close()


async def test_get_current_url_via_mcp_falls_back_to_evaluate(monkeypatch) -> None:
    """tabs_list 不可用（is_error / 异常）→ 退到 browser_evaluate(window.location.href)。"""
    _patch_playwright(monkeypatch)
    handles = _patch_mcp_bridge(monkeypatch)

    seen: list[str] = []

    async def _call_tool(name, args):
        seen.append(name)
        if name in ("browser_tabs_list", "browser_tabs"):
            raise RuntimeError("tabs_list 不在该 mcp 实现里")
        if name == "browser_evaluate":
            return {
                "content": "https://app.example.com/login",
                "is_error": False,
                "raw": [],
            }
        return {"content": "", "is_error": False, "raw": []}

    handles["bridge"].call_tool = AsyncMock(side_effect=_call_tool)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        url = await bundle.get_current_url_via_mcp()
        assert url == "https://app.example.com/login"
        assert "browser_evaluate" in seen
    finally:
        await bundle.close()


async def test_get_current_url_via_mcp_returns_none_when_all_paths_fail(monkeypatch) -> None:
    """所有 MCP 工具都拿不到 URL → 干净返回 None，不抛异常。"""
    _patch_playwright(monkeypatch)
    handles = _patch_mcp_bridge(monkeypatch)

    async def _call_tool(name, args):
        return {"content": "no url here", "is_error": False, "raw": []}

    handles["bridge"].call_tool = AsyncMock(side_effect=_call_tool)

    bundle = await BrowserBundle.open(_make_env(), uuid.uuid4())
    try:
        url = await bundle.get_current_url_via_mcp()
        assert url is None
    finally:
        await bundle.close()
