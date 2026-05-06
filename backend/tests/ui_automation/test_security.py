"""Task 7.3 验证：SecurityGuard + TokenBudget。

覆盖文档（PHASE2_DESIGN §3.3.4 + §3.4）声明的全部安全契约：
- 工具白名单拦截（含 browser_evaluate 默认禁 / 显式开启放行）
- URL 域名校验（精确 / 通配符 / 端口 / scheme 白名单）
- Token 预算 80% 一次性 warning + 100% raise BudgetExceededError
- 命名空间剥离（``<execution_id>__browser_navigate`` 也能正确校验，
  以及向后兼容旧 ``:`` 分隔符）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.ui_automation.security import (
    BudgetExceededError,
    SecurityError,
    SecurityGuard,
    TokenBudget,
    _host_in_allowlist,
)


def _make_env(
    *,
    base_url: str = "https://staging.foo.com",
    allowed_hosts: list[str] | None = None,
    token_budget: int = 50_000,
    enable_browser_evaluate: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        base_url=base_url,
        allowed_hosts=allowed_hosts if allowed_hosts is not None else ["staging.foo.com"],
        token_budget=token_budget,
        enable_browser_evaluate=enable_browser_evaluate,
    )


# ─── 工具白名单 ────────────────────────────────────────────────────────


def test_check_passes_for_allowed_tool() -> None:
    env = _make_env()
    guard = SecurityGuard(environment=env, budget=TokenBudget(limit=10_000))
    # navigate 到合法域名
    guard.check("browser_navigate", {"url": "https://staging.foo.com/login"})
    # 其他白名单工具
    for name in ("browser_click", "browser_type", "browser_snapshot",
                 "browser_screenshot", "browser_take_screenshot", "browser_check", "browser_press_key",
                 "browser_hover", "browser_wait_for"):
        guard.check(name, {"any": "thing"})


def test_check_rejects_unknown_tool() -> None:
    guard = SecurityGuard(environment=_make_env(), budget=TokenBudget(limit=10_000))
    with pytest.raises(SecurityError) as ei:
        guard.check("browser_send_email", {"to": "x"})
    assert "白名单" in str(ei.value)


def test_browser_evaluate_blocked_by_default() -> None:
    guard = SecurityGuard(environment=_make_env(), budget=TokenBudget(limit=10_000))
    with pytest.raises(SecurityError) as ei:
        guard.check("browser_evaluate", {"expression": "alert(1)"})
    assert "enable_browser_evaluate" in str(ei.value)


def test_browser_evaluate_allowed_when_enabled() -> None:
    env = _make_env(enable_browser_evaluate=True)
    guard = SecurityGuard(environment=env, budget=TokenBudget(limit=10_000))
    guard.check("browser_evaluate", {"expression": "1+1"})


def test_namespaced_tool_is_stripped_for_check() -> None:
    """Task 7.2 注册的工具带 ``<execution_id>__`` 前缀；guard 必须先剥再判白名单。

    分隔符是 ``__`` —— OpenAI tool name 规范要求 ``^[a-zA-Z0-9_-]+$``，老 ``:``
    分隔符在严格 provider 上会 400。同时验证 fallback：旧记录里的 ``:`` 前缀
    也得能被正确剥离（不然历史 tool_call 显示会 broken）。
    """
    guard = SecurityGuard(environment=_make_env(), budget=TokenBudget(limit=10_000))
    # 主路径：``__`` 分隔符
    guard.check("11111111-2222-3333-4444-555555555555__browser_click", {})
    with pytest.raises(SecurityError):
        guard.check("11111111-2222-3333-4444-555555555555__browser_eval", {})
    # 向后兼容：旧 ``:`` 分隔符仍能剥
    guard.check("11111111-2222-3333-4444-555555555555:browser_click", {})
    with pytest.raises(SecurityError):
        guard.check("11111111-2222-3333-4444-555555555555:browser_eval", {})


# ─── URL 域名校验 ─────────────────────────────────────────────────────


def test_navigate_blocks_off_domain() -> None:
    env = _make_env(allowed_hosts=["staging.foo.com"])
    guard = SecurityGuard(environment=env, budget=TokenBudget(limit=10_000))
    with pytest.raises(SecurityError) as ei:
        guard.check("browser_navigate", {"url": "https://attacker.example/steal"})
    assert "allowed_hosts" in str(ei.value)


def test_navigate_blocks_missing_url() -> None:
    guard = SecurityGuard(environment=_make_env(), budget=TokenBudget(limit=10_000))
    with pytest.raises(SecurityError) as ei:
        guard.check("browser_navigate", {})
    assert "url 参数" in str(ei.value)


def test_host_allowlist_exact_match() -> None:
    assert _host_in_allowlist("https://foo.com/x", ["foo.com"])
    assert not _host_in_allowlist("https://bar.com/x", ["foo.com"])


def test_host_allowlist_wildcard_subdomain() -> None:
    assert _host_in_allowlist("https://a.foo.com/x", ["*.foo.com"])
    assert _host_in_allowlist("https://b.x.foo.com/x", ["*.foo.com"])
    # 通配符不命中裸根
    assert not _host_in_allowlist("https://foo.com/x", ["*.foo.com"])
    # 显式加裸根条目就命中
    assert _host_in_allowlist("https://foo.com/x", ["*.foo.com", "foo.com"])


def test_host_allowlist_global_wildcard_allows_anything() -> None:
    """``*`` 单条规则 → 完全开放；任意 http/https URL 都放行。"""
    assert _host_in_allowlist("https://www.baidu.com/", ["*"])
    assert _host_in_allowlist("http://10.0.0.1:8080/api", ["*"])
    assert _host_in_allowlist("https://attacker.example/steal", ["*"])
    # 与其他条目并存也照常生效
    assert _host_in_allowlist("https://random.org/", ["staging.foo.com", "*"])
    # 非 http/https scheme 仍然被拦
    assert not _host_in_allowlist("file:///etc/passwd", ["*"])
    assert not _host_in_allowlist("javascript:alert(1)", ["*"])


def test_host_allowlist_with_port() -> None:
    """allowlist 写端口就严格匹配；不写端口则忽略实际端口。"""
    # 写了端口
    assert _host_in_allowlist("http://localhost:8443/x", ["localhost:8443"])
    assert not _host_in_allowlist("http://localhost:8080/x", ["localhost:8443"])
    # 没写端口（任意端口都过）
    assert _host_in_allowlist("http://localhost:8080/x", ["localhost"])
    assert _host_in_allowlist("http://localhost:9090/x", ["localhost"])


def test_host_allowlist_rejects_non_http_scheme() -> None:
    """只允许 http / https；file:// / javascript: / chrome:// 全拒。"""
    assert not _host_in_allowlist("file:///etc/passwd", ["foo.com"])
    assert not _host_in_allowlist("javascript:alert(1)", ["foo.com"])
    assert not _host_in_allowlist("chrome://settings", ["foo.com"])


def test_host_allowlist_handles_userinfo() -> None:
    """URL 含 user@host:port 时正确剥 userinfo 再比对。"""
    assert _host_in_allowlist("https://admin@foo.com/x", ["foo.com"])
    assert not _host_in_allowlist("https://admin@attacker.example/x", ["foo.com"])


def test_host_allowlist_case_insensitive() -> None:
    assert _host_in_allowlist("https://FOO.COM/x", ["foo.com"])
    assert _host_in_allowlist("https://foo.com/x", ["FOO.COM"])


def test_host_allowlist_handles_garbage() -> None:
    assert _host_in_allowlist("not a url", ["foo.com"]) is False
    assert _host_in_allowlist("", ["foo.com"]) is False
    assert _host_in_allowlist("https://foo.com", []) is False
    assert _host_in_allowlist("https://foo.com", None) is False  # type: ignore[arg-type]


# ─── TokenBudget ──────────────────────────────────────────────────────


def test_budget_add_and_ratio() -> None:
    b = TokenBudget(limit=1000)
    b.add(300)
    assert b.consumed == 300
    assert b.ratio == 0.3
    assert not b.over_limit


def test_budget_add_ignores_negative_or_none() -> None:
    b = TokenBudget(limit=1000)
    b.add(0)
    b.add(-50)
    b.add(None)  # type: ignore[arg-type]
    assert b.consumed == 0


def test_budget_warning_fires_once_at_80_percent() -> None:
    b = TokenBudget(limit=1000)
    b.add(700)
    assert b.maybe_warning() is None  # 70% 还不到阈值
    b.add(100)
    msg = b.maybe_warning()
    assert msg is not None
    assert "tokens" in msg
    # 二次调用必须返回 None（已通知过，避免 SSE 刷屏）
    assert b.maybe_warning() is None
    # 即使继续累加，仍然不再 warning
    b.add(50)
    assert b.maybe_warning() is None


def test_guard_raises_budget_exceeded_at_100_percent() -> None:
    env = _make_env(token_budget=1000)
    b = TokenBudget(limit=env.token_budget)
    b.add(1100)
    guard = SecurityGuard(environment=env, budget=b)
    with pytest.raises(BudgetExceededError) as ei:
        guard.check("browser_click", {})
    assert "1,000" in str(ei.value) or "1000" in str(ei.value)


def test_budget_exceeded_is_subclass_of_security_error() -> None:
    """Engine 想统一捕获时只 catch SecurityError 即可。"""
    assert issubclass(BudgetExceededError, SecurityError)


def test_budget_with_zero_limit_is_immediately_over() -> None:
    """配置错误防御：limit=0 视为永远超限。"""
    env = _make_env(token_budget=0)
    b = TokenBudget(limit=env.token_budget)
    guard = SecurityGuard(environment=env, budget=b)
    with pytest.raises(BudgetExceededError):
        guard.check("browser_click", {})
