"""Task 8.2 验证：前置步骤执行器。

测试策略：
- 用 ``unittest.mock.AsyncMock`` 全 mock BrowserBundle 及其 ``context`` / ``page``
- 不依赖真 Playwright / Chromium / MCP
- 4 种 type 每种至少 1 个 happy + 1 个 failure
- state_inject 重点测：缺文件降级、过期降级、未过期通过
- scripted_steps 重点测：白名单拦截、模板替换、缺凭据报错
- cookie_inject 重点测：value_ref 解析、credentials 替换
- 主入口测：超时、异常兜底、截图失败不影响主流程、state 持久化
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.ui_automation.precondition_executor import (
    ALLOWED_SCRIPT_ACTIONS,
    DEFAULT_STALE_KEYWORDS,
    _apply_password_hash,
    _interpolate,
    _match_stale_keyword,
    _parse_set_cookie_first,
    _render_http_template,
    _resolve_cookie_spec,
    _resolve_value_ref,
    _StubAILoginRunner,
    run_precondition,
)

# ─── 测试工具：mock BrowserBundle ─────────────────────────────────────


def make_mock_bundle(
    *,
    pages_initial: list | None = None,
    mcp_unavailable: bool = True,
    mcp_snapshot: str | None = None,
    page_content: str = "",
    storage_state_side_effect=None,
) -> SimpleNamespace:
    """构造 BrowserBundle stub。

    用 SimpleNamespace 而非 MagicMock(spec=BrowserBundle) 是因为 Bundle 的
    ``context`` / ``mcp_bridge`` 是运行时属性，spec 校验意义不大。
    """
    page = AsyncMock()
    page.content = AsyncMock(return_value=page_content)
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")
    page.goto = AsyncMock()
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.press = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.select_option = AsyncMock()
    page.check = AsyncMock()
    page.uncheck = AsyncMock()

    pages = list(pages_initial) if pages_initial is not None else [page]
    context = MagicMock()
    context.pages = pages
    context.new_page = AsyncMock(return_value=page)
    context.add_cookies = AsyncMock()
    context.storage_state = AsyncMock(side_effect=storage_state_side_effect)

    mcp_bridge = None
    if not mcp_unavailable:
        mcp_bridge = AsyncMock()
        mcp_bridge.call_tool = AsyncMock(
            return_value={"snapshot": mcp_snapshot or ""},
        )

    return SimpleNamespace(
        context=context,
        mcp_bridge=mcp_bridge,
        mcp_unavailable=mcp_unavailable,
        execution_id=uuid.uuid4(),
        _page=page,  # 留个引用方便测试断言
    )


def make_template(
    *,
    type: str,
    config: dict | None = None,
    name: str = "test-template",
    template_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    """模仿 PreconditionTemplate ORM 对象（避免依赖 DB）。"""
    return SimpleNamespace(
        id=template_id or uuid.uuid4(),
        name=name,
        type=type,
        config=config or {},
        credentials_encrypted=None,
        environment_id=uuid.uuid4(),
    )


# ─── 纯函数 helpers ───────────────────────────────────────────────────


def test_match_stale_keyword_returns_first_hit() -> None:
    assert _match_stale_keyword("用户中心 - 请登录", DEFAULT_STALE_KEYWORDS) == "请登录"
    assert _match_stale_keyword("welcome", DEFAULT_STALE_KEYWORDS) is None
    assert _match_stale_keyword("", DEFAULT_STALE_KEYWORDS) is None


def test_interpolate_replaces_credentials() -> None:
    assert _interpolate("U={{credentials.user}}", {"user": "alice"}) == "U=alice"
    # 多字段
    out = _interpolate("{{credentials.u}}/{{credentials.p}}", {"u": "a", "p": "b"})
    assert out == "a/b"


def test_interpolate_no_placeholder_returns_as_is() -> None:
    assert _interpolate("plain text", {"x": "y"}) == "plain text"


def test_interpolate_raises_when_creds_missing() -> None:
    with pytest.raises(ValueError, match="未提供凭据"):
        _interpolate("{{credentials.user}}", None)


def test_interpolate_raises_when_field_missing() -> None:
    with pytest.raises(ValueError, match="找不到引用的字段"):
        _interpolate("{{credentials.missing}}", {"user": "x"})


def test_resolve_value_ref_literal() -> None:
    assert _resolve_value_ref("literal:abc", None) == "abc"
    assert _resolve_value_ref("literal:", None) == ""


def test_resolve_value_ref_credentials() -> None:
    assert _resolve_value_ref("credentials.token", {"token": "xyz"}) == "xyz"


def test_resolve_value_ref_unknown_format_raises() -> None:
    with pytest.raises(ValueError):
        _resolve_value_ref("evil_format", {})


def test_resolve_cookie_spec_basic_with_value() -> None:
    spec = {"name": "sid", "value": "abc", "domain": "x.com", "path": "/"}
    out = _resolve_cookie_spec(spec, None)
    assert out == {"name": "sid", "value": "abc", "domain": "x.com", "path": "/"}


def test_resolve_cookie_spec_with_value_ref() -> None:
    spec = {
        "name": "sid", "value_ref": "credentials.session_id",
        "domain": "x.com", "path": "/",
    }
    out = _resolve_cookie_spec(spec, {"session_id": "S1"})
    assert out["value"] == "S1"


def test_resolve_cookie_spec_missing_required_raises() -> None:
    with pytest.raises(ValueError, match="缺 name"):
        _resolve_cookie_spec({"value": "x", "domain": "x.com"}, None)
    with pytest.raises(ValueError, match="缺 domain"):
        _resolve_cookie_spec({"name": "sid", "value": "x"}, None)
    with pytest.raises(ValueError, match="缺 value"):
        _resolve_cookie_spec({"name": "sid", "domain": "x.com"}, None)


def test_resolve_cookie_spec_passes_through_optional_fields() -> None:
    spec = {
        "name": "sid", "value": "v", "domain": "x.com", "path": "/",
        "expires": 9999, "httpOnly": True, "secure": True, "sameSite": "Lax",
    }
    out = _resolve_cookie_spec(spec, None)
    assert out["httpOnly"] is True
    assert out["sameSite"] == "Lax"
    assert out["expires"] == 9999


# ─── _StubAILoginRunner ──────────────────────────────────────────────


async def test_stub_ai_login_runner_reports_not_implemented() -> None:
    success, err = await _StubAILoginRunner().run_ai_login(
        bundle=None,  # type: ignore[arg-type]
        login_url="x", success_indicator="x", max_steps=1, credentials=None,
    )
    assert success is False
    assert "Task 9.4" in (err or "")


# ─── cookie_inject ───────────────────────────────────────────────────


async def test_cookie_inject_happy_path() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="cookie_inject", config={
        "cookies": [
            {"name": "sid", "value": "abc", "domain": ".foo.com", "path": "/"},
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.error is None
    bundle.context.add_cookies.assert_awaited_once()
    cookies_arg = bundle.context.add_cookies.call_args.args[0]
    assert cookies_arg[0]["name"] == "sid"
    assert cookies_arg[0]["domain"] == ".foo.com"


async def test_cookie_inject_with_value_ref_to_credentials() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="cookie_inject", config={
        "cookies": [
            {"name": "sid", "value_ref": "credentials.session_token",
             "domain": ".foo.com", "path": "/"},
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        credentials={"session_token": "REAL_TOKEN_123"},
        capture_screenshot=False,
    )
    assert result.success is True
    cookies_arg = bundle.context.add_cookies.call_args.args[0]
    assert cookies_arg[0]["value"] == "REAL_TOKEN_123"


async def test_cookie_inject_missing_credentials_returns_config_error() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="cookie_inject", config={
        "cookies": [
            {"name": "sid", "value_ref": "credentials.session_token",
             "domain": ".foo.com", "path": "/"},
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        credentials=None,
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"
    bundle.context.add_cookies.assert_not_awaited()


async def test_cookie_inject_empty_list_returns_config_error() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="cookie_inject", config={"cookies": []})
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"


async def test_cookie_inject_browser_failure_propagates_as_browser_error() -> None:
    bundle = make_mock_bundle()
    bundle.context.add_cookies = AsyncMock(side_effect=RuntimeError("boom"))
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "browser_error"
    assert "boom" in (result.error or "")


# ─── scripted_steps ───────────────────────────────────────────────────


async def test_scripted_steps_happy_path() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="scripted_steps", config={
        "steps": [
            {"action": "goto", "url": "https://foo.com/login"},
            {"action": "fill", "selector": "#user", "value": "{{credentials.username}}"},
            {"action": "fill", "selector": "#pwd", "value": "{{credentials.password}}"},
            {"action": "click", "selector": "#submit"},
            {"action": "wait_for_load_state", "state": "networkidle"},
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        credentials={"username": "alice", "password": "pwd123"},
        capture_screenshot=False,
    )
    assert result.success is True
    page = bundle._page
    page.goto.assert_awaited_with("https://foo.com/login")
    page.fill.assert_any_await("#user", "alice")
    page.fill.assert_any_await("#pwd", "pwd123")
    page.click.assert_awaited_with("#submit")
    page.wait_for_load_state.assert_awaited_with("networkidle")


async def test_scripted_steps_blocks_non_whitelist_action() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="scripted_steps", config={
        "steps": [{"action": "evaluate", "code": "alert(1)"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"
    assert "白名单" in (result.error or "")


async def test_scripted_steps_stops_on_first_failure() -> None:
    bundle = make_mock_bundle()
    bundle._page.click = AsyncMock(side_effect=RuntimeError("selector not found"))
    template = make_template(type="scripted_steps", config={
        "steps": [
            {"action": "click", "selector": "#a"},
            {"action": "click", "selector": "#b"},  # 不应被执行
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "browser_error"
    # 只调了一次 click（第二次没机会）
    assert bundle._page.click.await_count == 1


async def test_scripted_steps_sleep_bounds_enforced() -> None:
    """sleep 越界要被拦下。"""
    bundle = make_mock_bundle()
    template = make_template(type="scripted_steps", config={
        "steps": [{"action": "sleep", "ms": 60_000}],  # 超过 30s 上限
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "browser_error"


async def test_scripted_steps_empty_returns_config_error() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="scripted_steps", config={"steps": []})
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"


# ─── ai_login ────────────────────────────────────────────────────────


async def test_ai_login_with_stub_returns_not_implemented() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="ai_login", config={
        "login_url": "/login",
        "success_indicator": "退出登录",
        "max_steps": 5,
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "not_implemented"
    assert "Task 9.4" in (result.error or "")


async def test_ai_login_with_injected_runner_succeeds() -> None:
    bundle = make_mock_bundle()
    captured = {}

    class FakeRunner:
        async def run_ai_login(
            self, bundle, *, login_url, success_indicator, max_steps, credentials,
        ):
            captured["login_url"] = login_url
            captured["max_steps"] = max_steps
            captured["credentials"] = credentials
            return True, None

    template = make_template(type="ai_login", config={
        "login_url": "/auth/login",
        "success_indicator": "退出",
        "max_steps": 8,
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        credentials={"u": "x", "p": "y"},
        ai_login_runner=FakeRunner(),  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is True
    assert captured["login_url"] == "https://foo.com/auth/login"
    assert captured["max_steps"] == 8
    assert captured["credentials"] == {"u": "x", "p": "y"}


async def test_ai_login_runner_failure_marks_auth_failed() -> None:
    bundle = make_mock_bundle()

    class FailingRunner:
        async def run_ai_login(self, *args, **kwargs):
            return False, "用户名或密码错误"

    template = make_template(type="ai_login", config={
        "login_url": "/login",
        "success_indicator": "退出",
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        ai_login_runner=FailingRunner(),  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "auth_failed"
    assert "用户名或密码错误" in (result.error or "")


async def test_ai_login_missing_success_indicator_returns_config_error() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="ai_login", config={"login_url": "/login"})
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"
    assert "success_indicator" in (result.error or "")


async def test_ai_login_resolves_relative_login_url() -> None:
    bundle = make_mock_bundle()
    captured = {}

    class CaptureRunner:
        async def run_ai_login(self, bundle, *, login_url, **kwargs):
            captured["url"] = login_url
            return True, None

    template = make_template(type="ai_login", config={
        "login_url": "/auth",
        "success_indicator": "x",
    })
    await run_precondition(
        bundle, template, base_url="https://foo.com/",  # 末尾斜杠正常处理 # type: ignore[arg-type]
        ai_login_runner=CaptureRunner(),  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert captured["url"] == "https://foo.com/auth"


async def test_ai_login_keeps_absolute_login_url_intact() -> None:
    bundle = make_mock_bundle()
    captured = {}

    class CaptureRunner:
        async def run_ai_login(self, bundle, *, login_url, **kwargs):
            captured["url"] = login_url
            return True, None

    template = make_template(type="ai_login", config={
        "login_url": "https://other.example.com/login",
        "success_indicator": "x",
    })
    await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        ai_login_runner=CaptureRunner(),  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert captured["url"] == "https://other.example.com/login"


# ─── state_inject ─────────────────────────────────────────────────────


async def test_state_inject_no_file_falls_back_to_ai_login(tmp_path: Path) -> None:
    """state 文件不存在 + fallback 默认开 → 自动走 ai_login。"""
    bundle = make_mock_bundle()
    state_target = tmp_path / "state.json"  # 不存在

    class StubLoginRunner:
        async def run_ai_login(self, *args, **kwargs):
            return True, None

    template = make_template(type="state_inject", config={
        "fallback_to_ai_login": True,
        # 必须给 success_indicator，否则 ai_login fallback 会因 config_error 失败
        "login_url": "/login", "success_indicator": "退出",
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        ai_login_runner=StubLoginRunner(),  # type: ignore[arg-type]
        save_state_on_success=False,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.fell_back_to == "ai_login"
    assert result.state_was_loaded is False


async def test_state_inject_no_file_no_fallback_fails(tmp_path: Path) -> None:
    bundle = make_mock_bundle()
    state_target = tmp_path / "state.json"
    template = make_template(type="state_inject", config={
        "fallback_to_ai_login": False,
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind in ("state_stale", "config_error")


async def test_state_inject_valid_state_passes(tmp_path: Path) -> None:
    """state 文件存在 + 页面没有过期关键字 → 直接成功。"""
    state_target = tmp_path / "state.json"
    state_target.write_text("{}")
    bundle = make_mock_bundle(page_content="Welcome to dashboard 退出")

    template = make_template(type="state_inject", config={})
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_loaded is True
    assert result.state_was_stale is False
    assert result.fell_back_to is None
    bundle._page.goto.assert_awaited_with("https://foo.com")


async def test_state_inject_stale_falls_back_and_writes_new_state(
    tmp_path: Path,
) -> None:
    """state 命中过期关键字 → 删旧 state + 走 ai_login + 写新 state。"""
    state_target = tmp_path / "state.json"
    state_target.write_text("{}")
    bundle = make_mock_bundle(page_content="<html>请登录</html>")

    on_invalidated = AsyncMock()
    on_saved = AsyncMock()

    class StubRunner:
        async def run_ai_login(self, *args, **kwargs):
            return True, None

    template = make_template(type="state_inject", config={
        "fallback_to_ai_login": True,
        "login_url": "/login", "success_indicator": "退出",
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        on_state_saved=on_saved,
        on_state_invalidated=on_invalidated,
        ai_login_runner=StubRunner(),  # type: ignore[arg-type]
        save_state_on_success=True,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_loaded is True
    assert result.state_was_stale is True
    assert result.fell_back_to == "ai_login"
    assert not state_target.exists() or result.state_saved_path  # 旧文件被删，新文件由 storage_state 写
    on_invalidated.assert_awaited_once()
    on_saved.assert_awaited_once()
    bundle.context.storage_state.assert_awaited_once()


async def test_state_inject_uses_verify_url_when_provided(tmp_path: Path) -> None:
    state_target = tmp_path / "state.json"
    state_target.write_text("{}")
    bundle = make_mock_bundle(page_content="ok")
    template = make_template(type="state_inject", config={
        "verify_url": "https://foo.com/me",
    })
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        capture_screenshot=False,
    )
    assert result.success is True
    bundle._page.goto.assert_awaited_with("https://foo.com/me")


async def test_state_inject_uses_mcp_snapshot_when_available(tmp_path: Path) -> None:
    """MCP 可用时优先取 accessibility snapshot 而非 page.content()。"""
    state_target = tmp_path / "state.json"
    state_target.write_text("{}")
    bundle = make_mock_bundle(
        mcp_unavailable=False,
        mcp_snapshot="dashboard / settings / 退出登录",
        page_content="<html>请登录</html>",  # HTML 含过期关键字，但应该被 snapshot 覆盖
    )
    template = make_template(type="state_inject", config={})
    result = await run_precondition(
        bundle, template, base_url="https://foo.com",  # type: ignore[arg-type]
        state_target=state_target,
        capture_screenshot=False,
    )
    # snapshot 没有过期关键字 → 被判定为有效；HTML 中的"请登录"应被忽略
    assert result.success is True
    assert result.state_was_stale is False
    bundle.mcp_bridge.call_tool.assert_awaited_with("browser_snapshot", {})


# ─── 主入口：超时 / 异常 / 截图 / 持久化 ───────────────────────────────


async def test_run_precondition_timeout_returns_timeout_kind() -> None:
    bundle = make_mock_bundle()

    async def slow(*args, **kwargs):
        import asyncio
        await asyncio.sleep(5)

    bundle.context.add_cookies = AsyncMock(side_effect=slow)
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        per_template_timeout_seconds=0.1,
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "timeout"


async def test_run_precondition_unknown_type_marked_config_error() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="some_unknown_type")
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"


async def test_run_precondition_captures_screenshot_on_success() -> None:
    bundle = make_mock_bundle()
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=True,
    )
    assert result.success is True
    assert result.screenshot_base64 is not None
    assert len(result.screenshot_base64) > 0
    bundle._page.screenshot.assert_awaited_once()


async def test_run_precondition_screenshot_failure_does_not_break_main_flow() -> None:
    bundle = make_mock_bundle()
    bundle._page.screenshot = AsyncMock(side_effect=RuntimeError("display gone"))
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        capture_screenshot=True,
    )
    assert result.success is True  # 业务层成功
    assert result.screenshot_base64 is None
    assert any("截图失败" in line for line in result.logs)


async def test_run_precondition_persists_state_on_success(tmp_path: Path) -> None:
    """成功 + state_target + save_state_on_success=True → storage_state 被调，callback 触发。"""
    bundle = make_mock_bundle()
    state_target = tmp_path / "state.json"
    on_saved = AsyncMock()

    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        state_target=state_target,
        on_state_saved=on_saved,
        save_state_on_success=True,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_saved is True
    assert result.state_saved_path == str(state_target)
    bundle.context.storage_state.assert_awaited_once_with(path=str(state_target))
    on_saved.assert_awaited_once_with(state_target)


async def test_run_precondition_save_state_failure_does_not_flip_success(
    tmp_path: Path,
) -> None:
    """storage_state 写失败 → success 保持 True，只在 logs 里记一笔。"""
    bundle = make_mock_bundle(
        storage_state_side_effect=OSError("permission denied"),
    )
    state_target = tmp_path / "state.json"
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        state_target=state_target,
        save_state_on_success=True,
        capture_screenshot=False,
    )
    assert result.success is True  # 关键：state 写失败不抹平登录成功
    assert result.state_was_saved is False
    assert any("写入失败" in line for line in result.logs)


async def test_run_precondition_save_state_callback_failure_does_not_break(
    tmp_path: Path,
) -> None:
    """on_state_saved 回调抛错 → 主流程仍 success。"""
    bundle = make_mock_bundle()
    state_target = tmp_path / "state.json"

    async def cb(_p):
        raise RuntimeError("db connection lost")

    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        state_target=state_target,
        on_state_saved=cb,
        save_state_on_success=True,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_saved is True
    assert any("on_state_saved 回调失败" in line for line in result.logs)


async def test_run_precondition_skips_state_when_save_disabled(tmp_path: Path) -> None:
    bundle = make_mock_bundle()
    state_target = tmp_path / "state.json"
    template = make_template(type="cookie_inject", config={
        "cookies": [{"name": "sid", "value": "x", "domain": ".x.com", "path": "/"}],
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        state_target=state_target,
        save_state_on_success=False,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_saved is False
    bundle.context.storage_state.assert_not_awaited()


# ─── ALLOWED_SCRIPT_ACTIONS / dispatch 同步性 ────────────────────────


def test_allowed_actions_set_is_immutable() -> None:
    """frozenset 锁住，避免运行时被业务代码 add 进来奇怪 action。"""
    with pytest.raises(AttributeError):
        ALLOWED_SCRIPT_ACTIONS.add("evaluate")  # type: ignore[attr-defined]


async def test_each_allowed_action_has_dispatch_branch() -> None:
    """白名单内每个 action 都应该被 _execute_script_action 实际处理。

    本测试确保"加白名单 / 加分支"两件事不会脱节 —— 加了白名单忘了加分支会
    在这里炸掉，提前发现而非生产时报 RuntimeError。
    """
    bundle = make_mock_bundle()
    minimal_step = {
        "goto": {"url": "https://x.com"},
        "click": {"selector": "#a"},
        "fill": {"selector": "#a", "value": "v"},
        "press": {"selector": "#a", "key": "Enter"},
        "wait_for_selector": {"selector": "#a"},
        "wait_for_load_state": {"state": "load"},
        "select_option": {"selector": "#a", "value": "x"},
        "check": {"selector": "#a"},
        "uncheck": {"selector": "#a"},
        "sleep": {"ms": 1},
    }
    # 所有白名单 action 都覆盖到了
    assert ALLOWED_SCRIPT_ACTIONS == set(minimal_step.keys())

    for action, kwargs in minimal_step.items():
        bundle = make_mock_bundle()
        template = make_template(type="scripted_steps", config={
            "steps": [{"action": action, **kwargs}],
        })
        result = await run_precondition(
            bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
            capture_screenshot=False,
            per_template_timeout_seconds=10.0,
        )
        assert result.success is True, f"action={action} 分发失败：{result.error}"


# ─── execution_engine._default_run_preconditions ─────────────────────


@pytest.mark.asyncio
async def test_default_run_preconditions_returns_empty_when_no_templates() -> None:
    """环境没配 preconditions → 直接返回 [] 而不抛错（即便 environment 没
    定义 ``preconditions`` 属性也不应崩，借助 getattr 兜底）。"""
    from app.modules.ui_automation.execution_engine import (
        _default_run_preconditions,
    )

    env = SimpleNamespace(
        id=uuid.uuid4(), base_url="https://x.com", session_name="default",
        token_budget=10_000,
        # 故意不定义 preconditions 属性
    )
    bundle = make_mock_bundle()
    out = await _default_run_preconditions(bundle, env, llm_config_orm=None)
    assert out == []


@pytest.mark.asyncio
async def test_default_run_preconditions_runs_cookie_inject_no_llm_needed(
    tmp_path: Path,
) -> None:
    """``cookie_inject`` 不需要 LLM。即便 llm_config_orm=None，也应该正常
    跑完一条模板并返回 success=True 的 result dict。这是回归保障：旧版
    EngineDeps.run_preconditions 默认 None → 整个 preconditions 流程被
    跳过；现在改为默认实现后必须证明它不会因为"没 LLM"而错伤别的 type。
    """
    from app.modules.ui_automation.execution_engine import (
        _default_run_preconditions,
    )

    pt = make_template(
        type="cookie_inject",
        config={
            "cookies": [
                {"name": "sid", "value": "abc",
                 "domain": ".x.com", "path": "/"},
            ],
        },
        name="注入会话 cookie",
    )
    pt.enabled = True
    pt.order_index = 0
    pt.state_saved_at = None

    env = SimpleNamespace(
        id=uuid.uuid4(),
        base_url="https://x.com",
        session_name="default",
        token_budget=10_000,
        preconditions=[pt],
    )
    bundle = make_mock_bundle()
    out = await _default_run_preconditions(bundle, env, llm_config_orm=None)
    assert len(out) == 1
    assert out[0]["success"] is True
    assert out[0]["type"] == "cookie_inject"
    assert out[0]["name"] == "注入会话 cookie"


@pytest.mark.asyncio
async def test_default_run_preconditions_breaks_on_first_failure() -> None:
    """模板按 ``order_index`` 顺序跑；任一失败立刻 break，后续模板不再跑。
    这是为了避免"第一条登录失败但第二条还硬塞 cookie"这种语义错乱。"""
    from app.modules.ui_automation.execution_engine import (
        _default_run_preconditions,
    )

    pt_bad = make_template(
        type="cookie_inject",
        config={"cookies": []},  # 缺 cookies → 必失败
        name="坏的 cookie 模板",
    )
    pt_bad.enabled = True
    pt_bad.order_index = 0
    pt_bad.state_saved_at = None

    pt_good = make_template(
        type="cookie_inject",
        config={
            "cookies": [
                {"name": "x", "value": "y",
                 "domain": ".x.com", "path": "/"},
            ],
        },
        name="好的 cookie 模板",
    )
    pt_good.enabled = True
    pt_good.order_index = 1
    pt_good.state_saved_at = None

    env = SimpleNamespace(
        id=uuid.uuid4(), base_url="https://x.com", session_name="default",
        token_budget=10_000,
        preconditions=[pt_bad, pt_good],
    )
    bundle = make_mock_bundle()
    out = await _default_run_preconditions(bundle, env, llm_config_orm=None)
    assert len(out) == 1  # 只跑了第一条
    assert out[0]["success"] is False
    assert out[0]["name"] == "坏的 cookie 模板"


# ─── http_login（Task 8.2.5）─────────────────────────────────────────


def test_parse_set_cookie_first_basic() -> None:
    assert _parse_set_cookie_first("verification_code=5545; Domain=keyuanjiankang.com; Path=/") == (
        "verification_code", "5545",
    )
    assert _parse_set_cookie_first("c_token=abc123; Path=/") == ("c_token", "abc123")
    assert _parse_set_cookie_first("") == ("", "")
    assert _parse_set_cookie_first("invalidnoeq; Domain=x") == ("", "")


def test_apply_password_hash_md5() -> None:
    # 'admin' md5 → 21232f297a57a5a743894a0e4a801fc3
    assert _apply_password_hash("admin", "md5") == "21232f297a57a5a743894a0e4a801fc3"
    # 'admin' sha256 → 8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918
    assert _apply_password_hash("admin", "sha256") == (
        "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918"
    )
    assert _apply_password_hash("plain", "none") == "plain"
    assert _apply_password_hash("admin", "") == "21232f297a57a5a743894a0e4a801fc3"  # 空字符串 → md5 默认
    with pytest.raises(ValueError):
        _apply_password_hash("x", "bogus_algo")


def test_render_http_template_basic_substitution() -> None:
    assert _render_http_template(
        "${credentials.username}",
        credentials={"username": "alice"},
        captured={},
    ) == "alice"
    assert _render_http_template(
        "${captured.c_token}",
        credentials={},
        captured={"c_token": "TKN123"},
    ) == "TKN123"


def test_render_http_template_md5_transform() -> None:
    assert _render_http_template(
        "${md5:credentials.password}",
        credentials={"password": "admin"},
        captured={},
    ) == "21232f297a57a5a743894a0e4a801fc3"


def test_render_http_template_url_encode_json_for_wm_user() -> None:
    """模拟 wm_user 拼装：``url_encode(json({cn: ..., token: ...}))``。"""
    rendered = _render_http_template(
        '${url_encode_json:{"cn":"${credentials.username}","token":"${captured.c_token}"}}',
        credentials={"username": "alice"},
        captured={"c_token": "TKN"},
    )
    # 原始 JSON：{"cn":"alice","token":"TKN"}
    # url_encode 后：%7B%22cn%22%3A%22alice%22%2C%22token%22%3A%22TKN%22%7D
    assert rendered == "%7B%22cn%22%3A%22alice%22%2C%22token%22%3A%22TKN%22%7D"


def test_render_http_template_unknown_credentials_raises() -> None:
    with pytest.raises(ValueError, match="credentials.foo"):
        _render_http_template(
            "${credentials.foo}", credentials={}, captured={},
        )
    with pytest.raises(ValueError, match="captured.c_token"):
        _render_http_template(
            "${captured.c_token}", credentials={}, captured={},
        )


def test_render_http_template_passthrough_when_no_placeholder() -> None:
    assert _render_http_template("plain", credentials={}, captured={}) == "plain"


# ─── http_login：HTTP 集成（用 fake httpx.AsyncClient）────────────────


class _FakeRespHeaders:
    def __init__(self, set_cookies: list[str]) -> None:
        self._set_cookies = set_cookies

    def get_list(self, key: str) -> list[str]:
        if key.lower() == "set-cookie":
            return list(self._set_cookies)
        return []


class _FakeResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        set_cookies: list[str] | None = None,
        text: str = "",
        json_payload: dict | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = _FakeRespHeaders(set_cookies or [])
        self._json = json_payload

    def json(self) -> dict:
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeAsyncClient:
    """Queue-driven fake of httpx.AsyncClient.

    每个测试用例独立给一个 ``responses`` 列表 + 一个共享 ``calls`` 列表用于断言。
    """

    responses: list[_FakeResp] = []
    calls: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: object) -> _FakeResp:
        type(self).calls.append({"method": method, "url": url, "kwargs": kwargs})
        if not type(self).responses:
            raise AssertionError(f"no fake response queued for {method} {url}")
        return type(self).responses.pop(0)


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch):
    """patch httpx.AsyncClient with the queue-driven fake."""
    import httpx
    _FakeAsyncClient.responses = []
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


async def test_http_login_happy_path(fake_httpx) -> None:
    """两段式 HTTP 登录 → 注入 cookie → 验证成功。

    模拟 keyuanjiankang.com 真实链路：
    - GET /api/auth/verification/getCode → Set-Cookie: verification_code=5545
    - POST /api/auth/account/login       → Set-Cookie: c_token=TKN_FROM_LOGIN
    """
    fake_httpx.responses = [
        _FakeResp(set_cookies=[
            "verification_code=5545; Domain=keyuanjiankang.com; Path=/",
        ]),
        _FakeResp(set_cookies=[
            "c_token=TKN_FROM_LOGIN; Path=/",
        ]),
    ]

    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth-dashboard.keyuanjiankang.com",
        "cookie_domain": "keyuanjiankang.com",
    })

    result = await run_precondition(
        bundle, template, base_url="https://app.keyuanjiankang.com",  # type: ignore[arg-type]
        credentials={"username": "alice", "password": "secret"},
        capture_screenshot=False,
    )

    assert result.success is True, result.error
    assert result.error_kind is None

    # 两次 HTTP 调用都发出
    assert len(fake_httpx.calls) == 2
    assert fake_httpx.calls[0]["method"] == "GET"
    assert "/api/auth/verification/getCode" in fake_httpx.calls[0]["url"]
    assert fake_httpx.calls[1]["method"] == "POST"
    assert "/api/auth/account/login" in fake_httpx.calls[1]["url"]

    # POST body 含 username + md5(password) + verifyCode（来自 step1 抓的）
    login_body = fake_httpx.calls[1]["kwargs"]["json"]
    assert login_body["name"] == "alice"
    # md5("secret")
    import hashlib
    assert login_body["password"] == hashlib.md5(b"secret").hexdigest()  # noqa: S324
    assert login_body["verifyCode"] == "5545"

    # POST 时把 step1 的 cookie 一起带回去
    cookie_header = fake_httpx.calls[1]["kwargs"]["headers"]["Cookie"]
    assert "verification_code=5545" in cookie_header

    # 浏览器注入：verification_code + c_token + auto-built wm_user 都到 cookie_domain
    bundle.context.add_cookies.assert_awaited_once()
    injected = bundle.context.add_cookies.call_args.args[0]
    names = {c["name"] for c in injected}
    # 默认 auto_wm_user=true → 多一个 wm_user
    assert names == {"verification_code", "c_token", "wm_user"}
    for c in injected:
        # 默认 cookie_domain_leading_dot=true → ".keyuanjiankang.com"（跨子域生效）
        assert c["domain"] == ".keyuanjiankang.com"
        assert c["path"] == "/"
        if c["name"] == "c_token":
            assert c["value"] == "TKN_FROM_LOGIN"
        if c["name"] == "wm_user":
            # url_encode(JSON({"cn":"alice","token":"TKN_FROM_LOGIN"}))
            from urllib.parse import unquote
            import json as _json
            decoded = _json.loads(unquote(c["value"]))
            assert decoded == {"cn": "alice", "token": "TKN_FROM_LOGIN"}


async def test_http_login_password_hash_none(fake_httpx) -> None:
    """``password_hash=none`` → 明文传密码。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1234; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
        "password_hash": "none",
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "myplain"},
        capture_screenshot=False,
    )
    assert result.success is True
    assert fake_httpx.calls[1]["kwargs"]["json"]["password"] == "myplain"


async def test_http_login_extra_login_body_with_template(fake_httpx) -> None:
    """``extra_login_body`` 支持 ``${captured.X}`` 模板。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=9999; Path=/"]),
        _FakeResp(set_cookies=["c_token=T; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
        "extra_login_body": {
            "h_app_id": "127",
            "echo": "${captured.verification_code}",
        },
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    body = fake_httpx.calls[1]["kwargs"]["json"]
    assert body["h_app_id"] == "127"
    assert body["echo"] == "9999"


async def test_http_login_extra_cookies_inject_wm_user(fake_httpx) -> None:
    """``extra_cookies[].value_template`` 支持 ``${url_encode_json:...}``。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.weimiaocaishang.com",
        "cookie_domain": "weimiaocaishang.com",
        # 关掉 auto_wm_user，纯测 extra_cookies 模板能力
        "auto_wm_user": False,
        "extra_cookies": [
            {
                "name": "wm_user",
                "value_template": (
                    '${url_encode_json:{"cn":"${credentials.username}",'
                    '"token":"${captured.c_token}"}}'
                ),
                "domain": "weimiaocaishang.com",
                "path": "/",
            },
        ],
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.weimiaocaishang.com",  # type: ignore[arg-type]
        credentials={"username": "alice", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True

    injected = bundle.context.add_cookies.call_args.args[0]
    wm = next(c for c in injected if c["name"] == "wm_user")
    # url_encode({"cn":"alice","token":"TKN"}) = %7B%22cn%22%3A%22alice%22%2C%22token%22%3A%22TKN%22%7D
    assert wm["value"] == "%7B%22cn%22%3A%22alice%22%2C%22token%22%3A%22TKN%22%7D"


async def test_http_login_auto_wm_user_disabled_no_injection(fake_httpx) -> None:
    """``auto_wm_user=false`` → 不再自动拼装 wm_user，仅注入 captured 的 cookie。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
        "auto_wm_user": False,
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    injected = bundle.context.add_cookies.call_args.args[0]
    names = {c["name"] for c in injected}
    assert names == {"verification_code", "c_token"}  # 不再有 wm_user


async def test_http_login_auto_wm_user_custom_field_names(fake_httpx) -> None:
    """覆盖 ``wm_user_login_field`` / ``wm_user_token_field``——适配 fork 用 sn/sessionToken 的场景。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
        "wm_user_cookie_name": "user_session",
        "wm_user_login_field": "sn",
        "wm_user_token_field": "sessionToken",
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "alice", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    injected = bundle.context.add_cookies.call_args.args[0]
    user_session = next(c for c in injected if c["name"] == "user_session")
    from urllib.parse import unquote
    import json as _json
    decoded = _json.loads(unquote(user_session["value"]))
    assert decoded == {"sn": "alice", "sessionToken": "TKN"}


async def test_http_login_cookie_domain_leading_dot_can_be_disabled(
    fake_httpx,
) -> None:
    """``cookie_domain_leading_dot=false`` → 注入 host-only cookie（极少数场景）。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
        "cookie_domain_leading_dot": False,
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    injected = bundle.context.add_cookies.call_args.args[0]
    assert all(c["domain"] == "example.com" for c in injected)


async def test_http_login_cookie_domain_already_dotted_not_double_dotted(
    fake_httpx,
) -> None:
    """用户写 ``.example.com`` 时不应变成 ``..example.com``。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": ".example.com",  # 已经带 dot
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    injected = bundle.context.add_cookies.call_args.args[0]
    assert all(c["domain"] == ".example.com" for c in injected)


async def test_http_login_step1_no_challenge_cookie_returns_auth_failed(
    fake_httpx,
) -> None:
    """step1 没拿到 verification_code → auth_failed。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["other_cookie=x; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "auth_failed"
    assert "verification_code" in (result.error or "")
    bundle.context.add_cookies.assert_not_awaited()


async def test_http_login_step2_400_returns_auth_failed(fake_httpx) -> None:
    """step2 返回 4xx → auth_failed，附带响应体片段。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(status_code=401, text='{"code":401,"msg":"密码错误"}'),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "wrong"},
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "auth_failed"
    assert "401" in (result.error or "")
    assert "密码错误" in (result.error or "")


async def test_http_login_step2_no_token_cookie_returns_auth_failed(
    fake_httpx,
) -> None:
    """step2 200 但 Set-Cookie 里没 c_token → auth_failed。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(
            set_cookies=["unrelated=x; Path=/"],
            json_payload={"code": 0, "data": {"hint": "see body"}},
            text='{"code":0,"data":{"hint":"see body"}}',
        ),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "auth_failed"
    assert "c_token" in (result.error or "")


async def test_http_login_missing_credentials_returns_config_error(
    fake_httpx,
) -> None:
    """没传 username/password → config_error，不发任何请求。"""
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials=None,
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "config_error"
    # 关键：fake httpx 一次都不该被调用（实际上 step1 已经会跑——所以下面这个断言反过来）
    # 真实顺序：先校验 credentials → 再进 httpx。所以 0 次调用。
    assert len(fake_httpx.calls) == 0


async def test_http_login_cookie_domain_inferred_from_auth_base_url(
    fake_httpx,
) -> None:
    """没显式给 cookie_domain → 从 auth_base_url 主机自动推（auth-x.foo.com → foo.com）。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=T; Path=/"]),
    ]
    bundle = make_mock_bundle()
    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth-dashboard.keyuanjiankang.com",
        # cookie_domain 故意不设
    })
    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    injected = bundle.context.add_cookies.call_args.args[0]
    # 推断出的 cookie_domain=keyuanjiankang.com，加前导 dot 后是 .keyuanjiankang.com
    assert all(c["domain"] == ".keyuanjiankang.com" for c in injected)


async def test_http_login_with_verify_url_navigate_succeeds(fake_httpx) -> None:
    """配了 verify_url → 注入后 navigate 检查 URL/页面 是否含 success_indicator。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=T; Path=/"]),
    ]
    bundle = make_mock_bundle()
    # 让 page.url 模拟"已经成功跳到 home"
    bundle._page.url = "https://test-cq-auth-dashboard.keyuanjiankang.com/home/"

    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth-dashboard.keyuanjiankang.com",
        "cookie_domain": "keyuanjiankang.com",
        "verify_url": "https://test-cq-auth-dashboard.keyuanjiankang.com/home/",
        "success_indicator": "/home/",
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.keyuanjiankang.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True
    bundle._page.goto.assert_awaited_once()


async def test_http_login_with_verify_url_navigate_fails_returns_auth_failed(
    fake_httpx,
) -> None:
    """注入了 cookie 但 verify_url 落到登录页 → auth_failed（视为登录无效）。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=T; Path=/"]),
    ]
    bundle = make_mock_bundle(page_content="<html>请登录</html>")
    bundle._page.url = "https://auth-dashboard.keyuanjiankang.com/login"

    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth-dashboard.keyuanjiankang.com",
        "cookie_domain": "keyuanjiankang.com",
        "verify_url": "https://test-cq-auth-dashboard.keyuanjiankang.com/home/",
        "success_indicator": "/home/",
    })
    result = await run_precondition(
        bundle, template, base_url="https://app.keyuanjiankang.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is False
    assert result.error_kind == "auth_failed"
    assert "/home/" in (result.error or "")


async def test_http_login_injects_cookies_to_all_browser_contexts(fake_httpx) -> None:
    """**关键回归测试**：cookie 必须注入到 ``browser.contexts`` 上的**所有** context。

    背景：``BrowserBundle`` 用 ``chromium.launch() + browser.new_context()``，
    导致 browser 上有 2 个 context：default（playwright-mcp 通过 CDP attach 用
    的）+ 我们 SDK 创建的（``bundle.context``）。如果只注入到 ``bundle.context``，
    AI 通过 MCP ``browser_navigate`` 时根本看不到 cookie，业务后端 302
    重定向回登录页 —— 这是 http_login 看似成功但用例还是停留登录页的根因。
    """
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=TKN; Path=/"]),
    ]

    # 用 spec：browser 上有 2 个 context（模拟真实 BrowserBundle 状态）
    sdk_context = MagicMock()
    sdk_context.add_cookies = AsyncMock()
    sdk_context.pages = []
    sdk_context.new_page = AsyncMock()
    sdk_context.storage_state = AsyncMock(return_value={"cookies": []})

    default_context = MagicMock()
    default_context.add_cookies = AsyncMock()

    fake_browser = MagicMock()
    fake_browser.contexts = [default_context, sdk_context]

    bundle = SimpleNamespace(
        context=sdk_context,
        browser=fake_browser,
        mcp_bridge=None,
        mcp_unavailable=True,
        execution_id=uuid.uuid4(),
    )

    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })

    result = await run_precondition(
        bundle, template, base_url="https://app.example.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        capture_screenshot=False,
    )
    assert result.success is True

    # 两个 context 都收到了同一份 cookies
    sdk_context.add_cookies.assert_awaited_once()
    default_context.add_cookies.assert_awaited_once()
    sdk_args = sdk_context.add_cookies.call_args.args[0]
    default_args = default_context.add_cookies.call_args.args[0]
    assert sdk_args == default_args, "两个 context 的 cookie 必须完全一致"
    # 内容含 verification_code + c_token + auto wm_user，全部带 leading dot
    sdk_names = {c["name"] for c in sdk_args}
    assert sdk_names == {"verification_code", "c_token", "wm_user"}
    assert all(c["domain"] == ".example.com" for c in sdk_args)

    # logs 里应出现 "同步到 N 个 MCP/CDP context"
    log_text = "\n".join(result.logs)
    assert "MCP/CDP" in log_text
    assert "1" in log_text  # 1 个其他 context（default）


async def test_http_login_persists_state_on_success(fake_httpx, tmp_path: Path) -> None:
    """成功后会经过统一的 state 持久化分支（http_login 视为登录类）。"""
    fake_httpx.responses = [
        _FakeResp(set_cookies=["verification_code=1; Path=/"]),
        _FakeResp(set_cookies=["c_token=T; Path=/"]),
    ]
    bundle = make_mock_bundle()
    bundle.context.storage_state = AsyncMock(return_value={"cookies": []})

    template = make_template(type="http_login", config={
        "auth_base_url": "https://auth.example.com",
        "cookie_domain": "example.com",
    })

    state_target = tmp_path / "state.json"
    saved_calls: list = []

    async def _on_saved(path) -> None:
        saved_calls.append(str(path))

    result = await run_precondition(
        bundle, template, base_url="https://x.com",  # type: ignore[arg-type]
        credentials={"username": "u", "password": "p"},
        state_target=state_target,
        on_state_saved=_on_saved,
        save_state_on_success=True,
        capture_screenshot=False,
    )
    assert result.success is True
    assert result.state_was_saved is True
    assert result.state_saved_path == str(state_target)
    assert saved_calls == [str(state_target)]
    bundle.context.storage_state.assert_awaited()


async def test_precondition_types_constant_includes_http_login() -> None:
    """models.PRECONDITION_TYPES 必须包含 http_login（schema 校验同步）。"""
    from app.modules.ui_automation.models import PRECONDITION_TYPES
    assert "http_login" in PRECONDITION_TYPES
