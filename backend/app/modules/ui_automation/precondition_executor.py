"""前置步骤执行器（Task 8.2）。

职责：把 ``PreconditionTemplate`` 转成 BrowserContext 上的实际操作，并管好
"State 文件复用 / 失效 / 自动覆盖"的全部边界。

上下游：
- 上游 → ``service.test_precondition``（端点 /test 用）/ Task 9.4
  ``ExecutionEngine``（执行真用例前的 setup）。
- 下游 → ``BrowserBundle.context``（Playwright SDK） + 解密后的凭据
  + 可选的 ``AILoginRunner``（Task 9.4 接入真 StepRunner）。

四种 type 的执行策略：

- state_inject    → 加载已有 storage_state 文件 → navigate(base_url) → snapshot 检测过期关键字
                   过期则丢弃 + 触发 on_state_invalidated；执行成功不写新文件
- ai_login        → 调注入的 ``AILoginRunner`` 跑 LLM 驱动的登录流程
                   成功后 storage_state 写文件 + on_state_saved
- scripted_steps  → 按白名单 action 顺序调 Playwright SDK 方法
                   成功后视为登录类，写 state（能省去重复登录）
- cookie_inject   → context.add_cookies + 解密的凭据按 value_ref 解析
                   成功后写 state（cookie 也算"已登录态"）

设计原则：
1. **本模块不直接读写 DB** — 通过 ``on_state_saved`` / ``on_state_invalidated``
   callback 让 service 层更新 ``state_saved_at`` 字段；这样单测无需 DB
   fixture，Task 9.4 ExecutionEngine 也能复用。
2. **本模块不知道环境的 session_name 命名规则** — caller 负责用
   ``state_manager.state_path_for`` 计算好 ``state_target`` 再传进来。
3. **失败容忍** — 截图获取失败 / DB callback 失败 都不让主流程崩，
   只是 logs 里记一笔。真正的"操作失败"才反映到 ``PreconditionResult.success=False``。
4. **AI 登录解耦** — 用 ``AILoginRunner`` Protocol 把 ai_login 分支与
   Task 9.4 的 StepRunner 解耦。Task 9.4 已实现 ``StepRunnerAILoginRunner``
   （见 ``ai_login_runner.py``），Engine 注入它即可替换 ``_StubAILoginRunner``。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Protocol

if TYPE_CHECKING:
    from app.modules.ui_automation.browser_bundle import BrowserBundle
    from app.modules.ui_automation.models import PreconditionTemplate

logger = logging.getLogger(__name__)


# ─── State 过期检测关键字（可在 config.stale_keywords 覆盖）────────────
# 含中英文常见登录页线索；命中任意一项即判为 state 过期。
#
# **设计取舍**：故意 *不* 收录单字"登录" / "Login" 这类太短的词 —— 它们会
# 被"退出登录"、"Login button" 等已登录后的常见 UI 文本误命中。我们保留
# 完整短语（"请登录" / "Login required" / "Session expired"），既覆盖典型
# 登出页 / token 失效页，又避免 false-positive。
#
# 用户可在 ``config.stale_keywords`` 覆盖整个列表（典型场景：业务方的过期
# 提示是"账号已失效"等定制文案）。
DEFAULT_STALE_KEYWORDS: tuple[str, ...] = (
    "请登录", "请重新登录", "未登录",
    "登录已过期", "会话已过期", "账号已失效",
    "Sign in to continue", "Please sign in", "Please log in",
    "Login required", "Session expired", "Your session has expired",
    "Authentication required", "Unauthorized",
)

# scripted_steps 允许的 action 白名单（防止用户写 "evaluate" 任意 JS）
ALLOWED_SCRIPT_ACTIONS: frozenset[str] = frozenset({
    "goto", "click", "fill", "press",
    "wait_for_selector", "wait_for_load_state",
    "select_option", "check", "uncheck",
    "sleep",
})


# ─── 数据类型 ─────────────────────────────────────────────────────────


@dataclass
class PreconditionResult:
    """单条前置步骤的执行结果。

    设计为"全字段填齐"风格而非 ``Result | Error`` 二选一，方便 SSE 把整条结果
    序列化吐给前端，也方便单测用 ``asdict()`` 直接对照。
    """

    template_id: uuid.UUID
    template_name: str
    type: str
    success: bool
    elapsed_ms: int

    error: str | None = None
    """人类可读的错误信息（中文）。"""

    error_kind: str | None = None
    """机器可读的错误类别。常见值：

    - ``config_error`` — 配置错（缺字段、type 非法、白名单外 action）
    - ``not_implemented`` — ai_login 未注入 runner / 该分支未实现
    - ``browser_error`` — Playwright 操作失败（selector 找不到、超时等）
    - ``auth_failed`` — 登录最终被判失败
    - ``state_stale`` — state_inject 检测出 state 过期
    - ``timeout`` — 超出 ``per_template_timeout_seconds``
    """

    screenshot_base64: str | None = None
    """成功 / 失败均可附；获取截图本身失败时为 None。"""

    state_was_loaded: bool = False
    """state_inject 时是否真的加载了已存在的文件。"""

    state_was_stale: bool = False
    """state_inject 后检测出过期（关键字匹配）。"""

    state_was_saved: bool = False
    """成功后是否写出了新的 storage_state 文件。"""

    state_saved_path: str | None = None

    fell_back_to: str | None = None
    """state_inject 因过期降级到了哪种 type（典型 ``ai_login``）。
    None = 没降级。"""

    logs: list[str] = field(default_factory=list)


# ─── AILoginRunner Protocol（Task 9.4 接入点）─────────────────────────


class AILoginRunner(Protocol):
    """跑 ai_login 流程的注入接口。

    Task 8.2 阶段：``service`` 层不传 → 自动 fallback 到 ``_StubAILoginRunner``，
    返回 ``success=False, error_kind="not_implemented"``，让用户/SSE 看到"功能
    待 Task 9.4 完成"的明确提示。

    Task 9.4：``ExecutionEngine`` 注入真正包装 ``StepRunner`` 的实现 → 跑一遍
    LLM 驱动的登录步骤，最大 max_steps 次，成功标志由 ``success_indicator`` 决定。
    """

    async def run_ai_login(
        self,
        bundle: "BrowserBundle",
        *,
        login_url: str,
        success_indicator: str,
        max_steps: int,
        credentials: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        """返回 ``(success, error_message)``；error_message 仅 success=False 时填。"""


class _StubAILoginRunner:
    """占位实现。Task 9.4 已经提供 ``StepRunnerAILoginRunner`` 真实实现，
    但只有当 Engine 显式注入时才会替换；service 层走 ``test-precondition``
    端点单测试跑时仍允许走这个 stub（意图：让"没有有效 LLM 配置"也能用
    其他三种 type 的前置步骤功能）。"""

    async def run_ai_login(
        self,
        bundle: "BrowserBundle",
        *,
        login_url: str,
        success_indicator: str,
        max_steps: int,
        credentials: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        return False, "ai_login 分支需要 AILoginRunner（Task 9.4 集成 StepRunner 后接入）"


# ─── Callback 类型别名 ───────────────────────────────────────────────

OnStateSaved = Callable[[Path], Awaitable[None]]
OnStateInvalidated = Callable[[], Awaitable[None]]


# ─── 主入口 ──────────────────────────────────────────────────────────


async def run_precondition(
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    *,
    base_url: str,
    state_target: Path | None = None,
    credentials: dict[str, Any] | None = None,
    on_state_saved: OnStateSaved | None = None,
    on_state_invalidated: OnStateInvalidated | None = None,
    ai_login_runner: AILoginRunner | None = None,
    capture_screenshot: bool = True,
    save_state_on_success: bool = True,
    per_template_timeout_seconds: float = 60.0,
) -> PreconditionResult:
    """执行单条 PreconditionTemplate。

    :param bundle: 已 ``open()`` 完成的 BrowserBundle；调用方负责 close。
    :param template: 待执行的前置步骤，credentials 字段是 *encrypted* 的字符串，
                     **本函数不解密**；调用方应已通过 service.reveal_credentials
                     解出明文 dict 传进 ``credentials`` 参数。
    :param base_url: 环境的 base_url；state_inject 用它做"加载后跑一下确认未过期"。
    :param state_target: storage_state 文件路径；None = 完全跳过 state 持久化（test-precondition 端点会传 None）。
    :param credentials: 解密后的凭据 dict；None = 视为该模板无凭据。
    :param on_state_saved: 写入 state 文件成功后调（service 用来更新 DB.state_saved_at）。
    :param on_state_invalidated: state_inject 检测出过期、丢弃文件后调。
    :param ai_login_runner: ai_login 分支的实际 runner；None = stub。
    :param capture_screenshot: 是否在结尾截图（成功 / 失败均生效）。
    :param save_state_on_success: False = 即便成功也不写 state（用于试跑 endpoint）。
    :param per_template_timeout_seconds: 单条模板硬超时。
    """
    started_at = time.monotonic()
    result = PreconditionResult(
        template_id=template.id,
        template_name=template.name,
        type=template.type,
        success=False,
        elapsed_ms=0,
    )

    runner: AILoginRunner = ai_login_runner or _StubAILoginRunner()

    try:
        async with asyncio.timeout(per_template_timeout_seconds):
            await _dispatch(
                bundle=bundle,
                template=template,
                result=result,
                base_url=base_url,
                state_target=state_target,
                credentials=credentials,
                on_state_saved=on_state_saved,
                on_state_invalidated=on_state_invalidated,
                ai_login_runner=runner,
                save_state_on_success=save_state_on_success,
            )
    except TimeoutError:
        result.success = False
        result.error_kind = "timeout"
        # ai_login 类型超时几乎都是因为：(a) max_steps 配少了；(b) 网络慢；
        # (c) success_indicator 没匹配上但 AI 一直不收手。给出具体调优方向，
        # 比"超时"两个字更帮 caller 排查。
        if template.type == "ai_login":
            result.error = (
                f"AI 登录超时（>{per_template_timeout_seconds:.0f}s）。"
                "可尝试：① 在试跑请求体里把 timeout_seconds 调大（最多 600）；"
                "② 检查 success_indicator 是否能在登录后页面找到；"
                "③ 适当增大 config.max_steps（默认 10）。"
            )
        else:
            result.error = f"前置步骤执行超时（>{per_template_timeout_seconds:.0f}s）"
        result.logs.append(result.error)
    except Exception as exc:  # noqa: BLE001
        # 兜底：分支函数应自己处理异常 → 走到这里说明遗漏；统一兜成 browser_error
        # 而非让端点 500，对前端更友好。
        logger.exception(
            "run_precondition unhandled error template=%s type=%s",
            template.id, template.type,
        )
        result.success = False
        result.error_kind = result.error_kind or "browser_error"
        result.error = result.error or f"{type(exc).__name__}: {exc}"
        result.logs.append(f"unhandled {type(exc).__name__}: {exc}")
    finally:
        if capture_screenshot:
            shot = await _safe_screenshot(bundle, result.logs)
            if shot is not None:
                result.screenshot_base64 = shot
        result.elapsed_ms = int((time.monotonic() - started_at) * 1000)

    return result


async def _dispatch(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    base_url: str,
    state_target: Path | None,
    credentials: dict[str, Any] | None,
    on_state_saved: OnStateSaved | None,
    on_state_invalidated: OnStateInvalidated | None,
    ai_login_runner: AILoginRunner,
    save_state_on_success: bool,
) -> None:
    """type 路由 + State 写文件的统一收口。

    分支函数只负责"操作浏览器并报告 success / error"；State 持久化的逻辑
    集中在这里，避免每个分支都重复 try/except 写文件。
    """
    t = template.type

    if t == "state_inject":
        await _run_state_inject(
            bundle=bundle, template=template, result=result, base_url=base_url,
            state_target=state_target, credentials=credentials,
            on_state_invalidated=on_state_invalidated,
            ai_login_runner=ai_login_runner,
            on_state_saved=on_state_saved,
            save_state_on_success=save_state_on_success,
        )
        # 注意：state_inject 自身**不**写 state（state 已经在了；过期降级到 ai_login
        # 时由降级分支自己写 state）—— 因此这里跳过下面的统一 save 逻辑。
        return

    if t == "ai_login":
        await _run_ai_login(
            bundle=bundle, template=template, result=result, base_url=base_url,
            credentials=credentials, runner=ai_login_runner,
        )
    elif t == "scripted_steps":
        await _run_scripted_steps(
            bundle=bundle, template=template, result=result, credentials=credentials,
        )
    elif t == "cookie_inject":
        await _run_cookie_inject(
            bundle=bundle, template=template, result=result, credentials=credentials,
        )
    elif t == "http_login":
        await _run_http_login(
            bundle=bundle, template=template, result=result,
            base_url=base_url, credentials=credentials,
        )
    else:
        result.success = False
        result.error_kind = "config_error"
        result.error = f"未知 precondition.type={t!r}"
        result.logs.append(result.error)
        return

    # 统一收口：登录类成功后写 state
    if result.success and save_state_on_success and state_target is not None:
        await _persist_state(bundle, state_target, result, on_state_saved)


# ─── 4 个 type 分支 ───────────────────────────────────────────────────


async def _run_state_inject(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    base_url: str,
    state_target: Path | None,
    credentials: dict[str, Any] | None,
    on_state_invalidated: OnStateInvalidated | None,
    ai_login_runner: AILoginRunner,
    on_state_saved: OnStateSaved | None,
    save_state_on_success: bool,
) -> None:
    """state_inject 分支。

    流程：
    1. 检查 state_target 是否存在；不存在 → 视为"还没登录过"，直接降级到
       ai_login（如果 config.fallback_to_ai_login=true）或失败
    2. （注意：BrowserBundle 已经在 open() 时通过 ``BundleOptions.storage_state_path``
       把 storage_state 加载进 context；state_inject 这里只做"验证 + 过期判定"。）
    3. navigate(base_url) → snapshot 文本
    4. 关键字命中过期 → 丢弃 state（on_state_invalidated）+ 降级到 ai_login（若启用）

    config 形状：
    - ``required: bool = False``        缺 state 时是否必须降级，否则报错
    - ``fallback_to_ai_login: bool = True``  过期 / 缺失时是否自动走 ai_login
    - ``stale_keywords: list[str] | None``  覆盖 DEFAULT_STALE_KEYWORDS
    - ``verify_url: str | None``        navigate 验证 URL；None = base_url
    """
    config = template.config or {}
    required = bool(config.get("required", False))
    fallback = bool(config.get("fallback_to_ai_login", True))
    stale_keywords = tuple(config.get("stale_keywords") or DEFAULT_STALE_KEYWORDS)
    verify_url = config.get("verify_url") or base_url

    state_exists = state_target is not None and state_target.exists()
    result.state_was_loaded = state_exists

    if not state_exists:
        result.logs.append("没有可用的 storage_state 文件")
        if fallback:
            result.logs.append("→ 降级到 ai_login")
            await _fallback_to_ai_login(
                bundle=bundle, template=template, result=result, base_url=base_url,
                credentials=credentials, runner=ai_login_runner,
                state_target=state_target, on_state_saved=on_state_saved,
                save_state_on_success=save_state_on_success,
            )
            return
        result.success = False
        result.error_kind = "config_error" if required else "state_stale"
        result.error = "state 文件不存在且未启用 fallback_to_ai_login"
        return

    # 验证 state 是否仍有效
    page = await _ensure_page(bundle)
    try:
        await page.goto(verify_url)
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error_kind = "browser_error"
        result.error = f"navigate({verify_url}) 失败：{exc}"
        result.logs.append(result.error)
        return

    try:
        snapshot_text = await _read_snapshot_text(bundle, page)
    except Exception as exc:  # noqa: BLE001
        result.logs.append(f"快照读取失败（按'已登录'乐观处理）：{exc}")
        snapshot_text = ""

    matched = _match_stale_keyword(snapshot_text, stale_keywords)
    if matched:
        result.state_was_stale = True
        result.logs.append(f"检测到过期关键字 {matched!r}")
        # 丢弃失效的 state
        if on_state_invalidated is not None:
            try:
                await on_state_invalidated()
            except Exception as exc:  # noqa: BLE001
                result.logs.append(f"on_state_invalidated 回调失败：{exc}")
        if state_target is not None and state_target.exists():
            try:
                state_target.unlink()
                result.logs.append(f"已删除过期 state 文件：{state_target.name}")
            except OSError as exc:
                result.logs.append(f"删除 state 文件失败：{exc}")

        if fallback:
            result.logs.append("→ 降级到 ai_login 重新登录")
            await _fallback_to_ai_login(
                bundle=bundle, template=template, result=result, base_url=base_url,
                credentials=credentials, runner=ai_login_runner,
                state_target=state_target, on_state_saved=on_state_saved,
                save_state_on_success=save_state_on_success,
            )
        else:
            result.success = False
            result.error_kind = "state_stale"
            result.error = f"state 已过期（命中关键字 {matched!r}），且未启用 fallback_to_ai_login"
        return

    result.success = True
    result.logs.append("state 仍有效")


async def _fallback_to_ai_login(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    base_url: str,
    credentials: dict[str, Any] | None,
    runner: AILoginRunner,
    state_target: Path | None,
    on_state_saved: OnStateSaved | None,
    save_state_on_success: bool,
) -> None:
    """state_inject 失败时把降级到 ai_login 的细节统一在这里，避免逻辑分叉。

    重要：降级成功后**立即**写新 state，避免下次执行又走一遍 ai_login。
    """
    result.fell_back_to = "ai_login"
    await _run_ai_login(
        bundle=bundle, template=template, result=result, base_url=base_url,
        credentials=credentials, runner=runner,
    )
    if result.success and save_state_on_success and state_target is not None:
        await _persist_state(bundle, state_target, result, on_state_saved)


async def _run_ai_login(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    base_url: str,
    credentials: dict[str, Any] | None,
    runner: AILoginRunner,
) -> None:
    """ai_login 分支。

    本 task 的核心动作：解析 config → 调注入的 runner.run_ai_login → 翻译结果。
    真正的 LLM 驱动登录由 Task 9.4 ``StepRunner``-backed runner 实现。
    """
    config = template.config or {}
    login_url_rel = config.get("login_url") or "/login"
    # 允许写绝对 URL，也允许 "/login" 这种相对路径（自动拼 base_url）
    if login_url_rel.startswith("http://") or login_url_rel.startswith("https://"):
        login_url = login_url_rel
    else:
        login_url = base_url.rstrip("/") + "/" + login_url_rel.lstrip("/")
    success_indicator = config.get("success_indicator") or ""
    max_steps = int(config.get("max_steps") or 10)

    if not success_indicator:
        result.success = False
        result.error_kind = "config_error"
        result.error = "ai_login 配置缺 success_indicator（成功登录后页面应出现的关键字 / selector）"
        result.logs.append(result.error)
        return

    result.logs.append(f"调用 AILoginRunner: login_url={login_url}, max_steps={max_steps}")
    success, err = await runner.run_ai_login(
        bundle,
        login_url=login_url,
        success_indicator=success_indicator,
        max_steps=max_steps,
        credentials=credentials,
    )
    if success:
        result.success = True
        result.logs.append("AI 登录成功")
    else:
        result.success = False
        # stub 没接入时给的错误用 "not_implemented" kind 让前端识别"等 9.4"，
        # 真 runner 失败用 "auth_failed"。区分手段：检测 err 里有 stub 的关键句子。
        if err and "Task 9.4" in err:
            result.error_kind = "not_implemented"
        else:
            result.error_kind = "auth_failed"
        result.error = err or "AI 登录失败（无详细原因）"
        result.logs.append(result.error)


async def _run_scripted_steps(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    credentials: dict[str, Any] | None,
) -> None:
    """scripted_steps 分支：按白名单 action 顺序调 Playwright SDK。

    config 形状：
    - ``steps: [{"action": "...", ...kwargs}, ...]``

    支持的模板替换：fill / press 的 value 字段里 ``{{credentials.xxx}}`` 会被替换为
    解密凭据的对应字段。
    """
    config = template.config or {}
    steps = config.get("steps") or []
    if not isinstance(steps, list) or not steps:
        result.success = False
        result.error_kind = "config_error"
        result.error = "scripted_steps 配置缺 steps 列表"
        result.logs.append(result.error)
        return

    page = await _ensure_page(bundle)

    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            result.success = False
            result.error_kind = "config_error"
            result.error = f"steps[{idx}] 非 dict"
            result.logs.append(result.error)
            return
        action = step.get("action")
        if action not in ALLOWED_SCRIPT_ACTIONS:
            result.success = False
            result.error_kind = "config_error"
            result.error = (
                f"steps[{idx}].action={action!r} 不在白名单 {sorted(ALLOWED_SCRIPT_ACTIONS)}"
            )
            result.logs.append(result.error)
            return

        try:
            await _execute_script_action(page, action, step, credentials)
            result.logs.append(f"steps[{idx}] {action} OK")
        except Exception as exc:  # noqa: BLE001
            result.success = False
            result.error_kind = "browser_error"
            result.error = f"steps[{idx}] {action} 失败：{exc}"
            result.logs.append(result.error)
            return

    result.success = True
    result.logs.append(f"全部 {len(steps)} 个 scripted step 执行完成")


async def _run_cookie_inject(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    credentials: dict[str, Any] | None,
) -> None:
    """cookie_inject 分支：直接 ``context.add_cookies([...])``。

    config 形状：
    - ``cookies: [{"name", "value_ref" | "value", "domain", "path", "expires"?, ...}]``

    value_ref 引用规则：
    - ``credentials.<key>`` → 从解密的 credentials dict 取 ``<key>``
    - ``literal:<value>``   → 字面量（方便临时调试）
    - 直接给 ``value`` 字段也支持（明文，不推荐写敏感信息）
    """
    config = template.config or {}
    cookie_specs = config.get("cookies") or []
    if not isinstance(cookie_specs, list) or not cookie_specs:
        result.success = False
        result.error_kind = "config_error"
        result.error = "cookie_inject 配置缺 cookies 列表"
        result.logs.append(result.error)
        return
    if bundle.context is None:
        result.success = False
        result.error_kind = "browser_error"
        result.error = "BrowserBundle.context 未初始化"
        result.logs.append(result.error)
        return

    resolved_cookies: list[dict[str, Any]] = []
    for idx, spec in enumerate(cookie_specs):
        if not isinstance(spec, dict):
            result.success = False
            result.error_kind = "config_error"
            result.error = f"cookies[{idx}] 非 dict"
            result.logs.append(result.error)
            return
        try:
            cookie = _resolve_cookie_spec(spec, credentials)
        except ValueError as exc:
            result.success = False
            result.error_kind = "config_error"
            result.error = f"cookies[{idx}] 解析失败：{exc}"
            result.logs.append(result.error)
            return
        resolved_cookies.append(cookie)

    try:
        # 走 ``_inject_cookies_to_all_contexts``：与 http_login 一样的根因——
        # MCP 通过 CDP 用的是 default context，而我们 SDK 创建的是另一个 context，
        # 必须把 cookie 同步到所有 context，否则 AI navigate 时看不到 cookie。
        await _inject_cookies_to_all_contexts(bundle, resolved_cookies)
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error_kind = "browser_error"
        result.error = f"context.add_cookies 失败：{exc}"
        result.logs.append(result.error)
        return

    result.success = True
    other = _other_contexts_count(bundle)
    if other > 0:
        result.logs.append(
            f"已注入 {len(resolved_cookies)} 个 cookie（SDK + {other} 个 MCP/CDP context）"
        )
    else:
        result.logs.append(f"已注入 {len(resolved_cookies)} 个 cookie")


# ─── http_login 分支（Task 8.2.5）─────────────────────────────────────


def _diagnose_http_transport_error(
    exc: BaseException | None,
    *,
    auth_base_url: str,
) -> str:
    """把 httpx transport 错误翻译成"含修复建议"的人类可读提示。

    动机：``ConnectTimeout``/``ConnectError`` 这类错误信息原文常常是空字符串
    或纯堆栈名，运维侧看到只会一脸懵。这里按错误类别给出"下一步该干啥"的
    动作指引，覆盖三个最高频场景：
    1. 连不上（VPN 没开 / 路由不到 / 防火墙拦） → 指引启用 vpn override；
    2. DNS 失败 → 指引检查 environment.base_url；
    3. TLS 握手失败 → 指引检查 base_url 是否拼错或证书是否有效。
    """
    import os as _os

    if exc is None:
        return ""

    name = type(exc).__name__
    msg = str(exc).strip()

    proxy_hint = ""
    has_proxy = bool(
        _os.environ.get("HTTPS_PROXY") or _os.environ.get("HTTP_PROXY")
    )
    if not has_proxy:
        proxy_hint = (
            "  · 容器侧未配置 HTTPS_PROXY，目标域看起来在公司内网（VPN 后端），"
            "macOS Docker Desktop 默认无法直连内网。"
            "  · 修复：宿主机起 HTTP 代理（mitmdump / pproxy / tinyproxy 任选），"
            "然后用 `docker compose -f docker-compose.yml -f docker-compose.vpn.yml up -d backend` "
            "重启后端容器（详见 docker-compose.vpn.yml docstring）。"
        )

    if name in ("ConnectTimeout", "PoolTimeout"):
        return (
            f"网络层连接超时（{name}{f': {msg}' if msg else ''}）—— TCP 握手 20s 内未完成。"
            f" 目标 {auth_base_url} 多半因 VPN/路由不可达。"
            f"{proxy_hint}"
        )
    if name in ("ConnectError",):
        return (
            f"网络层无法建立连接（{name}{f': {msg}' if msg else ''}）—— 本地 DNS 解析到的 IP "
            f"不可路由，或目标端口 443 被防火墙拦截。{proxy_hint}"
        )
    if name in ("ReadTimeout", "WriteTimeout"):
        return (
            f"网络层读写超时（{name}{f': {msg}' if msg else ''}）—— TCP 已建立但业务后端"
            f" 20s 内未返回 / 链路抖动。建议先在宿主机 `curl {auth_base_url}` 确认是否同样卡住。"
        )
    if name in ("RemoteProtocolError", "ProtocolError"):
        return (
            f"网络层协议错误（{name}{f': {msg}' if msg else ''}）—— 多见于代理只允许 HTTP/1.1 "
            f"或对端要求 TLS SNI。检查代理是否支持 HTTPS CONNECT，以及 auth_base_url 是否拼对协议。"
        )
    return f"{name}{f': {msg}' if msg else ''}"


async def _run_http_login(
    *,
    bundle: "BrowserBundle",
    template: "PreconditionTemplate",
    result: PreconditionResult,
    base_url: str,
    credentials: dict[str, Any] | None,
) -> None:
    """http_login 分支：纯 HTTP API 走"两段式"登录拿 cookie，再注入浏览器。

    适用场景：业务后台暴露 ``GET /auth/getCode`` + ``POST /auth/login``
    这类设计——getCode 接口通过 ``Set-Cookie`` 下发挑战 cookie（典型如
    ``verification_code=5545``，**所谓的"图形验证码值就在 cookie 里"**，图片
    只是给人眼看的），login 接口拿 username + password + 回填的 verifyCode
    换一个 token cookie（典型 ``c_token``）。这就是"很多管理后台的登录流程"
    的内部真相，根本就不需要 OCR 识图。

    比 ai_login 快 100 倍（<2s vs 60-180s），0 LLM token 消耗，0 OCR 误差，
    一次配置长期稳定。

    config 形状（全部字段都有合理默认值，必填的只有 ``auth_base_url`` /
    ``cookie_domain``，假设你的后台就是 keyuanjiankang/weimiaocaishang 风格）：

    .. code-block:: yaml

        auth_base_url: https://auth-dashboard.keyuanjiankang.com
        precode_path: /api/auth/verification/getCode    # default
        precode_method: GET                              # default
        precode_cookie_name: verification_code           # default：从 Set-Cookie 抓
        login_path: /api/auth/account/login              # default
        login_method: POST                               # default
        login_username_field: name                       # default
        login_password_field: password                   # default
        login_verify_code_field: verifyCode              # default
        password_hash: md5                               # md5 / sha256 / none
        extra_login_body: {h_app_id: 127}                # 额外字段（可选）
        login_token_cookie_name: c_token                 # default：从 Set-Cookie 抓
        cookie_domain: keyuanjiankang.com                # 注入到这个域
        cookie_path: /                                   # default
        # ── 自动拼装 wm_user（长轻/微秒/康伴系后台默认要求）──
        auto_wm_user: true                               # default；其他系统可设 false
        wm_user_cookie_name: wm_user                     # default
        wm_user_login_field: cn                          # default：cn=用户名
        wm_user_token_field: token                       # default：token=c_token
        extra_cookies: []                                # 额外的 cookie（如别的复合 cookie）
        verify_url: https://test-cq-auth-dashboard.keyuanjiankang.com/home/  # 可选
        success_indicator: /home/                        # 可选；URL 包含此子串视为成功

    模板占位（用在 ``extra_login_body`` 的字符串值 / ``extra_cookies[].value_template``）：
    - ``${credentials.X}`` —— 解密后的凭据 dict 取 X
    - ``${captured.X}`` —— 前序步骤抓的 cookie 取 X
    - ``${md5:...}`` —— 对内容 md5 (32 hex)
    - ``${url_encode:...}`` —— URL 编码
    - ``${url_encode_json:...}`` —— 先视为 JSON 字符串、再 URL 编码（用于 wm_user 这种）

    实施要点：
    1. 不带 follow_redirects（HEAD/POST 重定向会丢 Set-Cookie）；
    2. 同 host 的 Set-Cookie 跨 GET/POST 累积到 captured dict；
    3. 注入完默认所有 captured cookie 都注入到 cookie_domain（覆盖 weimiao
       那种"挑战 cookie 也要带回去"的场景）；
    4. ``verify_url`` 只是"试探一下、看 URL 没回登录页"——不依赖具体业务逻辑。
    """
    config = template.config or {}
    auth_base_url = (config.get("auth_base_url") or base_url or "").rstrip("/")
    if not auth_base_url:
        result.success = False
        result.error_kind = "config_error"
        result.error = "http_login 配置缺 auth_base_url（且 environment.base_url 也未提供）"
        result.logs.append(result.error)
        return

    cookie_domain = (config.get("cookie_domain") or "").strip()
    if not cookie_domain:
        # 从 auth_base_url 推一个出来：去 protocol、去 path、去 leading auth- 子域
        try:
            from urllib.parse import urlparse
            host = urlparse(auth_base_url).hostname or ""
            # auth-dashboard.keyuanjiankang.com → keyuanjiankang.com
            parts = host.split(".")
            cookie_domain = ".".join(parts[-2:]) if len(parts) >= 2 else host
        except Exception:  # noqa: BLE001
            cookie_domain = ""
    if not cookie_domain:
        result.success = False
        result.error_kind = "config_error"
        result.error = "http_login 配置缺 cookie_domain，且无法从 auth_base_url 推断"
        result.logs.append(result.error)
        return

    # ── HTTP client：纯 backend，不走 chromium 代理 ──
    # 但若部署了 HTTP_PROXY env，httpx 会自动走（这是 vpn override 期望的行为）
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        result.success = False
        result.error_kind = "browser_error"
        result.error = f"httpx 未安装：{exc}"
        result.logs.append(result.error)
        return

    # 提前校验 credentials —— 没用户名/密码就根本不该发起 HTTP 请求。
    creds = credentials or {}
    username = str(creds.get("username") or creds.get("name") or "")
    password = str(creds.get("password") or "")
    if not username or not password:
        result.success = False
        result.error_kind = "config_error"
        result.error = "http_login 需要在凭据里配 username + password"
        result.logs.append(result.error)
        return

    captured: dict[str, str] = {}

    common_headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "AITestPlatform/1.0 (http_login)",
    }

    # ── 步骤 1：GET 拿挑战 cookie ──────────────────────────────────────
    #
    # **网络瞬态防御（2026-05 验收 #04941fa4）**：
    # 业务后台的 ``/auth/verification/getCode`` 经常出现 DNS 抖动 / TCP RST /
    # TLS 握手瞬时失败 —— 同一配置上次成功、下次单点失败。这里加一层指数退避
    # 重试，覆盖 ``httpx.HTTPError`` 全家族（含 connect / read / pool 各类
    # transport error）。重试只针对 *transport* 失败；server 5xx 已经能拿到
    # response，不在这里重试（让用户清楚地看到业务后端在抽风）。
    precode_path = config.get("precode_path") or "/api/auth/verification/getCode"
    precode_method = (config.get("precode_method") or "GET").upper()
    precode_cookie_name = config.get("precode_cookie_name") or "verification_code"
    retry_attempts = max(1, int(config.get("http_retry_attempts") or 3))
    retry_backoff = float(config.get("http_retry_backoff") or 0.8)

    # ── 精确旁路代理（settings.UI_HTTP_LOGIN_PROXY）────────────────────
    # 仅 http_login 用；ai_login / state_inject / LLM 调用都不受影响。详见
    # ``app.config.Settings.UI_HTTP_LOGIN_PROXY`` docstring。env 优先级：
    # PreconditionTemplate.config.proxy （per-环境覆盖） > settings 全局。
    from app.config import settings as _settings
    proxy_url: str | None = (
        (config.get("proxy") or "").strip() or
        (_settings.UI_HTTP_LOGIN_PROXY or "").strip() or
        None
    )
    client_kwargs: dict[str, Any] = {
        "timeout": 20.0,
        "follow_redirects": False,
        "verify": True,
        # 默认 ``trust_env=True``，意味着进程内 HTTP_PROXY/HTTPS_PROXY env 会被
        # httpx 自动采纳。当用户**显式**指定了 UI_HTTP_LOGIN_PROXY，我们直接
        # 用它并关闭 trust_env，避免被 docker-compose.vpn.yml 的全局 HTTP_PROXY
        # 二次劫持（典型表现：proxy chain 把 CONNECT 转给一个不通的 hop）。
    }
    if proxy_url:
        # httpx 0.28+ 推荐用 ``proxy=`` 单数（旧的 ``proxies=`` dict 已 deprecated）。
        # 这里用 try/except 双兼容：1.x 老版本走 proxies，新版走 proxy。
        try:
            client_kwargs["proxy"] = proxy_url
        except Exception:  # noqa: BLE001
            client_kwargs["proxies"] = proxy_url
        client_kwargs["trust_env"] = False
        result.logs.append(f"http_login 走旁路代理：{proxy_url}")

    async with httpx.AsyncClient(**client_kwargs) as client:
        resp1 = None
        last_exc: httpx.HTTPError | None = None
        for attempt in range(1, retry_attempts + 1):
            try:
                resp1 = await client.request(
                    precode_method,
                    auth_base_url + precode_path,
                    headers=common_headers,
                )
                last_exc = None
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retry_attempts:
                    delay = retry_backoff * (2 ** (attempt - 1))
                    result.logs.append(
                        f"挑战接口第 {attempt}/{retry_attempts} 次请求失败："
                        f"{type(exc).__name__}: {exc} → {delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)
                else:
                    result.logs.append(
                        f"挑战接口第 {attempt}/{retry_attempts} 次请求失败："
                        f"{type(exc).__name__}: {exc} → 已达最大重试次数"
                    )

        if resp1 is None:
            result.success = False
            result.error_kind = "browser_error"
            diag = _diagnose_http_transport_error(last_exc, auth_base_url=auth_base_url)
            result.error = (
                f"挑战接口请求失败 ({precode_method} {auth_base_url}{precode_path})："
                f"已重试 {retry_attempts} 次仍失败。{diag}"
            )
            result.logs.append(result.error)
            return

        # 抓 Set-Cookie。httpx Cookies 自动 parse 多个 Set-Cookie，但我们要用
        # 原始 header 抓 cookie name=value，因为有些 cookie 跨 domain 多次
        # 出现（同名不同 domain），只看其中第一次出现的就够。
        for raw in resp1.headers.get_list("set-cookie"):
            name, value = _parse_set_cookie_first(raw)
            if name and name not in captured:
                captured[name] = value

        if precode_cookie_name not in captured:
            result.success = False
            result.error_kind = "auth_failed"
            result.error = (
                f"挑战接口未返回 cookie {precode_cookie_name!r}（"
                f"实际拿到：{sorted(captured.keys())}）"
            )
            result.logs.append(result.error)
            return
        result.logs.append(
            f"step1 OK：{precode_method} {precode_path} → captured "
            f"{sorted(captured.keys())}"
        )

        # ── 步骤 2：POST login ────────────────────────────────────────
        login_path = config.get("login_path") or "/api/auth/account/login"
        login_method = (config.get("login_method") or "POST").upper()
        username_field = config.get("login_username_field") or "name"
        password_field = config.get("login_password_field") or "password"
        verify_code_field = config.get("login_verify_code_field") or "verifyCode"

        password_hashed = _apply_password_hash(
            password, config.get("password_hash") or "md5",
        )

        login_body: dict[str, Any] = {
            username_field: username,
            password_field: password_hashed,
            verify_code_field: captured[precode_cookie_name],
        }
        # 额外字段（如 h_app_id 等）—— 支持 ${...} 模板
        for k, v in (config.get("extra_login_body") or {}).items():
            login_body[k] = (
                _render_http_template(v, credentials=creds, captured=captured)
                if isinstance(v, str) else v
            )

        cookie_header = "; ".join(f"{k}={v}" for k, v in captured.items())
        # 同步对登录接口加上一样的瞬态重试逻辑——挑战接口能过、登录接口因
        # TCP RST 失败的概率较低，但还是同等防御一下，避免"挑战 OK 登录炸"
        # 的尴尬。
        resp2 = None
        last_exc = None
        for attempt in range(1, retry_attempts + 1):
            try:
                resp2 = await client.request(
                    login_method,
                    auth_base_url + login_path,
                    headers={
                        **common_headers,
                        "Content-Type": "application/json;charset=UTF-8",
                        "Cookie": cookie_header,
                    },
                    json=login_body,
                )
                last_exc = None
                break
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retry_attempts:
                    delay = retry_backoff * (2 ** (attempt - 1))
                    result.logs.append(
                        f"登录接口第 {attempt}/{retry_attempts} 次请求失败："
                        f"{type(exc).__name__}: {exc} → {delay:.1f}s 后重试"
                    )
                    await asyncio.sleep(delay)

        if resp2 is None:
            result.success = False
            result.error_kind = "browser_error"
            diag = _diagnose_http_transport_error(last_exc, auth_base_url=auth_base_url)
            result.error = (
                f"登录接口请求失败 ({login_method} {auth_base_url}{login_path})："
                f"已重试 {retry_attempts} 次仍失败。{diag}"
            )
            result.logs.append(result.error)
            return

        if resp2.status_code >= 400:
            body_preview = (resp2.text or "")[:300]
            result.success = False
            result.error_kind = "auth_failed"
            result.error = (
                f"登录接口返回 {resp2.status_code}：{body_preview}"
            )
            result.logs.append(result.error)
            return

        for raw in resp2.headers.get_list("set-cookie"):
            name, value = _parse_set_cookie_first(raw)
            if name:
                captured[name] = value  # 后到的覆盖（login 的 cookie 优先级高）

        login_token_cookie_name = (
            config.get("login_token_cookie_name") or "c_token"
        )
        if login_token_cookie_name not in captured:
            # 业务 API 也可能在 JSON body 里返回 token / message —— 把那段一并写进 logs
            try:
                body_preview = resp2.json()
            except Exception:  # noqa: BLE001
                body_preview = (resp2.text or "")[:300]
            result.success = False
            result.error_kind = "auth_failed"
            result.error = (
                f"登录后未拿到 token cookie {login_token_cookie_name!r}（"
                f"实际 cookie：{sorted(captured.keys())}）。响应体片段：{body_preview!r}"
            )
            result.logs.append(result.error)
            return

        result.logs.append(
            f"step2 OK：{login_method} {login_path} → captured "
            f"{sorted(captured.keys())}"
        )

    # ── 注入 cookie 到 BrowserContext ──────────────────────────────────
    if bundle.context is None:
        result.success = False
        result.error_kind = "browser_error"
        result.error = "BrowserBundle.context 未初始化"
        result.logs.append(result.error)
        return

    cookie_path = config.get("cookie_path") or "/"

    # Playwright / Chromium：``domain`` 不带前导 dot 时是 host-only cookie，
    # 仅 ``cookie_domain`` 那个具体 host 能拿到；带 ``.`` 才会"includeSubdomains"。
    # 业务后台普遍跨多个子域（auth-dashboard.X / cq-auth-dashboard.X /
    # cq-front-changqing-kbj-dashboard.X 等），如果不加 dot，浏览器到子域时
    # 拿不到 cookie 直接被识别为未登录，重定向回登录页 —— 这是 http_login
    # "前置成功但用例进不去目标页"的常见症结。这里**自动**确保前导 dot，
    # 用户能用 ``cookie_domain_leading_dot=false`` 关掉（极少数 host-only
    # 场景）。
    cookie_domain_normalized = cookie_domain
    if config.get("cookie_domain_leading_dot", True):
        if not cookie_domain_normalized.startswith("."):
            cookie_domain_normalized = "." + cookie_domain_normalized

    cookies_to_inject: list[dict[str, Any]] = []
    for name, value in captured.items():
        cookies_to_inject.append({
            "name": name,
            "value": value,
            "domain": cookie_domain_normalized,
            "path": cookie_path,
        })

    # ── 自动拼装 wm_user（长轻/微秒/康伴系后台默认要求）────────────────
    #
    # 业务侧的 dashboard 后端实测：仅有 ``c_token`` 浏览器会被重定向回登录页。
    # 实际需要把 ``wm_user`` 这个复合 cookie 也拼上：
    #
    #     wm_user = url_encode(JSON({"cn": <username>, "token": <c_token>}))
    #
    # 这里默认开启（``auto_wm_user=true``），用户在 advanced JSON 里设
    # ``"auto_wm_user": false`` 即可关闭（比如对接的不是这套鉴权服务）。
    # 字段名 / 子键名也可覆盖（如某些 fork 用 sn 取代 cn）。
    auto_wm_user = bool(config.get("auto_wm_user", True))
    if auto_wm_user:
        token_value = captured.get(login_token_cookie_name)
        if token_value:
            wm_user_cookie_name = (
                config.get("wm_user_cookie_name") or "wm_user"
            )
            login_field = config.get("wm_user_login_field") or "cn"
            token_field = config.get("wm_user_token_field") or "token"
            import json as _json
            from urllib.parse import quote
            wm_user_payload = _json.dumps(
                {login_field: username, token_field: token_value},
                separators=(",", ":"),
                ensure_ascii=False,
            )
            cookies_to_inject.append({
                "name": wm_user_cookie_name,
                "value": quote(wm_user_payload, safe=""),
                "domain": cookie_domain_normalized,
                "path": cookie_path,
            })
            result.logs.append(
                f"自动拼装 {wm_user_cookie_name} cookie 已注入（"
                f"{login_field}={username!r}, "
                f"{token_field} 来自 {login_token_cookie_name}）"
            )
        else:
            # 没拿到 token —— 上面已经报 auth_failed 了；此分支理论上不可达
            pass

    # extra_cookies：业务额外要拼装的 cookie（典型如 wm_user）
    for spec in (config.get("extra_cookies") or []):
        if not isinstance(spec, dict):
            continue
        name = spec.get("name")
        if not name:
            continue
        if "value_template" in spec:
            value = _render_http_template(
                spec["value_template"], credentials=creds, captured=captured,
            )
        elif "value" in spec:
            value = str(spec["value"])
        else:
            continue
        cookies_to_inject.append({
            "name": name,
            "value": value,
            "domain": spec.get("domain") or cookie_domain_normalized,
            "path": spec.get("path") or cookie_path,
        })

    try:
        await _inject_cookies_to_all_contexts(bundle, cookies_to_inject)
    except Exception as exc:  # noqa: BLE001
        result.success = False
        result.error_kind = "browser_error"
        result.error = f"context.add_cookies 失败：{exc}"
        result.logs.append(result.error)
        return

    other_contexts_count = _other_contexts_count(bundle)
    if other_contexts_count > 0:
        result.logs.append(
            f"已向 {cookie_domain} 注入 {len(cookies_to_inject)} 个 cookie "
            f"（同步到 SDK + {other_contexts_count} 个 MCP/CDP 共享 context）"
        )
    else:
        result.logs.append(
            f"已向 {cookie_domain} 注入 {len(cookies_to_inject)} 个 cookie"
        )

    # ── 验证（可选）：navigate 到一个登录后页面看停在哪 ──────────
    verify_url = config.get("verify_url")
    success_indicator = config.get("success_indicator") or ""
    if verify_url:
        try:
            page = await bundle.context.new_page()
        except Exception as exc:  # noqa: BLE001
            result.logs.append(f"⚠ 跳过验证：new_page 失败 {exc}")
            result.success = True
            return
        try:
            try:
                await page.goto(verify_url, wait_until="domcontentloaded", timeout=15_000)
            except Exception as exc:  # noqa: BLE001
                result.logs.append(f"⚠ 验证 navigate 失败：{exc}（cookie 已注入，按成功处理）")
                result.success = True
                return
            current_url = getattr(page, "url", "") or ""
            result.logs.append(f"验证页面 URL：{current_url}")
            if success_indicator and success_indicator not in current_url:
                # 二次兜底：抓 page content 看看
                try:
                    content = await page.content()
                except Exception:  # noqa: BLE001
                    content = ""
                if success_indicator not in content:
                    result.success = False
                    result.error_kind = "auth_failed"
                    result.error = (
                        f"cookie 已注入但 navigate 验证失败：当前 URL={current_url} "
                        f"不包含 success_indicator={success_indicator!r}"
                    )
                    result.logs.append(result.error)
                    return
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass

    result.success = True


def _list_bundle_contexts(bundle: "BrowserBundle") -> list[Any]:
    """统一返回 bundle 当前持有的所有 BrowserContext。

    兼容三种 bundle 形态（按优先级）：

    1. **真实 BrowserBundle（persistent_context 架构）** —— 调
       ``bundle._all_contexts()``；返回的列表通常长度 1，因为整个 chromium
       只有一个共享 BrowserContext。
    2. **遗留 mock bundle（``browser.contexts`` 暴露多 context）** —— 单测里
       用 SimpleNamespace 模拟早期"launch + new_context"双 context 状态时的
       退路，遍历 ``bundle.browser.contexts``。
    3. **极简 mock（只有 ``bundle.context``）** —— 兜底返回 ``[bundle.context]``。

    保留两条 fallback 不只为兼容老测试，也是给未来 Playwright 行为变更留余
    地（万一 persistent_context 又开始挂多 context 出来）。
    """
    if bundle.context is None:
        return []
    fn = getattr(bundle, "_all_contexts", None)
    if callable(fn):
        try:
            ctxs = fn()
        except Exception:  # noqa: BLE001
            ctxs = None
        if ctxs:
            return list(ctxs)
    browser = getattr(bundle, "browser", None)
    if browser is not None:
        try:
            ctxs = list(browser.contexts)
        except Exception:  # noqa: BLE001
            ctxs = []
        if ctxs:
            if bundle.context not in ctxs:
                ctxs.append(bundle.context)
            return ctxs
    return [bundle.context]


async def _inject_cookies_to_all_contexts(
    bundle: "BrowserBundle",
    cookies: list[dict[str, Any]],
) -> None:
    """把 cookie 注入到 bundle 当前所有 BrowserContext。

    历史背景（保留为防御）：
    早期 ``BrowserBundle`` 用 ``chromium.launch() + browser.new_context()`` 启
    动，结果 chromium 内部存在**两个**独立 BrowserContext —— SDK 的 incognito
    context 与 CDP 看到的 default profile context 是分开的，cookie store 也
    分开。``self.context.add_cookies(...)`` 注入到 BC1，但 AI 通过 MCP
    （CDP attach）操作的是 BC2，**cookie 完全不参与请求** —— 业务后端检测到
    未登录立刻 302 回登录页，登录态白配了。

    现状（2026-05 修复后）：
    ``BrowserBundle`` 改用 ``launch_persistent_context``，整个 chromium 只有一
    个 BrowserContext，SDK 与 MCP 共享。``_list_bundle_contexts`` 通常返回长
    度 1 的列表，注入一次即可。但保留本函数作为**防御层**：未来如果引入多
    tab / 多 context 场景（典型如复杂 SSO 弹出窗），仍然能保证全量注入。
    """
    if bundle.context is None:
        raise RuntimeError("BrowserBundle.context 未初始化")
    for ctx in _list_bundle_contexts(bundle):
        await ctx.add_cookies(cookies)


def _other_contexts_count(bundle: "BrowserBundle") -> int:
    """bundle 当前持有的 context 总数 - 1（"除主 context 外的额外 context 数"）。

    用于 logs 里清晰地告诉用户 "cookie 同步到了 N 个 context"。
    persistent_context 模式下通常返回 0（只有主 context）。
    """
    return max(0, len(_list_bundle_contexts(bundle)) - 1)


def _parse_set_cookie_first(raw: str) -> tuple[str, str]:
    """从 Set-Cookie header 抓出第一段 ``name=value``，忽略 attributes（Domain/Path/...）。

    Set-Cookie 标准格式：``name=value; Domain=...; Path=...; Expires=...``。
    我们只关心 name + value，attributes 在注入时自己重新声明（domain 用 config
    指定的，更准确——服务端可能给 ``.x.com`` 但实际我们要注入到 ``a.x.com``）。
    """
    if not raw:
        return "", ""
    head = raw.split(";", 1)[0].strip()
    if "=" not in head:
        return "", ""
    name, _, value = head.partition("=")
    return name.strip(), value.strip()


def _apply_password_hash(plain: str, algo: str) -> str:
    """对密码做哈希。``md5`` / ``sha256`` / ``none``（不哈希）。"""
    algo = (algo or "md5").lower()
    if algo == "none" or algo == "":
        return plain
    if algo == "md5":
        import hashlib
        return hashlib.md5(plain.encode("utf-8")).hexdigest()  # noqa: S324
    if algo == "sha256":
        import hashlib
        return hashlib.sha256(plain.encode("utf-8")).hexdigest()
    raise ValueError(f"不支持的 password_hash={algo!r}（仅支持 md5 / sha256 / none）")


def _render_http_template(
    tpl: str,
    *,
    credentials: dict[str, Any],
    captured: dict[str, str],
) -> str:
    """渲染 ``${...}`` 模板。支持的占位：

    - ``${credentials.X}`` —— 取 credentials[X]
    - ``${captured.X}`` —— 取前序步骤抓的 cookie X
    - ``${md5:<inner>}`` —— 对 inner 渲染后做 md5
    - ``${url_encode:<inner>}`` —— URL 编码
    - ``${url_encode_json:<inner>}`` —— 同 url_encode（语义提示这里 inner 已经是
      合法的 JSON 文本），用于 wm_user 这种 ``url_encode(json({cn:...,token:...}))``
      的拼装。

    支持嵌套：``${url_encode_json:{"cn":"${credentials.username}"}}`` 这种内嵌占位
    会先内层后外层逐层渲染。

    实现：手写小解析器（找匹配 ``}``），比 regex 更可靠地处理 inner 含 brace 的情况。
    """
    if not isinstance(tpl, str):
        return str(tpl)
    if "${" not in tpl:
        return tpl

    out_parts: list[str] = []
    i = 0
    n = len(tpl)
    while i < n:
        if tpl[i:i + 2] == "${":
            # 找匹配的关闭 ``}``，所有 ``{`` / ``}`` 都计入栈。
            # 这样既支持嵌套 ``${...}``（``${`` 自带一个 ``{``），又能让 inner
            # 含 JSON 风格的 ``{ "key": "..." }`` 自然平衡。
            depth = 1  # 已经吃了开 ``{``
            j = i + 2
            while j < n and depth > 0:
                ch = tpl[j]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0:
                # 未闭合：原样输出，避免抛错让用户难定位
                out_parts.append(tpl[i:])
                break
            inner_raw = tpl[i + 2:j]
            out_parts.append(_resolve_template_expr(
                inner_raw, credentials=credentials, captured=captured,
            ))
            i = j + 1
        else:
            out_parts.append(tpl[i])
            i += 1
    return "".join(out_parts)


_KNOWN_TRANSFORMS = {"md5", "sha256", "url_encode", "url_encode_json"}


def _resolve_template_expr(
    expr: str,
    *,
    credentials: dict[str, Any],
    captured: dict[str, str],
) -> str:
    """解析单个 ``${...}`` 占位的内容（外层 ``${}`` 已剥掉）。

    递归顺序：
    1. 先把 inner 里的嵌套占位 ``${...}`` 渲染掉
    2. 再看头部是否是 ``transform:`` —— 是则递归解析 rest 后做变换
    3. 否则按 dotted-path 取值（``credentials.X`` / ``captured.X``）
    4. 否则视为字面量
    """
    if "${" in expr:
        expr = _render_http_template(expr, credentials=credentials, captured=captured)

    # 转换前缀
    if ":" in expr:
        head, _, rest = expr.partition(":")
        head = head.strip()
        if head in _KNOWN_TRANSFORMS:
            # rest 自身可能还是 dotted-path；递归一次
            inner = _resolve_template_expr(
                rest, credentials=credentials, captured=captured,
            )
            if head == "md5":
                import hashlib
                return hashlib.md5(inner.encode("utf-8")).hexdigest()  # noqa: S324
            if head == "sha256":
                import hashlib
                return hashlib.sha256(inner.encode("utf-8")).hexdigest()
            if head in ("url_encode", "url_encode_json"):
                from urllib.parse import quote
                return quote(inner, safe="")
        # 不是已知 transform：往下当 path / 字面量

    if expr.startswith("credentials."):
        key = expr[len("credentials."):]
        if key not in credentials:
            raise ValueError(f"模板引用 credentials.{key} 未配置")
        return str(credentials[key])
    if expr.startswith("captured."):
        key = expr[len("captured."):]
        if key not in captured:
            raise ValueError(f"模板引用 captured.{key}（前序步骤未抓到）")
        return captured[key]

    return expr


# ─── helpers：脚本动作执行 ────────────────────────────────────────────


async def _execute_script_action(
    page: Any,
    action: str,
    step: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> None:
    """白名单 action → Playwright Page 方法分发。

    注意所有 await 调用都直接对接 Playwright API；用 mock Page 测试时只需要
    mock 这些方法签名。
    """
    if action == "goto":
        url = _required(step, "url")
        await page.goto(url)
    elif action == "click":
        selector = _required(step, "selector")
        await page.click(selector)
    elif action == "fill":
        selector = _required(step, "selector")
        value = _interpolate(_required(step, "value"), credentials)
        await page.fill(selector, value)
    elif action == "press":
        selector = _required(step, "selector")
        key = _required(step, "key")
        await page.press(selector, key)
    elif action == "wait_for_selector":
        selector = _required(step, "selector")
        timeout = step.get("timeout_ms")
        if timeout is not None:
            await page.wait_for_selector(selector, timeout=int(timeout))
        else:
            await page.wait_for_selector(selector)
    elif action == "wait_for_load_state":
        state = step.get("state") or "load"
        if state not in ("load", "domcontentloaded", "networkidle"):
            raise ValueError(f"非法 wait_for_load_state.state={state!r}")
        await page.wait_for_load_state(state)
    elif action == "select_option":
        selector = _required(step, "selector")
        value = step.get("value")
        await page.select_option(selector, value)
    elif action == "check":
        selector = _required(step, "selector")
        await page.check(selector)
    elif action == "uncheck":
        selector = _required(step, "selector")
        await page.uncheck(selector)
    elif action == "sleep":
        ms = int(_required(step, "ms"))
        if ms < 0 or ms > 30_000:
            raise ValueError(f"sleep ms={ms} 越界（0..30000）")
        await asyncio.sleep(ms / 1000)
    else:
        # 落到这里说明白名单和分发不一致 —— 写代码失误
        raise RuntimeError(f"分发未实现 action={action!r}（与 ALLOWED_SCRIPT_ACTIONS 不同步）")


def _required(step: dict[str, Any], key: str) -> Any:
    if key not in step or step[key] is None:
        raise ValueError(f"缺 {key!r}")
    return step[key]


def _interpolate(value: Any, credentials: dict[str, Any] | None) -> str:
    """把 ``{{credentials.xxx}}`` 替换成解密凭据中的值。

    实现刻意简单：只支持 ``credentials.<key>`` 单层引用。复杂引用 / 嵌套
    属于"应该用 ai_login 解决"的场景，不在 scripted_steps 范畴内。
    """
    if not isinstance(value, str):
        return str(value)
    if "{{" not in value:
        return value
    if not credentials:
        # 没凭据但模板里含 {{credentials.xxx}} → 直接报错，避免传空字符串
        # 让登录看似 "fill 成功" 实则填了空
        raise ValueError("模板含 {{credentials.xxx}} 但未提供凭据")

    out = value
    for key, val in credentials.items():
        placeholder = "{{credentials." + key + "}}"
        if placeholder in out:
            out = out.replace(placeholder, str(val))
    if "{{credentials." in out:
        # 仍残留 → 引用了不存在的字段
        raise ValueError(f"凭据中找不到引用的字段：{out!r}")
    return out


# ─── helpers：cookie 解析 ────────────────────────────────────────────


def _resolve_cookie_spec(
    spec: dict[str, Any],
    credentials: dict[str, Any] | None,
) -> dict[str, Any]:
    """单条 cookie spec → Playwright add_cookies 接受的 dict。

    必填：name + (value | value_ref) + domain + path
    可选：expires / httpOnly / secure / sameSite
    """
    name = spec.get("name")
    if not name:
        raise ValueError("缺 name")
    domain = spec.get("domain")
    path = spec.get("path") or "/"
    if not domain:
        raise ValueError("缺 domain")

    if "value" in spec and spec["value"] is not None:
        value = str(spec["value"])
    elif "value_ref" in spec and spec["value_ref"]:
        value = _resolve_value_ref(spec["value_ref"], credentials)
    else:
        raise ValueError("缺 value / value_ref")

    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path,
    }
    for opt in ("expires", "httpOnly", "secure", "sameSite"):
        if opt in spec and spec[opt] is not None:
            cookie[opt] = spec[opt]
    return cookie


def _resolve_value_ref(ref: str, credentials: dict[str, Any] | None) -> str:
    """``credentials.<key>`` / ``literal:<value>`` → 实际字符串。"""
    if ref.startswith("literal:"):
        return ref[len("literal:"):]
    if ref.startswith("credentials."):
        if not credentials:
            raise ValueError(f"value_ref={ref!r} 引用了凭据但未提供")
        key = ref[len("credentials."):]
        if key not in credentials:
            raise ValueError(f"凭据中找不到字段 {key!r}")
        return str(credentials[key])
    raise ValueError(
        f"value_ref={ref!r} 不识别（支持 credentials.<key> / literal:<value>）"
    )


# ─── helpers：state 持久化 ───────────────────────────────────────────


async def _persist_state(
    bundle: "BrowserBundle",
    state_target: Path,
    result: PreconditionResult,
    on_state_saved: OnStateSaved | None,
) -> None:
    """登录类成功后写 storage_state；失败仅记日志，**不**翻转 result.success。

    state 写失败的逻辑：用户已经登录成功，state 写不下去最多下次再走一遍登录，
    不应该把"登录成功"这件事抹掉。
    """
    if bundle.context is None:
        result.logs.append("BrowserBundle.context 未初始化，跳过 state 持久化")
        return
    try:
        state_target.parent.mkdir(parents=True, exist_ok=True)
        await bundle.context.storage_state(path=str(state_target))
        result.state_was_saved = True
        result.state_saved_path = str(state_target)
        result.logs.append(f"storage_state 已写入：{state_target}")
        if on_state_saved is not None:
            try:
                await on_state_saved(state_target)
            except Exception as exc:  # noqa: BLE001
                result.logs.append(f"on_state_saved 回调失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        result.logs.append(f"storage_state 写入失败（不影响登录成功）：{exc}")


# ─── helpers：浏览器辅助 ─────────────────────────────────────────────


async def _ensure_page(bundle: "BrowserBundle") -> Any:
    """确保 context 上至少有一个 page，返回第一个。

    Playwright BrowserContext.new_context() 默认不开 page；首次操作前要 ``new_page()``。
    """
    if bundle.context is None:
        raise RuntimeError("BrowserBundle.context 未初始化")
    pages = bundle.context.pages
    if pages:
        return pages[0]
    return await bundle.context.new_page()


async def _read_snapshot_text(bundle: "BrowserBundle", page: Any) -> str:
    """获取当前页面的可用于过期检测的文本。

    优先调 MCP ``browser_snapshot`` 拿可访问性树（信噪比高 + token 友好）；
    MCP 不可用时回退到 ``page.content()`` 拿完整 HTML。这样在 dev 环境
    没装 Node 时也能跑（虽然 HTML 检索的 false-positive 多一些）。
    """
    if bundle.mcp_unavailable or bundle.mcp_bridge is None:
        try:
            return await page.content()
        except Exception:  # noqa: BLE001
            return ""
    try:
        # MCP browser_snapshot 返回 {"snapshot": "..."}（accessibility tree）
        snap = await bundle.mcp_bridge.call_tool("browser_snapshot", {})
        if isinstance(snap, dict):
            text = snap.get("snapshot") or snap.get("text") or ""
            return str(text)
        return str(snap)
    except Exception:  # noqa: BLE001
        try:
            return await page.content()
        except Exception:  # noqa: BLE001
            return ""


def _match_stale_keyword(text: str, keywords: tuple[str, ...]) -> str | None:
    """命中第一个返回；都没命中 None。"""
    if not text:
        return None
    for kw in keywords:
        if kw and kw in text:
            return kw
    return None


async def _safe_screenshot(bundle: "BrowserBundle", logs: list[str]) -> str | None:
    """截图失败不抛错；返回 base64 字符串或 None。"""
    if bundle.context is None:
        return None
    try:
        pages = bundle.context.pages
        if not pages:
            return None
        page = pages[0]
        png_bytes = await page.screenshot(type="png", full_page=False)
        return base64.b64encode(png_bytes).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logs.append(f"截图失败（不影响主流程）：{exc}")
        return None


__all__ = [
    "PreconditionResult",
    "AILoginRunner",
    "run_precondition",
    "DEFAULT_STALE_KEYWORDS",
    "ALLOWED_SCRIPT_ACTIONS",
]
