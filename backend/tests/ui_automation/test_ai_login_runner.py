"""Task 9.4 — StepRunnerAILoginRunner 单测。

不依赖真 BrowserBundle / MCP / LLM，全部用 stub。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.modules.ui_automation.ai_login_runner import StepRunnerAILoginRunner


class _FakeStepRunner:
    """记录 run_one 入参，按预设返回结果。"""

    __test__ = False

    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    async def run_one(self, **kwargs):
        self.calls.append(kwargs)
        return self._result


def make_bundle(
    *, mcp_unavailable: bool = True, page_url: str = "",
    mcp_url: str | None = None,
) -> SimpleNamespace:
    """构造 stub bundle。
    - ``page_url`` —— SDK 视角能拿到的 URL；模拟"page 在 SDK 的 BrowserContext 里"
    - ``mcp_url`` —— MCP 视角能拿到的 URL；模拟"page 只在 MCP 自己的 context 里、
      SDK 看不到"。两者独立，覆盖 SDK / MCP 各自拿到 URL 的两条独立路径。
    """
    fake_page = SimpleNamespace(url=page_url)
    bundle = SimpleNamespace(
        execution_id=uuid.uuid4(),
        mcp_unavailable=mcp_unavailable,
        mcp_bridge=AsyncMock() if not mcp_unavailable else None,
        register_mcp_tools_for_agent=AsyncMock(return_value=[]),
        get_primary_page=lambda: fake_page if page_url else None,
        get_current_url_via_mcp=AsyncMock(return_value=mcp_url),
    )
    return bundle


@pytest.mark.asyncio
async def test_success_when_indicator_in_snapshot() -> None:
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - heading 'Welcome user' [ref=e1]",
        final_message="登录完成",
        tool_calls=[1, 2, 3],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    ok, err = await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="Welcome user",
        max_steps=5,
        credentials={"username": "admin", "password": "x"},
    )
    assert ok is True
    assert err is None

    # 验证：password 字段在传给 step 的 description 里以 secret 占位形式出现
    assert sr.calls
    desc = sr.calls[0]["step_description"]
    assert "<secret:password>" in desc
    assert "admin" in desc


@pytest.mark.asyncio
async def test_failure_when_indicator_missing() -> None:
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - link '其他页面' [ref=e1]",
        final_message="完成（但其实没找到）",
        tool_calls=[1],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    ok, err = await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="Welcome user",
        max_steps=5,
        credentials={"username": "admin"},
    )
    assert ok is False
    assert "Welcome user" in (err or "")


@pytest.mark.asyncio
async def test_success_when_indicator_matches_page_url() -> None:
    """用户最常填的就是登录后跳转 URL（完整 URL / path 片段 / host）。
    aria snapshot 里通常没完整 URL，所以必须直接拿 ``page.url`` 校验。
    这是真实碰到的 case：用户填
    ``https://cq-auth-dashboard.keyuanjiankang.com/home/`` 但 snapshot 没这串。"""
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - link '首页' [ref=e1]",  # 没有 URL
        final_message="登录完成，已跳转。",  # 也没有完整 URL
        tool_calls=[1, 2],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(
        page_url="https://cq-auth-dashboard.keyuanjiankang.com/home/",
    )
    ok, err = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://auth-dashboard.keyuanjiankang.com/login",
        success_indicator="https://cq-auth-dashboard.keyuanjiankang.com/home/",
        max_steps=5,
        credentials={"username": "admin"},
    )
    assert ok is True, f"URL 子串匹配应该让 indicator 命中，但 err={err}"


@pytest.mark.asyncio
async def test_success_when_indicator_is_url_path_fragment() -> None:
    """填 ``/home/`` 等 path 片段也应该能从 ``page.url`` 命中。"""
    fake_step_result = SimpleNamespace(
        success=True, last_snapshot_text="- main", final_message="完成",
        tool_calls=[1], error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(page_url="https://app.example.com/home/dashboard")
    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="/home/",
        max_steps=5,
        credentials={"username": "u"},
    )
    assert ok is True


@pytest.mark.asyncio
async def test_failure_message_includes_current_page_url() -> None:
    """登录失败时，错误消息里应该带上"当前 URL"，帮用户排查
    （比如停在 /login → 没真正跳转；或者跳到了 /forbidden → 权限问题）。"""
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - input '用户名'",
        final_message="登录后还在登录页",
        tool_calls=[1, 2],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(page_url="https://app.example.com/login?error=1")
    ok, err = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="/dashboard",
        max_steps=5,
        credentials=None,
    )
    assert ok is False
    assert "/login?error=1" in (err or ""), (
        f"错误消息里应当包含当前 URL 帮排查，实际：{err}"
    )


@pytest.mark.asyncio
async def test_indicator_matches_via_mcp_url_when_sdk_page_missing() -> None:
    """关键回归：SDK 拿不到 page（``get_primary_page`` 返回 None，常见！），
    必须从 MCP 兜底 URL 命中 success_indicator。

    背景：SDK 的 ``self.browser.contexts`` 与 MCP 创建的 page 经常不在同一个
    BrowserContext 里 —— SDK 视角永远空，但 MCP 自己一定看得到。这条路径不通
    会让"AI 已经登录成功了，URL 已经跳到 /home，但平台仍判定失败"的诡异现象
    永久存在。
    """
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - link '退出登录'",  # snapshot 没命中 indicator
        final_message="登录完成",
        tool_calls=[1, 2, 3],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(
        page_url="",  # SDK 视角拿不到
        mcp_url="https://auth-dashboard.keyuanjiankang.com/home/dashboard",  # MCP 兜底
    )

    ok, err = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://auth-dashboard.keyuanjiankang.com/login",
        success_indicator="/home/",  # 用户最常填的"登录后跳转 url 的路径片段"
        max_steps=5,
        credentials=None,
    )
    assert ok is True, "MCP 兜底 URL 必须能命中 success_indicator，否则 AI 登录成功也判失败"
    assert err is None
    bundle.get_current_url_via_mcp.assert_awaited()


@pytest.mark.asyncio
async def test_mcp_url_fallback_skipped_when_sdk_page_already_has_url() -> None:
    """SDK 已经能拿到 URL 时不应再去调 MCP（多一次 RPC 浪费 ~50ms）。"""
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="",
        final_message="ok",
        tool_calls=[1],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(
        page_url="https://app.example.com/home",  # SDK 已经有 URL
        mcp_url="https://app.example.com/home",  # 即便 MCP 也能拿到，不该被 await
    )

    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="/home",
        max_steps=5,
        credentials=None,
    )
    assert ok is True
    bundle.get_current_url_via_mcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_indicator_does_not_match_navigate_fallback_url() -> None:
    """关键回归：success_indicator 不能从 ``AI 最后一次 navigate 调用的 url``
    fallback 命中 —— 否则只要 AI 调过 ``browser_navigate(login_url)``，indicator
    若是 host / 路径子串就会被误判通过（实际登录可能根本没跑完）。

    之前一个版本把 navigate fallback URL 也写进 page_url 同一个字符串里，indicator
    ``in page_url`` 会误命中。这条用例守住"fallback URL 只用于错误消息显示，不参与
    indicator 匹配判定"。
    """
    # AI 调过 navigate 但 page 还没建好（比如 navigate 超时）
    fake_nav_record = SimpleNamespace(
        name="exec__browser_navigate",
        raw_name="browser_navigate",
        arguments={"url": "https://auth-dashboard.keyuanjiankang.com/login"},
        result={"is_error": True, "content": "navigation timeout"},
    )
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="",
        final_message="导航超时",
        tool_calls=[fake_nav_record],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle(page_url="")  # 没拿到 page

    # indicator 是登录后跳转的 host/path 片段，**也**出现在 navigate 的 login_url 里
    # （host 部分一致），但实际页面没到那 → 必须判失败
    ok, err = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://auth-dashboard.keyuanjiankang.com/login",
        success_indicator="auth-dashboard.keyuanjiankang.com",
        max_steps=5,
        credentials=None,
    )
    assert ok is False, (
        "fallback navigate URL 不能用来匹配 success_indicator，否则 AI 还没真正跳转"
        "也会被误判通过"
    )
    # 但错误消息里应该带 fallback URL 的提示，方便排查
    assert "auth-dashboard.keyuanjiankang.com/login" in (err or "")
    assert "AI 最后一次 navigate" in (err or "")


@pytest.mark.asyncio
async def test_indicator_check_tolerates_page_lookup_exception() -> None:
    """``bundle.get_primary_page()`` 抛错时（bundle 已 close / page detach），
    URL 拿不到不能让整个判定挂掉，应该静默退到 snapshot/final 兜底。"""
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- heading 'Welcome' [ref=e1]",  # snapshot 命中
        final_message="ok",
        tool_calls=[1],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)

    def _broken_page():
        raise RuntimeError("bundle already closed")

    bundle = SimpleNamespace(
        execution_id=uuid.uuid4(),
        mcp_unavailable=True,
        mcp_bridge=None,
        register_mcp_tools_for_agent=AsyncMock(return_value=[]),
        get_primary_page=_broken_page,
    )
    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials=None,
    )
    assert ok is True, "snapshot 已命中，page 拿不到不应该影响结果"


@pytest.mark.asyncio
async def test_failure_when_step_runner_reports_error() -> None:
    fake_step_result = SimpleNamespace(
        success=False,
        last_snapshot_text=None,
        final_message="",
        tool_calls=[],
        error="预算耗尽",
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    ok, err = await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="https://app.example.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials=None,
    )
    assert ok is False
    assert "预算耗尽" in (err or "")


@pytest.mark.asyncio
async def test_max_steps_overrides_step_runner_max_iterations() -> None:
    """回归保障：模板配的 ``max_steps`` 应该真把 ``StepRunner.max_iterations``
    抬上去（之前只塞到 prompt 里），否则 5 步后被截断、AI 还没填完密码就
    被强行结束 → 用户看到的现象就是"60s 内一直转，最后超时"。
    """
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - heading 'Welcome' [ref=e1]",
        final_message="ok",
        tool_calls=[],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    # 模拟 StepRunner 默认上限只有 5
    sr.max_iterations = 5  # type: ignore[attr-defined]

    runner = StepRunnerAILoginRunner(step_runner=sr)  # type: ignore[arg-type]

    captured: dict[str, int] = {}

    async def _spy_run_one(**kwargs):
        # run_one 调用瞬间记下当前 step_runner.max_iterations，验证拉高生效
        captured["max_iter_during_run"] = sr.max_iterations
        return fake_step_result

    sr.run_one = _spy_run_one  # type: ignore[method-assign]

    ok, _ = await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=10,
        credentials={"username": "u"},
    )
    assert ok is True
    # 模板要求 10 步 → 临时拉到 10（高于默认 5）
    assert captured["max_iter_during_run"] == 10
    # finally 还原回原始 5，避免下次复用 runner 时残留
    assert sr.max_iterations == 5


@pytest.mark.asyncio
async def test_step_runner_exception_returns_failure_not_raise() -> None:
    class Boom:
        async def run_one(self, **_):
            raise RuntimeError("network down")

    runner = StepRunnerAILoginRunner(step_runner=Boom())  # type: ignore[arg-type]
    ok, err = await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="x", success_indicator="x", max_steps=1, credentials=None,
    )
    assert ok is False
    assert "network down" in (err or "")


# ─── build_ai_login_runner 工厂 ─────────────────────────────────────────


def test_build_ai_login_runner_returns_none_when_no_llm_config() -> None:
    """``build_ai_login_runner(llm_config_orm=None)`` 必须返回 None，让
    caller 能优雅降级到 ``_StubAILoginRunner``——避免在未配置 LLM 的全新
    部署里直接 500。"""
    from app.modules.ui_automation.ai_login_runner import build_ai_login_runner

    env = SimpleNamespace(token_budget=10000, allowed_hosts=["*"])
    runner = build_ai_login_runner(
        llm_config_orm=None,
        environment=env,
        budget_limit=10_000,
    )
    assert runner is None


def test_build_ai_login_runner_returns_runner_when_llm_config_present(monkeypatch) -> None:
    """有 LLM 配置 → 返回真实的 ``StepRunnerAILoginRunner`` 实例。"""
    from app.modules.ui_automation import ai_login_runner as mod

    fake_orm = SimpleNamespace(
        provider="openai",
        model="gpt-4o-mini",
        api_key_encrypted=None,
        base_url="https://example.com/v1",
        temperature=0.0,
        max_tokens=1024,
    )
    env = SimpleNamespace(token_budget=20000, allowed_hosts=["*"])
    runner = mod.build_ai_login_runner(
        llm_config_orm=fake_orm,
        environment=env,
        budget_limit=20_000,
    )
    assert runner is not None
    assert isinstance(runner, StepRunnerAILoginRunner)
    # budget 限制按 budget_limit 设置
    assert runner.step_runner.budget.limit == 20_000


def test_build_ai_login_runner_falls_back_to_env_token_budget() -> None:
    """``budget_limit=None`` → 退回到 ``environment.token_budget``。"""
    from app.modules.ui_automation.ai_login_runner import build_ai_login_runner

    fake_orm = SimpleNamespace(
        provider="openai", model="x",
        api_key_encrypted=None, base_url=None,
        temperature=0.0, max_tokens=512,
    )
    env = SimpleNamespace(token_budget=33_000, allowed_hosts=["*"])
    runner = build_ai_login_runner(
        llm_config_orm=fake_orm, environment=env, budget_limit=None,
    )
    assert runner is not None
    assert runner.step_runner.budget.limit == 33_000


# ─── 验证码工作流回归 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_captcha_tool_is_registered_and_passed_to_step_runner() -> None:
    """run_ai_login 必须把 ``platform_solve_captcha`` 同时：
    (1) 注册到 ``agent_tools.TOOL_REGISTRY``，让 step_runner 真能调；
    (2) 把对应 OpenAI schema 加到传给 step_runner 的 ``mcp_tool_specs`` 里，
        让 LLM 看得到这个工具。
    没这两步 AI 看不到验证码识别能力，遇到 captcha 必然死循环。
    """
    from app.modules.llm import agent_tools

    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- main\n  - heading 'Welcome' [ref=e1]",
        final_message="ok",
        tool_calls=[],
        error=None,
    )

    captured: dict[str, list] = {"specs": []}

    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle()
    expected_tool_name = f"{bundle.execution_id}__platform_solve_captcha"

    # run_one 调用瞬间检查 TOOL_REGISTRY 里是否真有 captcha tool（assert 在
    # finally unregister 之前执行——这是验证"注册时机"的关键）
    async def _spy_run_one(**kwargs):
        captured["specs"] = list(kwargs.get("mcp_tool_specs") or [])
        captured["registered_during_run"] = expected_tool_name in agent_tools.TOOL_REGISTRY
        return fake_step_result

    sr.run_one = _spy_run_one  # type: ignore[method-assign]

    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials={"username": "u"},
    )
    assert ok is True

    # 1) tool 在 run_one 期间已经注册
    assert captured["registered_during_run"] is True

    # 2) schema 出现在传给 step_runner 的 specs 里，function.name 就是 namespaced 名字
    captcha_specs = [
        s for s in captured["specs"]
        if isinstance(s, dict)
        and s.get("function", {}).get("name") == expected_tool_name
    ]
    assert len(captcha_specs) == 1, "captcha schema 应只注入 1 份"

    # 3) finally 清理：tool 必须被反注册
    assert expected_tool_name not in agent_tools.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_inline_platform_get_secret_tool_is_registered() -> None:
    """⭐ 关键回归：``platform_get_secret`` 必须在 ai_login 试跑时注册。

    历史 bug：完整版 ``platform_get_secret`` 由 ``register_data_tools`` 在
    **真实用例执行**时基于 ``TestDataResolver`` 注册——但前置步骤试跑里
    **根本没有 TestDataResolver**，导致 prompt 让 AI 调 ``platform_get_secret``
    拿密码时 tool 不在 ``TOOL_REGISTRY`` / 不在 LLM tools list 里，AI 直接
    返回"缺少 platform_get_secret 工具无法获取密码"，登录失败。

    本测试守住这条契约：
    1. ``run_ai_login`` 期间 ``<ns>__platform_get_secret`` 在 TOOL_REGISTRY
    2. schema 进入传给 step_runner 的 mcp_tool_specs
    3. 调它时返回 plaintext + secret 脱敏标志位
    4. finally 后清干净
    """
    from app.modules.llm import agent_tools

    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="- heading 'Welcome' [ref=e1]",
        final_message="ok",
        tool_calls=[],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    bundle = make_bundle()
    secret_tool = f"{bundle.execution_id}__platform_get_secret"

    captured: dict = {}

    async def _spy_run_one(**kwargs):
        captured["specs"] = list(kwargs.get("mcp_tool_specs") or [])
        captured["registered"] = secret_tool in agent_tools.TOOL_REGISTRY
        # 在 step_runner 期间真调一次 secret tool 验证返回值
        if captured["registered"]:
            executor = agent_tools.TOOL_REGISTRY[secret_tool]
            captured["pwd_result"] = await executor({"key": "password"})
            captured["user_result"] = await executor({"key": "username"})
            captured["miss_result"] = await executor({"key": "nonexistent"})
        return fake_step_result

    sr.run_one = _spy_run_one  # type: ignore[method-assign]

    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials={"username": "alice", "password": "p@ss"},
    )
    assert ok is True

    # (1) 注册时机 - run_one 跑的时候必须已经注册
    assert captured["registered"] is True, (
        "platform_get_secret 必须在调 step_runner.run_one 之前注册到 TOOL_REGISTRY"
    )

    # (2) schema 进入 specs - LLM 才能看到这个工具
    secret_specs = [
        s for s in captured["specs"]
        if isinstance(s, dict)
        and s.get("function", {}).get("name") == secret_tool
    ]
    assert len(secret_specs) == 1, "secret schema 应只注入 1 份"

    # (3) 返回值正确 - 密码返回明文 + 脱敏标志
    pwd = captured["pwd_result"]
    assert pwd["key"] == "password"
    assert pwd["value"] == "p@ss"
    assert pwd["_test_data_secret_used"] is True, (
        "敏感字段必须打 _test_data_secret_used 标志，让 redact_tool_result_for_reasoning "
        "自动从落库 reasoning 里脱敏"
    )

    # (3b) 非敏感字段（username）不打脱敏标志
    user = captured["user_result"]
    assert user["value"] == "alice"
    assert "_test_data_secret_used" not in user

    # (3c) 不存在的 key 给清晰错误
    miss = captured["miss_result"]
    assert "error" in miss
    assert "not found" in miss["error"]

    # (4) finally 清理
    assert secret_tool not in agent_tools.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_inline_secret_tool_unregistered_when_step_runner_raises() -> None:
    """异常路径下 secret tool 也必须清干净（防止后续执行撞残留）。"""
    from app.modules.llm import agent_tools

    class _BoomRunner:
        max_iterations = 5

        async def run_one(self, **_):
            raise RuntimeError("crash")

    runner = StepRunnerAILoginRunner(step_runner=_BoomRunner())  # type: ignore[arg-type]
    bundle = make_bundle()
    secret_tool = f"{bundle.execution_id}__platform_get_secret"

    ok, _ = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials={"password": "secret123"},
    )
    assert ok is False
    assert secret_tool not in agent_tools.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_default_captcha_pattern_targets_4_digit_numeric() -> None:
    """⭐ 国产后台最常见的 4 位数字 + 干扰验证码必须是默认目标。

    这条测试盯死 ``_DEFAULT_CAPTCHA_PATTERN``：上一版默认是 ``^[A-Za-z0-9]{2,8}$``，
    范围太宽，OCR 串位（比如默认模型把 4 位数字识成 5 个字符）也会被验证通过，
    捷径塞进输入框 → 服务端拒绝。改成 ``^\\d{4}$`` 后，OCR 出错值会被 validate
    拦下来重试。
    """
    import re as _re

    from app.modules.ui_automation.ai_login_runner import _DEFAULT_CAPTCHA_PATTERN

    assert _DEFAULT_CAPTCHA_PATTERN == r"^\d{4}$"
    # 行为校验：只接受 4 位数字
    assert _re.fullmatch(_DEFAULT_CAPTCHA_PATTERN, "1234")
    assert _re.fullmatch(_DEFAULT_CAPTCHA_PATTERN, "0007")
    # 拒绝 3 位 / 5 位 / 含字母
    assert not _re.fullmatch(_DEFAULT_CAPTCHA_PATTERN, "123")
    assert not _re.fullmatch(_DEFAULT_CAPTCHA_PATTERN, "12345")
    assert not _re.fullmatch(_DEFAULT_CAPTCHA_PATTERN, "12ab")


@pytest.mark.asyncio
async def test_captcha_tool_unregistered_even_when_step_runner_raises() -> None:
    """``finally`` 清理必须 robust——StepRunner 抛错也要清干净，否则后续执行
    遇到同 execution_id（或 namespace 复用）会撞到残留 tool。"""
    from app.modules.llm import agent_tools

    class _BoomRunner:
        max_iterations = 5

        async def run_one(self, **_):
            raise RuntimeError("simulated crash")

    runner = StepRunnerAILoginRunner(step_runner=_BoomRunner())  # type: ignore[arg-type]
    bundle = make_bundle()
    expected_tool_name = f"{bundle.execution_id}__platform_solve_captcha"

    ok, err = await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="Welcome",
        max_steps=5,
        credentials=None,
    )
    assert ok is False
    assert "simulated crash" in (err or "")
    # 关键：异常路径下 captcha tool 也已清理
    assert expected_tool_name not in agent_tools.TOOL_REGISTRY


@pytest.mark.asyncio
async def test_captcha_workflow_appears_in_step_description() -> None:
    """登录 prompt 必须包含验证码工作流的关键引导词，否则 AI 可能仍然
    走"截图肉眼看"的老路，识别不出验证码。"""
    fake_step_result = SimpleNamespace(
        success=True,
        last_snapshot_text="ok",
        final_message="登录完成",
        tool_calls=[],
        error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    runner = StepRunnerAILoginRunner(step_runner=sr)
    await runner.run_ai_login(
        make_bundle(),  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="ok",
        max_steps=5,
        credentials={"username": "u"},
    )
    desc = sr.calls[0]["step_description"]
    # 关键提示词必须出现，避免被改文案时无意中删掉验证码引导
    assert "platform_solve_captcha" in desc
    assert "ddddocr" in desc
    assert "严禁靠肉眼看图" in desc


@pytest.mark.asyncio
async def test_captcha_namespace_falls_back_when_bundle_has_no_execution_id() -> None:
    """``bundle.execution_id`` 缺席时退回到 ``step_runner.execution_id``，
    都没有则用 ``"login"`` 兜底。任何情况下 schema function.name 都得是合法
    的 ``<ns>__platform_solve_captcha``，否则 LLM 看不到这个 tool。"""
    fake_step_result = SimpleNamespace(
        success=True, last_snapshot_text="x", final_message="x",
        tool_calls=[], error=None,
    )
    sr = _FakeStepRunner(fake_step_result)
    sr.execution_id = "exec-from-runner"  # type: ignore[attr-defined]

    captured = {}

    async def _spy(**kw):
        captured["specs"] = list(kw.get("mcp_tool_specs") or [])
        return fake_step_result

    sr.run_one = _spy  # type: ignore[method-assign]

    runner = StepRunnerAILoginRunner(step_runner=sr)

    # bundle 没有 execution_id —— ``getattr(bundle, "execution_id", None) is None``
    bundle = SimpleNamespace(
        execution_id=None,
        mcp_unavailable=True,
        mcp_bridge=None,
        register_mcp_tools_for_agent=AsyncMock(return_value=[]),
    )

    await runner.run_ai_login(
        bundle,  # type: ignore[arg-type]
        login_url="https://x.com/login",
        success_indicator="ok",
        max_steps=5,
        credentials=None,
    )

    captcha_names = [
        s["function"]["name"]
        for s in captured["specs"]
        if s.get("function", {}).get("name", "").endswith("__platform_solve_captcha")
    ]
    assert captcha_names == ["exec-from-runner__platform_solve_captcha"]
