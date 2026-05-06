"""ExecutionEngine — UI 自动化执行流程编排（Task 9.5）。

把前面 Task 7 / 8 / 9.1–9.4 的所有部件串成一次完整的 ``execution.run``：

1. 加载环境 / LLM 配置 / 用例（含 steps）
2. 构建 ``TestDataResolver``、注册 ``platform_*`` 物料工具、跑 preflight 缺料告警
3. 落 ``ui_executions`` 行 → 切到 ``running`` 状态
4. 起 ``BrowserBundle``（Task 7.3）+ 注册 MCP browser_* 工具（Task 7.2）
5. 跑前置步骤（Task 8.2）
6. 循环用例：
    - ``case_resolver = resolver.with_case_overrides(testcase_id)``
    - 重新注册 platform 工具到 case_resolver（保证 ``finalize_case`` 拿到本用
      例的 synth / failure）
    - 循环 step：``StepRunner.run_one`` → ``AssertionJudge.judge`` → ``flush_step``
    - 用户主动停止 / token 超预算 → 提早结束（``stopped`` / ``aborted_budget``）
    - ``data_failure`` 仅终止当前用例，**继续后续用例**（核心需求）
    - ``case_resolver.finalize_case()`` → ``flush_case``
7. ``flush_execution`` 收口；finally 关 bundle、卸 platform tools、mark stream done

设计关键：
- 所有外部依赖（DB query / Bundle.open / StepRunner / Judge / persistence /
  stream hub publish）都通过 ``EngineDeps`` 注入，方便单测全 mock，不用启
  Playwright / Postgres
- 失败语义清晰：
    - ``aborted_budget``：``BudgetExceededError`` → 整个 execution 终止
    - ``data_failure``：仅本 case 终止，``data_confidence=data_failure``
    - ``failed``：execution 入口异常或 strict_data_mode 拒绝执行
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.core.crypto import decrypt
from app.modules.ui_automation import persistence as default_persistence
from app.modules.ui_automation.assertion_judge import (
    AssertionJudge,
    AssertionLLMConfig,
    AssertionVerdict,
)
from app.modules.ui_automation.data_platform_tools import (
    register_data_tools,
    unregister_data_tools,
)
from app.modules.ui_automation.debug_control import (
    DEBUG_CONTROL_HUB,
    DEFAULT_DEBUG_TIMEOUT_SECONDS,
)
from app.modules.ui_automation.preflight import (
    MissingDataAlert,
    preflight_data_check,
)
from app.modules.ui_automation.security import (
    BudgetExceededError,
    SecurityError,
    TokenBudget,
)
from app.modules.ui_automation.step_runner import (
    StepRunner,
    StepRunResult,
    ToolCallRecord,
)
from app.modules.ui_automation.stream_hub import (
    EXECUTION_STREAM_HUB,
    _ExecutionStream,
)
from app.modules.ui_automation.test_data_resolver import TestDataResolver

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.llm.models import LLMConfig
    from app.modules.testcases.models import Testcase
    from app.modules.ui_automation.models import TestEnvironment


logger = logging.getLogger(__name__)


# ─── 输入参数 ────────────────────────────────────────────────────────


@dataclass
class ExecutionInputs:
    """``ExecutionEngine.run`` 的全部入参（来自 API / chat / SDK）。

    在 Engine 内不会再读 DB 拿这些字段，全部由调用方填好。
    """

    execution_id: uuid.UUID
    project_id: uuid.UUID
    environment_id: uuid.UUID | None
    testcase_ids: list[uuid.UUID]
    llm_config_id: uuid.UUID | None
    triggered_by: uuid.UUID | None
    manual_overrides: dict[str, Any] = field(default_factory=dict)
    loaded_set_ids: list[uuid.UUID] = field(default_factory=list)
    mode: str = "normal"
    chat_message_id: uuid.UUID | None = None
    token_budget_override: int | None = None
    """覆盖 environment.token_budget；为 None 时用环境默认。"""
    strict_data_mode: bool = False
    """缺料严格模式：preflight 发现缺 key 直接拒绝执行。"""
    module_entry_overrides: dict[uuid.UUID, str] = field(default_factory=dict)
    """按 module_id 临时覆盖 module.entry_path（仅本次执行有效）。

    取值规则（在引擎里查找 effective entry_path 时按下述优先级）：
        ``module_entry_overrides[module_id]`` → ``module.entry_path`` (DB) → None

    None 表示"该模块没配入口"——AI 仍能跑，但 prompt 里不会出现 target_url
    那一段，行为退回到现状（依赖用例 step 自然语言）。
    """


@dataclass
class ExecutionOutcome:
    """run() 收尾后的简短摘要。SSE / API 响应可用。"""

    execution_id: uuid.UUID
    status: str
    total: int
    passed: int
    failed: int
    skipped: int
    duration_ms: int
    tokens_total: int
    error_message: str | None = None
    # 产物路径（Engine 在 bundle 关闭后填入；outer finally 会带入 flush_execution）
    video_path: str | None = None
    trace_path: str | None = None


# ─── Dependency injection ────────────────────────────────────────────


class _BundleLike(Protocol):
    """Engine 用到的 BrowserBundle 最小契约（简化测试 mock）。"""

    execution_id: uuid.UUID
    mcp_unavailable: bool

    async def register_mcp_tools_for_agent(self) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


@dataclass
class EngineDeps:
    """所有"会读外部世界"的依赖都在这里，方便单测整体替换。

    生产默认实现都在 module 级别，测试可以替换其中任何一个。
    """

    db_session_factory: Callable[[], "AsyncSession"] = field(
        default=lambda: _default_db_session_factory()
    )
    """每次调一次得到一个 ``AsyncSession``（已 begin / not yet committed）。
    Engine 用它 ``await session.execute(...)`` 拿环境 / 用例 / llm 配置。
    """

    open_browser_bundle: Callable[..., Awaitable[_BundleLike]] | None = None
    """``async def(env, execution_id) -> BrowserBundle``。None = lazy import。"""

    step_runner_factory: Callable[..., StepRunner] | None = None
    """``(env, llm, budget, execution_id) -> StepRunner``。None = 直接 ``StepRunner(...)``。"""

    assertion_judge_factory: Callable[[], AssertionJudge] | None = None

    persistence: Any = default_persistence
    """暴露 ``init_execution_record / mark_execution_running / create_case_result /
    flush_step / flush_case / flush_execution / is_execution_stopped``。"""

    stream_hub: Any = EXECUTION_STREAM_HUB

    run_preconditions: (
        Callable[..., Awaitable[list[dict[str, Any]]]] | None
    ) = None
    """``async def(bundle, env, llm_config_or_none) -> list[result_dict]``。
    None 时跳过前置步骤（典型：本环境没配前置模板）。"""

    debug_controller: Any = DEBUG_CONTROL_HUB
    """Task 9.7 — debug 模式 pause/continue 信号 hub。注入便于单测。"""

    debug_timeout_seconds: float = DEFAULT_DEBUG_TIMEOUT_SECONDS
    """每个 step 之间最多 pause 多久；超过自动 stop。测试时可以 patch 成 0.05 秒。"""


def _default_db_session_factory() -> "AsyncSession":
    """运行时再 import 避免 alembic 拓扑被无谓加载。"""
    from app.database import async_session_factory

    return async_session_factory()


async def _default_open_bundle(env: Any, execution_id: uuid.UUID) -> _BundleLike:
    from app.config import settings
    from app.modules.ui_automation.browser_bundle import BrowserBundle, BundleOptions

    artifacts_root = os.path.abspath(settings.UI_ARTIFACTS_DIR)
    video_dir = os.path.join(artifacts_root, str(execution_id), "video")
    os.makedirs(video_dir, exist_ok=True)

    # 出口代理（VPN 场景）：优先取 environment 自身配置，回落到全局
    # ``settings.UI_BROWSER_PROXY``。这样常态部署可以全局走一个统一代理，单环境
    # 也能定制（比如跨多 VPN 多代理的高级场景）。
    env_proxy = getattr(env, "browser_proxy", None) or None
    proxy_server = env_proxy or settings.UI_BROWSER_PROXY or None
    proxy_bypass = (
        getattr(env, "browser_proxy_bypass", None)
        or settings.UI_BROWSER_PROXY_BYPASS
        or None
    )

    return await BrowserBundle.open(  # type: ignore[return-value]
        env,
        execution_id,
        options=BundleOptions(
            headless=getattr(env, "headless", True),
            record_video_dir=video_dir,
            browser_proxy=proxy_server,
            browser_proxy_bypass=proxy_bypass,
        ),
    )


# playwright-mcp 0.x 的 snapshot result 通常包含一段::
#     ### Page
#     - Page URL: https://x.com/foo
#     - Page Title: Foo Dashboard
#     ### Snapshot
#     ...
# 直接 regex 抽出 URL / title 比再发一次 MCP 调用更省 token、更稳（同步内存
# 操作，不会因 MCP 抖动失败）。
_PAGE_URL_RE = re.compile(r"Page URL:\s*(\S+)", re.IGNORECASE)
_PAGE_TITLE_RE = re.compile(r"Page Title:\s*([^\r\n]+)", re.IGNORECASE)


def _safe_extract_page_url(snapshot_text: str | None) -> str | None:
    """从 a11y snapshot 文本里抽 ``Page URL: ...``；找不到返回 None。"""
    if not snapshot_text:
        return None
    m = _PAGE_URL_RE.search(snapshot_text)
    return m.group(1).strip() if m else None


def _safe_extract_page_title(snapshot_text: str | None) -> str | None:
    """从 a11y snapshot 文本里抽 ``Page Title: ...``；找不到返回 None。"""
    if not snapshot_text:
        return None
    m = _PAGE_TITLE_RE.search(snapshot_text)
    if m:
        return m.group(1).strip() or None
    return None


async def _safe_get_current_url(
    bundle: Any, *, fallback_snapshot: str | None = None,
) -> str | None:
    """尽力取浏览器当前页面 URL；任何失败返回 None。

    解析顺序（优先级降序）：
    1. 从 ``fallback_snapshot`` regex 抽 ``Page URL:`` —— 便宜 + 稳
    2. 调 ``bundle.get_current_url_via_mcp()`` —— 当 snapshot 里没带时兜底

    用途：ExecutionEngine 在两步骤之间刷新"当前 URL"，让下一步的 prompt
    准确反映浏览器状态。失败不阻塞用例推进。
    """
    from_snap = _safe_extract_page_url(fallback_snapshot)
    if from_snap:
        return from_snap
    if bundle is None:
        return None
    try:
        get_url = getattr(bundle, "get_current_url_via_mcp", None)
        if get_url is None:
            return None
        result = await get_url()
        if isinstance(result, str) and result.strip():
            return result.strip()
    except Exception as exc:  # noqa: BLE001
        logger.info("ExecutionEngine refresh current_url failed: %s", exc)
    return None


async def _capture_step_screenshot_safe(
    *,
    bundle: Any,
    execution_id: uuid.UUID,
    case_result_id: uuid.UUID,
    step_number: int,
) -> str | None:
    """尽力抓一张当前 step 完成时的浏览器截图；任何失败都返回 None，不阻塞
    step flush。

    优先级：
    1. **MCP** ``browser_take_screenshot`` —— 最可靠，因为 MCP 一定知道哪个
       tab 是 active，且截图作用于 MCP 操作的 page（与 Python SDK 的 context
       不一定共享）。
    2. **Playwright SDK** ``page.screenshot()`` —— fallback。仅当 MCP 不可用
       或返回 None 时尝试。
    """
    try:
        from app.config import settings

        ext = (settings.UI_STEP_SCREENSHOT_TYPE or "png").lower()
        if ext not in ("png", "jpeg", "jpg"):
            ext = "png"
        image_type = "jpeg" if ext in ("jpeg", "jpg") else "png"
        steps_dir = os.path.join(
            os.path.abspath(settings.UI_ARTIFACTS_DIR),
            str(execution_id),
            "steps",
        )
        os.makedirs(steps_dir, exist_ok=True)
        dest = os.path.join(
            steps_dir,
            f"case_{case_result_id}_step_{step_number:03d}.{ext}",
        )
    except Exception:  # noqa: BLE001
        logger.exception("capture_step_screenshot_safe path setup failed")
        return None

    via_mcp = getattr(bundle, "capture_step_screenshot_via_mcp", None)
    if callable(via_mcp):
        try:
            path = await via_mcp(dest, image_type=image_type)
            if path:
                return path
        except Exception:  # noqa: BLE001
            logger.exception("capture_step_screenshot_via_mcp raised")

    via_sdk = getattr(bundle, "capture_step_screenshot", None)
    if callable(via_sdk):
        try:
            return await via_sdk(dest, image_type=image_type, full_page=False)
        except Exception:  # noqa: BLE001
            logger.exception("capture_step_screenshot (SDK) raised")
    return None


# ─── ExecutionEngine ─────────────────────────────────────────────────


class ExecutionEngine:
    """单次执行批次的编排器。一次 ``run()`` 调用 = 一次执行。"""

    __test__ = False

    def __init__(self, *, deps: EngineDeps | None = None) -> None:
        self.deps = deps or EngineDeps()

    async def run(self, inputs: ExecutionInputs) -> ExecutionOutcome:
        started_at = time.monotonic()
        stream = await self.deps.stream_hub.register(inputs.execution_id)

        # Task 9.7：debug 模式下提前 register 信号槽，让 router 在 step_paused
        # 第一次出现之前就能接收 ``POST /continue``——否则极快的第一步会出现
        # "用户点 continue 时 signal 还没建" 的竞态。
        debug_registered = False
        if inputs.mode == "debug":
            try:
                await self.deps.debug_controller.register(inputs.execution_id)
                debug_registered = True
            except Exception:  # noqa: BLE001
                logger.exception("debug_controller.register failed")

        outcome = ExecutionOutcome(
            execution_id=inputs.execution_id,
            status="failed",
            total=len(inputs.testcase_ids),
            passed=0,
            failed=0,
            skipped=0,
            duration_ms=0,
            tokens_total=0,
        )

        try:
            await self._run_inner(inputs, stream, outcome)
        except BudgetExceededError as exc:
            outcome.status = "aborted_budget"
            outcome.error_message = str(exc)
            await stream.append("budget_exceeded", {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("ExecutionEngine.run crashed: %s", inputs.execution_id)
            outcome.status = "failed"
            outcome.error_message = f"{type(exc).__name__}: {exc}"
            await stream.append(
                "execution_error",
                {"error": outcome.error_message},
            )
        finally:
            outcome.duration_ms = int((time.monotonic() - started_at) * 1000)
            try:
                await self.deps.persistence.flush_execution(
                    execution_id=inputs.execution_id,
                    status=outcome.status,
                    passed_cases=outcome.passed,
                    failed_cases=outcome.failed,
                    skipped_cases=outcome.skipped,
                    duration_ms=outcome.duration_ms,
                    tokens_total=outcome.tokens_total,
                    error_message=outcome.error_message,
                    video_path=outcome.video_path,
                    trace_path=outcome.trace_path,
                )
            except Exception as flush_exc:  # noqa: BLE001
                logger.exception("flush_execution failed: %s", flush_exc)

            await stream.append(
                "execution_complete",
                {
                    "execution_id": str(inputs.execution_id),
                    "status": outcome.status,
                    "passed": outcome.passed,
                    "failed": outcome.failed,
                    "skipped": outcome.skipped,
                    "duration_ms": outcome.duration_ms,
                    "tokens_total": outcome.tokens_total,
                    "error_message": outcome.error_message,
                },
            )
            await stream.mark_done()

            # Task 9.7：必清——否则 hub 会越积越多 _DebugSignal 实例
            if debug_registered:
                try:
                    await self.deps.debug_controller.unregister(inputs.execution_id)
                except Exception:  # noqa: BLE001
                    logger.exception("debug_controller.unregister failed")

        return outcome

    # ── 主流程 ───────────────────────────────────────────────────

    async def _run_inner(
        self,
        inputs: ExecutionInputs,
        stream: _ExecutionStream,
        outcome: ExecutionOutcome,
    ) -> None:
        # 1. 加载环境 / LLM 配置 / 用例
        async with self.deps.db_session_factory() as db:
            environment = await _load_environment(db, inputs.environment_id)
            llm_config_orm = await _load_llm_config(db, inputs.llm_config_id)
            testcases = await _load_testcases(db, inputs.testcase_ids)
            # 预加载本批次所有用例的 module.entry_path —— 后续 _run_one_case
            # 再去拼 target_url 时 O(1) 查表，不用每条用例都打一次 DB
            #
            # 用 ``getattr`` 兜底是因为单元测试里的轻量 ``_Testcase`` stub 不带
            # 这个字段；生产 ORM 一定有（``Testcase.module_id`` 是 mapped_column）。
            module_entry_map = await _load_module_entry_paths(
                db,
                [
                    getattr(tc, "module_id", None)
                    for tc in testcases
                    if getattr(tc, "module_id", None) is not None
                ],
            )

            # 2. 构建 TestDataResolver
            resolver = await TestDataResolver.build(
                db=db,
                execution=_ResolverExecutionStub(
                    project_id=inputs.project_id,
                    environment_id=inputs.environment_id,
                    triggered_by=inputs.triggered_by,
                ),
                manual_overrides=inputs.manual_overrides,
                loaded_set_ids=inputs.loaded_set_ids,
            )
            # build with_case_overrides 需要的是同一个 db session；后续在该
            # session 上做的查询都已在 selectin 加载，调用方关闭 session 后
            # 不能再用 resolver.with_case_overrides —— 因此本函数把整段
            # "用例循环"也圈在 db scope 内

            # 收集本次「显式配置」的物料集 id（验收反馈：物料快照仅展示这些
            # 集合的明细，过滤掉个人/项目 scope 自动合并的杂项）：
            #   loaded_set_ids（弹窗勾选）
            # + env.default_data_set_ids（环境默认）
            # + testcase.default_data_set_ids（用例默认）
            configured_set_ids = await _collect_configured_set_ids(
                db=db,
                project_id=inputs.project_id,
                environment_id=inputs.environment_id,
                loaded_set_ids=inputs.loaded_set_ids,
                testcase_ids=inputs.testcase_ids,
            )
            data_snapshot = resolver.serialize_for_audit(
                configured_set_ids=configured_set_ids,
            )

            await self.deps.persistence.init_execution_record(
                execution_id=inputs.execution_id,
                project_id=inputs.project_id,
                environment_id=inputs.environment_id,
                triggered_by=inputs.triggered_by,
                chat_message_id=inputs.chat_message_id,
                mode=inputs.mode,
                total_cases=len(testcases),
                config_snapshot=_build_config_snapshot(
                    inputs,
                    configured_set_ids=configured_set_ids,
                ),
            )
            await self.deps.persistence.mark_execution_running(
                execution_id=inputs.execution_id,
                test_data_snapshot=data_snapshot,
            )
            await stream.append(
                "execution_started",
                {
                    "execution_id": str(inputs.execution_id),
                    "total_cases": len(testcases),
                    "mode": inputs.mode,
                },
            )

            # 3. preflight 缺料告警
            alerts = await preflight_data_check(testcases, resolver)
            if alerts:
                await stream.append(
                    "missing_data_warning",
                    {
                        "alerts": [a.model_dump() for a in alerts],
                        "strict": inputs.strict_data_mode,
                    },
                )
                if inputs.strict_data_mode:
                    outcome.status = "failed"
                    outcome.error_message = (
                        f"严格物料模式：发现 {len(alerts)} 个缺料 key，拒绝执行"
                    )
                    return

            if not testcases:
                outcome.status = "completed"
                return

            # 4. 起浏览器 + 注册 MCP 工具
            bundle = await _open_bundle(self.deps, environment, inputs.execution_id)
            try:
                if getattr(bundle, "headless_downgraded", False):
                    await stream.append(
                        "headless_downgraded",
                        {
                            "execution_id": str(inputs.execution_id),
                            "message": (
                                "当前部署运行在容器中且未挂载显示器（DISPLAY），"
                                "已自动忽略环境的『有头模式』设置改用无头模式。"
                                "如需有头浏览器，请在带 X 服务器（或 Xvfb）的宿主机直接部署后端。"
                            ),
                        },
                    )
                await stream.append(
                    "bundle_ready",
                    {
                        "execution_id": str(inputs.execution_id),
                        "mcp_unavailable": bundle.mcp_unavailable,
                    },
                )
                mcp_specs: list[dict[str, Any]] = []
                if not bundle.mcp_unavailable:
                    try:
                        mcp_specs = await bundle.register_mcp_tools_for_agent()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("register_mcp_tools_for_agent failed: %s", exc)
                        mcp_specs = []

                # 5. 跑前置步骤（默认走 ``_default_run_preconditions``，环境
                #    没配前置步骤时返回 [] 自然 no-op；测试可通过
                #    ``EngineDeps.run_preconditions=...`` 覆盖）。
                run_preconds = (
                    self.deps.run_preconditions or _default_run_preconditions
                )
                try:
                    pre_results = await run_preconds(
                        bundle, environment, llm_config_orm,
                    )
                    if pre_results:
                        await stream.append(
                            "preconditions_complete",
                            {"results": pre_results},
                        )
                        # 若任一前置步骤明确失败 → 后续用例不再继续，避免在
                        # 没有登录态 / 关键 cookie 的状态下大批量失败。
                        first_failed = next(
                            (r for r in pre_results if not r.get("success", True)),
                            None,
                        )
                        if first_failed is not None:
                            outcome.status = "failed"
                            outcome.error_message = (
                                f"前置步骤未通过：{first_failed.get('name')!r} → "
                                f"{first_failed.get('error') or first_failed.get('error_kind')}"
                            )
                            await stream.append(
                                "precondition_error",
                                {
                                    "error": outcome.error_message,
                                    "error_kind": first_failed.get("error_kind"),
                                },
                            )
                            return
                except Exception as exc:  # noqa: BLE001
                    logger.exception("run_preconditions failed")
                    await stream.append(
                        "precondition_error",
                        {"error": f"{type(exc).__name__}: {exc}"},
                    )

                # 6. 准备运行时
                budget = TokenBudget(
                    limit=inputs.token_budget_override
                    or getattr(environment, "token_budget", 50_000)
                )
                llm_config_proto = _build_llm_proto(llm_config_orm)
                judge_llm_config = (
                    AssertionLLMConfig(
                        provider=llm_config_proto.provider,
                        model=llm_config_proto.model,
                        api_key=llm_config_proto.api_key,
                        base_url=llm_config_proto.base_url,
                        temperature=0.0,
                        max_tokens=512,
                    )
                    if llm_config_proto.api_key or llm_config_proto.base_url
                    else None
                )
                runner_factory = self.deps.step_runner_factory or (
                    lambda env, llm, budget_, execution_id: StepRunner(
                        llm=llm,
                        environment=env,
                        budget=budget_,
                        execution_id=execution_id,
                    )
                )
                step_runner = runner_factory(
                    environment, llm_config_proto, budget, inputs.execution_id,
                )
                judge_factory = self.deps.assertion_judge_factory or AssertionJudge
                judge = judge_factory()

                # 7. 用例循环
                for sort_idx, tc in enumerate(testcases):
                    # 检查停止信号
                    if await _check_stopped(self.deps, inputs.execution_id):
                        outcome.status = "stopped"
                        outcome.error_message = "用户主动停止"
                        await stream.append(
                            "execution_stopped",
                            {"reason": "user_stop"},
                        )
                        # 剩余用例不执行 → 计入 skipped
                        outcome.skipped += len(testcases) - sort_idx
                        return

                    if budget.over_limit:
                        outcome.status = "aborted_budget"
                        outcome.error_message = (
                            f"已超过 token 预算 {budget.limit}（消耗 {budget.consumed}）"
                        )
                        await stream.append(
                            "budget_exceeded",
                            {"message": outcome.error_message},
                        )
                        outcome.skipped += len(testcases) - sort_idx
                        return

                    case_aborted = await self._run_one_case(
                        db=db,
                        bundle=bundle,
                        tc=tc,
                        sort_idx=sort_idx,
                        inputs=inputs,
                        environment=environment,
                        module_entry_map=module_entry_map,
                        resolver=resolver,
                        mcp_specs=mcp_specs,
                        step_runner=step_runner,
                        judge=judge,
                        judge_llm_config=judge_llm_config,
                        budget=budget,
                        stream=stream,
                        outcome=outcome,
                    )
                    if case_aborted == "budget":
                        outcome.status = "aborted_budget"
                        outcome.error_message = (
                            f"已超过 token 预算 {budget.limit}（消耗 {budget.consumed}）"
                        )
                        await stream.append(
                            "budget_exceeded",
                            {"message": outcome.error_message},
                        )
                        outcome.skipped += len(testcases) - sort_idx - 1
                        return
                    if case_aborted in ("stopped", "debug_timeout"):
                        outcome.status = "stopped"
                        outcome.error_message = (
                            "用户在调试模式中主动停止"
                            if case_aborted == "stopped"
                            else f"调试模式 {self.deps.debug_timeout_seconds:.0f}s 内未"
                                 "收到 continue，自动停止"
                        )
                        reason = (
                            "user_stop_during_debug"
                            if case_aborted == "stopped"
                            else "debug_timeout"
                        )
                        await stream.append(
                            "execution_stopped" if case_aborted == "stopped"
                            else "debug_timeout",
                            {
                                "reason": reason,
                                "timeout_seconds": self.deps.debug_timeout_seconds,
                            },
                        )
                        outcome.skipped += len(testcases) - sort_idx - 1
                        return

                outcome.status = "completed"
            finally:
                outcome.tokens_total = budget.consumed if "budget" in locals() else 0
                # 关 bundle 前先固定 video 引用；关了之后 context 就没了
                try:
                    finalize = getattr(bundle, "finalize_videos", None)
                    if callable(finalize):
                        await finalize()
                except Exception:  # noqa: BLE001
                    logger.exception("BrowserBundle.finalize_videos failed")
                try:
                    await bundle.close()
                except Exception:  # noqa: BLE001
                    logger.exception("BrowserBundle.close failed")
                # 关闭后再读 video 实际路径，写回 outcome 让外层 flush_execution 落盘
                try:
                    collect = getattr(bundle, "collect_video_paths", None)
                    if callable(collect):
                        paths = await collect()
                        if paths:
                            outcome.video_path = paths[0]
                except Exception:  # noqa: BLE001
                    logger.exception("BrowserBundle.collect_video_paths failed")

    # ── 单条用例执行 ──────────────────────────────────────────────

    async def _run_one_case(
        self,
        *,
        db: "AsyncSession",
        bundle: _BundleLike,
        tc: "Testcase",
        sort_idx: int,
        inputs: ExecutionInputs,
        environment: Any,
        module_entry_map: dict[uuid.UUID, str | None],
        resolver: TestDataResolver,
        mcp_specs: list[dict[str, Any]],
        step_runner: StepRunner,
        judge: AssertionJudge,
        judge_llm_config: AssertionLLMConfig | None,
        budget: TokenBudget,
        stream: _ExecutionStream,
        outcome: ExecutionOutcome,
    ) -> str | None:
        """跑一条用例。返回 None / ``"budget"``（外层据此决定是否中止整批）。"""
        case_started = time.monotonic()

        # ── 用例间页面状态清理（sort_idx > 0 时启用）───────────────────
        # 第一条用例不需要重置——bundle.open() 后浏览器本来就是干净状态、且
        # storage_state 注入刚做完。从第二条开始，上一条用例可能在浏览器里
        # 留下未关弹窗 / 未提交表单 / SPA 路由 history / 残留 sessionStorage，
        # 这些会污染下一条用例的判断（典型：上条停在 ``/edit?id=999``、下条
        # 进来 AI 看到 ``current_url`` 不匹配 target_url 就会去 navigate，但
        # navigate 期间未保存的对话框可能挡住操作）。``reset_for_next_case``
        # 关掉多余 page + 主 page 跳 about:blank，**保留 cookies/localStorage**
        # 让登录态延续，单条用例之间从干净起跑。
        if sort_idx > 0:
            reset_fn = getattr(bundle, "reset_for_next_case", None)
            if callable(reset_fn):
                try:
                    reset_report = await reset_fn()
                    await stream.append(
                        "case_reset",
                        {
                            "next_case_index": sort_idx,
                            "closed_extra_pages": reset_report.get("closed_extra_pages", 0),
                            "navigated_to_blank": reset_report.get(
                                "navigated_to_blank", False,
                            ),
                            "errors": reset_report.get("errors") or [],
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    # reset 失败不致命：本条用例 step 1 的 prompt 仍会引导
                    # AI 自己 navigate 到 target_url 兜底。只 log + 流事件。
                    logger.warning(
                        "reset_for_next_case failed before case sort=%d: %s",
                        sort_idx, exc,
                    )
                    await stream.append(
                        "case_reset",
                        {
                            "next_case_index": sort_idx,
                            "errors": [f"{type(exc).__name__}: {exc}"],
                        },
                    )

        case_resolver = await resolver.with_case_overrides(tc.id)
        case_resolver.reset_case_state()
        # 计算本条用例的 target_url（base_url + module.entry_path / override）。
        # None 表示该模块未配且未临时覆盖 → step_runner 收到 None 时 prompt 里
        # 不会出现 "目标 URL：…" 块，行为退回到现状（依赖 step.action 自然语言）。
        target_url = _resolve_target_url(
            tc=tc,
            environment=environment,
            module_entry_map=module_entry_map,
            module_entry_overrides=inputs.module_entry_overrides,
        )

        # 重新注册 platform 工具到 case_resolver
        unregister_data_tools(inputs.execution_id)
        register_data_tools(inputs.execution_id, case_resolver, db=db)

        case_row = await self.deps.persistence.create_case_result(
            execution_id=inputs.execution_id,
            testcase_id=tc.id,
            sort_order=sort_idx,
        )
        # 与 replayer 保持事件结构同构：除 title 外还要带 ``testcase_no`` /
        # ``testcase_module_name``，前端用以渲染 ``TC-0061 标题`` 形式。
        # tc 是 ORM 加载的 ``Testcase``——它没有 module_name 直接字段（要 join
        # TestcaseModule），engine 里目前 testcase loader 不一定 eager-load 模
        # 块，所以兜底走 ``getattr(tc, "module", None)`` 走 relationship；如果
        # 没有 relationship 也只是 ``None``，前端展示 ``TC-0061 标题`` 仍然
        # 工作。
        _module = getattr(tc, "module", None)
        await stream.append(
            "case_started",
            {
                "case_result_id": str(case_row.id),
                "testcase_id": str(tc.id),
                "title": getattr(tc, "title", "") or "",
                "testcase_no": getattr(tc, "case_no", None),
                "testcase_module_name": getattr(_module, "name", None) if _module else None,
                "sort_order": sort_idx,
            },
        )

        case_status = "passed"
        case_error: str | None = None
        case_tokens_before = budget.consumed
        last_snapshot_text: str | None = None
        # Task 9.4 修复 #3c95cf69：跨 step 维护"当前 URL / 页面标题"，
        # 让下一个 step 的 prompt 看到准确的浏览器状态——否则 step 2+ 收到
        # ``current_url="(未知)"`` + ``snapshot_block=空``，AI 出于保险倾向
        # 在每个 step 开头都重新 navigate，**冲掉前一步在表单里输入的内容**
        # （典型表现：step 1 输入"9999"通过，step 2 点查询却看到全部数据，
        # 因为 step 2 又 navigate 一次重置了表单）。
        # 步骤之间不连贯 = 整条用例失效，这条修复必须保留。
        #
        # 用例切换后（sort_idx>0）``reset_for_next_case`` 已经把主 page 跳到
        # about:blank。这里给 step 1 的 prompt 同步一个准确的初值——让 AI 看
        # 到 ``current_url=about:blank`` 后明确知道"需要 navigate 到 target_url"，
        # 避免和未知态搅在一起。第一条用例（sort_idx=0）保持 ``(未知)``，因为
        # 浏览器刚启动时主 page 可能还没创建。
        last_url: str
        last_page_title: str
        if sort_idx > 0:
            last_url = "about:blank"
            last_page_title = "(已重置 / 新用例起点)"
        else:
            last_url = "(未知)"
            last_page_title = "(未知)"
        steps = list(getattr(tc, "steps", []) or [])
        step_iter = iter(steps)
        case_aborted_budget = False
        # Task 9.7：debug 模式专属退出原因。与 budget 互斥；最先触发的赢
        case_user_stopped = False
        case_debug_timeout = False
        is_debug = inputs.mode == "debug"

        for step in step_iter:
            try:
                rendered_action = case_resolver.render_template(step.action or "")
                rendered_expected = case_resolver.render_template(
                    step.expected_result or "",
                )
                manifest = case_resolver.render_manifest_markdown()

                await stream.append(
                    "step_started",
                    {
                        "case_result_id": str(case_row.id),
                        "step_number": step.step_number,
                        "action_preview": rendered_action[:200],
                    },
                )

                step_started_at = time.monotonic()
                run_result = await step_runner.run_one(
                    step_description=rendered_action,
                    expected=rendered_expected,
                    bundle=bundle,
                    data_manifest=manifest,
                    data_resolver=case_resolver,
                    prev_snapshot=last_snapshot_text,
                    # 把上一步的"当前 URL / 页面标题 / a11y 快照"注入到本步骤
                    # 的 system prompt，让 AI 看到真实的浏览器状态（关键：
                    # 已经在目标 URL 时不要重新 navigate，详见上文 ``last_url``
                    # 字段注释 + #3c95cf69 修复案例）。step 1 时这三者都是
                    # 默认值，AI 会按 prompt 里的指引 navigate 到 target_url；
                    # step 2+ 接续上一步的状态，AI 不会再保险性 navigate。
                    initial_snapshot_text=last_snapshot_text,
                    current_url=last_url,
                    page_title=last_page_title,
                    mcp_tool_specs=mcp_specs,
                    target_url=target_url,
                )
                last_snapshot_text = run_result.last_snapshot_text or last_snapshot_text
                # 步骤收尾：刷新"当前 URL / 标题"给下一步用。优先从刚拿到的
                # snapshot 文本里抽（playwright-mcp 在 snapshot result 里就带了
                # Page URL/Title），没抽到再调 MCP。best-effort：失败保留旧值。
                fresh_url = await _safe_get_current_url(
                    bundle, fallback_snapshot=run_result.last_snapshot_text,
                )
                if fresh_url:
                    last_url = fresh_url
                fresh_title = _safe_extract_page_title(run_result.last_snapshot_text)
                if fresh_title:
                    last_page_title = fresh_title

                if run_result.error_kind == "budget_exceeded":
                    case_status = "error"
                    case_error = run_result.error or "token 预算耗尽"
                    case_aborted_budget = True

                # AssertionJudge
                verdict: AssertionVerdict
                if run_result.error_kind in ("budget_exceeded", "security_blocked", "llm_error"):
                    verdict = AssertionVerdict(
                        passed=False,
                        reason=run_result.error or run_result.error_kind or "step 异常未通过",
                        method="skipped",
                    )
                else:
                    verdict = await judge.judge(
                        expected=rendered_expected,
                        snapshot=run_result.last_snapshot_text,
                        step_description=rendered_action,
                        llm_config=judge_llm_config,
                    )

                step_status = (
                    "blocked_by_security"
                    if run_result.error_kind == "security_blocked"
                    else "passed" if verdict.passed
                    else "failed"
                )
                step_duration = int((time.monotonic() - step_started_at) * 1000)

                # 每步截图（best-effort：失败不阻塞用例推进）
                screenshot_path = await _capture_step_screenshot_safe(
                    bundle=bundle,
                    execution_id=inputs.execution_id,
                    case_result_id=case_row.id,
                    step_number=step.step_number,
                )

                await self.deps.persistence.flush_step(
                    case_result_id=case_row.id,
                    step_number=step.step_number,
                    description=rendered_action,
                    expected_result=rendered_expected or None,
                    tool_calls=[
                        _serialize_tool_call(tc_) for tc_ in run_result.tool_calls
                    ],
                    ai_reasoning=run_result.reasoning or None,
                    snapshot_before=None,  # Engine 当前未单独捕获 step-before snapshot
                    snapshot_after=run_result.last_snapshot_text,
                    assertion_passed=verdict.passed,
                    assertion_reason=verdict.reason,
                    assertion_evidence=verdict.evidence,
                    status=step_status,
                    screenshot_path=screenshot_path,
                    error_message=run_result.error,
                    tokens_used=run_result.tokens_used - case_tokens_before
                    if run_result.tokens_used >= case_tokens_before else 0,
                    duration_ms=step_duration,
                )
                # screenshot_url 在事件里走 nginx 静态路径（无需 Bearer
                # token），让前端 LiveScreenshot 直接 ``<img src>`` 加载
                screenshot_url: str | None = None
                if screenshot_path:
                    from app.config import settings

                    art_root = os.path.abspath(settings.UI_ARTIFACTS_DIR)
                    abs_p = os.path.abspath(screenshot_path)
                    if abs_p.startswith(art_root + os.sep):
                        rel = os.path.relpath(abs_p, art_root).replace(os.sep, "/")
                        screenshot_url = f"/uploads/ui_artifacts/{rel}"
                await stream.append(
                    "step_complete",
                    {
                        "case_result_id": str(case_row.id),
                        "step_number": step.step_number,
                        "status": step_status,
                        "assertion": verdict.to_dict(),
                        "tool_calls": len(run_result.tool_calls),
                        "tokens_used": run_result.tokens_used,
                        "iterations": run_result.iterations,
                        "duration_ms": step_duration,
                        "error": run_result.error,
                        "screenshot_url": screenshot_url,
                    },
                )

                if budget_warning := budget.maybe_warning():
                    await stream.append("budget_warning", {"message": budget_warning})

                if not verdict.passed and case_status == "passed":
                    case_status = "failed"

                if case_aborted_budget:
                    break

                # Task 9.7 — debug 模式：每步完成后暂停，等用户调
                # ``POST /continue`` 推进。只在还没 abort 的情况下暂停（已经
                # 出 budget / failure 时再暂停就纯属浪费时间）。
                if is_debug:
                    pause_action = await self._maybe_debug_pause(
                        inputs=inputs,
                        stream=stream,
                        case_row_id=case_row.id,
                        step_number=step.step_number,
                    )
                    if pause_action == "stopped":
                        case_user_stopped = True
                        break
                    if pause_action == "timeout":
                        case_debug_timeout = True
                        break

                # data_failure 早停：发现 mark_data_failure 已经触发 → 后续步骤不再跑
                # （case_resolver 会在 finalize_case 里给出 data_failure）
                if case_resolver._case_failures:  # noqa: SLF001
                    case_status = "error"
                    case_error = (
                        f"用例数据问题：{case_resolver._case_failures[-1].get('reason', '')}"  # noqa: SLF001
                    )
                    break
            except BudgetExceededError as exc:
                case_aborted_budget = True
                case_status = "error"
                case_error = str(exc)
                break
            except SecurityError as exc:
                logger.exception("SecurityError during step")
                case_status = "error"
                case_error = str(exc)
                break
            except Exception as exc:  # noqa: BLE001
                logger.exception("Step crashed unexpectedly")
                case_status = "error"
                case_error = f"{type(exc).__name__}: {exc}"
                break

        # 收尾本用例
        # Task 9.7：debug 暂停被打断的用例，剩余步骤当作 skipped；状态从 passed
        # 改成 skipped/error 让前端不会误以为"通过"。判断要在 finalize 之前。
        if case_user_stopped and case_status == "passed":
            case_status = "skipped"
            case_error = case_error or "用户在调试中主动停止"
        elif case_debug_timeout and case_status == "passed":
            case_status = "skipped"
            case_error = case_error or (
                f"调试模式 {self.deps.debug_timeout_seconds:.0f}s 内未收到 continue，自动停止"
            )

        case_finalized = case_resolver.finalize_case()
        if case_finalized["data_confidence"] == "data_failure" and case_status == "passed":
            # 即便 step 都"通过"，AI 标了 data_failure，结果应记为 error
            case_status = "error"
            if not case_error:
                fails = case_finalized.get("data_failures") or []
                case_error = "数据失败：" + str(fails[0]) if fails else "数据失败"

        case_tokens_used = max(0, budget.consumed - case_tokens_before)
        case_duration = int((time.monotonic() - case_started) * 1000)

        await self.deps.persistence.flush_case(
            case_result_id=case_row.id,
            status=case_status,
            ai_summary=None,
            error_message=case_error,
            duration_ms=case_duration,
            tokens_used=case_tokens_used,
            test_data_used=_extract_test_data_used(case_resolver),
            synthesized_data=case_finalized["synthesized_data"],
            data_failures=case_finalized["data_failures"],
            data_confidence=case_finalized["data_confidence"],
        )
        await stream.append(
            "case_complete",
            {
                "case_result_id": str(case_row.id),
                "testcase_id": str(tc.id),
                "status": case_status,
                "data_confidence": case_finalized["data_confidence"],
                "duration_ms": case_duration,
                "tokens_used": case_tokens_used,
                "error_message": case_error,
            },
        )

        if case_status == "passed":
            outcome.passed += 1
        elif case_status == "failed":
            outcome.failed += 1
        elif case_status == "skipped":
            # Task 9.7：debug stop / timeout 被打断的当前用例算 skipped 而非
            # failed —— 它**没有**被测系统的 bug 凭据，纯属人工中断
            outcome.skipped += 1
        else:
            # error 都计入 failed 桶（与设计一致；data_confidence 用来排除
            # "数据问题导致的失败"）
            outcome.failed += 1

        if case_aborted_budget:
            return "budget"
        if case_user_stopped:
            return "stopped"
        if case_debug_timeout:
            return "debug_timeout"
        return None


    # ── Task 9.7：debug 模式 pause hook ─────────────────────────────

    async def _maybe_debug_pause(
        self,
        *,
        inputs: ExecutionInputs,
        stream: _ExecutionStream,
        case_row_id: uuid.UUID,
        step_number: int,
    ) -> str:
        """``mode="debug"`` 时在每步完成后阻塞等用户 ``POST /continue``。

        返回值：
        - ``"continue"``：用户推进，主循环继续
        - ``"stopped"``：用户调了 ``POST /stop``，主循环把当前用例标 skipped
          后退出
        - ``"timeout"``：``debug_timeout_seconds`` 内无信号，自动 stop
        """
        await stream.append(
            "step_paused",
            {
                "case_result_id": str(case_row_id),
                "step_number": step_number,
                "execution_id": str(inputs.execution_id),
                "timeout_seconds": self.deps.debug_timeout_seconds,
                "hint": "请调 POST /api/ui-executions/{id}/continue 推进下一步",
            },
        )

        async def _stop_check() -> bool:
            return await _check_stopped(self.deps, inputs.execution_id)

        try:
            outcome = await self.deps.debug_controller.wait_for_continue(
                inputs.execution_id,
                timeout=self.deps.debug_timeout_seconds,
                stop_check=_stop_check,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "debug_controller.wait_for_continue raised; treating as continue",
            )
            return "continue"

        # 不是 continue 的话发对应事件，让前端 UI 把 step_paused 关掉
        if outcome == "stopped":
            await stream.append(
                "debug_stopped",
                {
                    "execution_id": str(inputs.execution_id),
                    "case_result_id": str(case_row_id),
                    "step_number": step_number,
                },
            )
        elif outcome == "timeout":
            await stream.append(
                "debug_timeout_pending",
                {
                    "execution_id": str(inputs.execution_id),
                    "case_result_id": str(case_row_id),
                    "step_number": step_number,
                    "timeout_seconds": self.deps.debug_timeout_seconds,
                },
            )
        else:
            await stream.append(
                "step_resumed",
                {
                    "case_result_id": str(case_row_id),
                    "step_number": step_number,
                },
            )
        return outcome


# ─── helpers ─────────────────────────────────────────────────────────


def _serialize_tool_call(rec: ToolCallRecord) -> dict[str, Any]:
    return {
        "name": rec.name,
        "raw_name": rec.raw_name,
        "arguments": rec.arguments,
        "duration_ms": rec.duration_ms,
        "blocked": rec.blocked,
        "error": rec.error,
        "snapshot_chars": rec.snapshot_after_chars,
        "result": rec.result,
    }


def _build_config_snapshot(
    inputs: ExecutionInputs,
    *,
    configured_set_ids: Sequence[uuid.UUID] | None = None,
) -> dict[str, Any]:
    return {
        "testcase_ids": [str(x) for x in inputs.testcase_ids],
        "loaded_set_ids": [str(x) for x in inputs.loaded_set_ids],
        # 「本次显式配置」的物料集 id 列表（验收反馈：用于前端 snapshot
        # 面板做过滤、后端兼容老快照——值为 None / 缺省时按"全部展示"）
        "configured_set_ids": (
            [str(x) for x in configured_set_ids]
            if configured_set_ids is not None
            else None
        ),
        "manual_overrides": dict(inputs.manual_overrides or {}),
        "llm_config_id": str(inputs.llm_config_id) if inputs.llm_config_id else None,
        "token_budget_override": inputs.token_budget_override,
        "strict_data_mode": inputs.strict_data_mode,
        "mode": inputs.mode,
        "module_entry_overrides": {
            str(k): v for k, v in (inputs.module_entry_overrides or {}).items()
        },
    }


async def _collect_configured_set_ids(
    *,
    db: "AsyncSession",
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    loaded_set_ids: Sequence[uuid.UUID],
    testcase_ids: Sequence[uuid.UUID],
) -> list[uuid.UUID]:
    """汇总本次执行「显式配置」的物料集 id（保持插入顺序、去重）。

    包含来源：
    1. ``loaded_set_ids``——执行弹窗里勾选的物料集（最直接的"用户配置"）
    2. 当前环境的 ``default_data_set_ids``——环境层「默认加载」的物料集
    3. 用例的 ``default_data_set_ids``——用例层默认绑定的物料集

    **不**包含：personal scope / 普通 project scope 等"被动合并"的物料集。
    snapshot 用这个集合做过滤（serialize_for_audit），让用户只看到他主动
    配置/选中的明细，避免被项目里全部物料淹没（验收反馈）。

    DB 操作失败 / session stub 不支持 query 时，退化为只返回 ``loaded_set_ids``
    （单测会用 ``_FakeSessionContext`` 这种空 stub，不应阻塞 engine 主流程）。
    """
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []

    def _push(sid: Any) -> None:
        try:
            parsed = uuid.UUID(str(sid))
        except (ValueError, TypeError):
            return
        if parsed in seen:
            return
        seen.add(parsed)
        out.append(parsed)

    # 1. 弹窗勾选——纯内存去重，永远成功
    for sid in loaded_set_ids or []:
        _push(sid)

    # 2/3. DB 查询包在 try/except 里：单测的轻量 session stub 不支持 .execute()
    try:
        from sqlalchemy import select

        from app.modules.testcases.models import Testcase
        from app.modules.ui_automation.models import TestEnvironment

        if environment_id is not None:
            env_row = (
                await db.execute(
                    select(TestEnvironment).where(TestEnvironment.id == environment_id),
                )
            ).scalar_one_or_none()
            if env_row is not None:
                for sid in env_row.default_data_set_ids or []:
                    _push(sid)

        if testcase_ids:
            rows = (
                await db.execute(
                    select(Testcase.default_data_set_ids).where(
                        Testcase.id.in_(list(testcase_ids)),
                        Testcase.project_id == project_id,
                    ),
                )
            ).all()
            for (id_list,) in rows:
                for sid in id_list or []:
                    _push(sid)
    except (AttributeError, TypeError):  # pragma: no cover - 单测兜底
        # session stub / 单元测试场景：保留 loaded_set_ids 即可
        return out
    except Exception:  # pragma: no cover - 真实 DB 异常时不让 snapshot 把整个执行带挂
        logger.exception("_collect_configured_set_ids 查询失败，仅按 loaded_set_ids 返回")
        return out

    return out


def _extract_test_data_used(resolver: TestDataResolver) -> list[dict[str, Any]]:
    """合并后的 keys 列表 + synthetic 标记，作为本用例 test_data_used 落库。"""
    out: list[dict[str, Any]] = []
    for key, item in sorted(resolver.data.items()):
        out.append({
            "key": key,
            "value_type": item.value_type,
            "synthetic": item.synthetic_source is not None,
            "synthetic_source": item.synthetic_source,
        })
    return out


@dataclass
class _ResolverExecutionStub:
    """``TestDataResolver.build`` 期望的 ``ExecutionLike`` 字段子集。"""

    project_id: uuid.UUID
    environment_id: uuid.UUID | None
    triggered_by: uuid.UUID | None


@dataclass
class _LLMConfigProto:
    """``StepRunner`` 用的 LLMConfigLike 实体（已解密）。"""

    provider: str
    model: str
    api_key: str | None
    base_url: str | None
    temperature: float
    max_tokens: int


def _build_llm_proto(orm: "LLMConfig | None") -> _LLMConfigProto:
    if orm is None:
        # 走到这里说明 ``_load_llm_config`` 已 fallback 失败 —— 库里一条
        # LLMConfig 都没有。直接抛错让 ExecutionEngine 的 try/except 把整
        # 个 execution 标记为 failed 并写入 error_message，避免用 hardcoded
        # 假配置去打 OpenAI 收 401。
        raise ValueError(
            "未配置任何 LLM；请先到「系统设置 → LLM 配置」创建并设为默认，"
            "或在执行时显式指定 LLM 配置"
        )
    api_key = (
        decrypt(orm.api_key_encrypted) if getattr(orm, "api_key_encrypted", None) else None
    )
    return _LLMConfigProto(
        provider=orm.provider,
        model=orm.model,
        api_key=api_key,
        base_url=getattr(orm, "base_url", None),
        temperature=getattr(orm, "temperature", 0.0) or 0.0,
        max_tokens=getattr(orm, "max_tokens", 2048) or 2048,
    )


async def _open_bundle(
    deps: EngineDeps,
    env: Any,
    execution_id: uuid.UUID,
) -> _BundleLike:
    if deps.open_browser_bundle is not None:
        return await deps.open_browser_bundle(env, execution_id)
    return await _default_open_bundle(env, execution_id)


async def _default_run_preconditions(
    bundle: Any,
    environment: Any,
    llm_config_orm: Any,
) -> list[dict[str, Any]]:
    """生产默认实现：把环境的 ``preconditions`` 列表按 order_index 跑一遍。

    设计要点：
    - 仅 ``ai_login`` / ``state_inject`` 需要 LLM；缺 LLM 时这两类会自动走
      stub 报错，其他类型（``scripted_steps`` / ``cookie_inject``）无影响。
    - 任一模板失败立刻 break；调用方再决定是否中断后续用例（在
      ``_run_inner`` 里我们选择中断）。
    - 截图只在试跑端点返回 base64；这里 ``capture_screenshot=False`` 节省
      payload，本来 ``preconditions_complete`` 事件也不渲染图。
    - state_target 和 credential 解密参考 ``service.test_precondition`` 的
      做法，但不依赖 service 层（避免循环依赖）。
    """
    from app.core.crypto import decrypt
    from app.modules.ui_automation import state_manager
    from app.modules.ui_automation.ai_login_runner import build_ai_login_runner
    from app.modules.ui_automation.precondition_executor import run_precondition

    raw_templates = getattr(environment, "preconditions", None) or []
    templates = sorted(
        [pt for pt in raw_templates if getattr(pt, "enabled", True)],
        key=lambda t: getattr(t, "order_index", 0),
    )
    if not templates:
        return []

    ai_login_runner = build_ai_login_runner(
        llm_config_orm=llm_config_orm,
        environment=environment,
        budget_limit=getattr(environment, "token_budget", 50_000),
    )

    results: list[dict[str, Any]] = []
    for pt in templates:
        creds: dict[str, Any] | None = None
        if pt.credentials_encrypted:
            try:
                creds = json.loads(decrypt(pt.credentials_encrypted))
            except Exception:  # noqa: BLE001
                logger.exception("decrypt precondition credentials failed: %s", pt.id)
                creds = None

        state_target = state_manager.state_path_for(
            environment.id, session_name=environment.session_name,
        )

        async def _on_state_saved(_p: Any) -> None:
            return None  # 持久化由 service.precondition 端点专管，这里只读

        async def _on_state_invalidated() -> None:
            return None

        try:
            result = await run_precondition(
                bundle, pt,
                base_url=environment.base_url,
                state_target=state_target,
                credentials=creds,
                on_state_saved=_on_state_saved,
                on_state_invalidated=_on_state_invalidated,
                ai_login_runner=ai_login_runner,
                capture_screenshot=False,
                save_state_on_success=True,
                # 与试跑端点保持一致：AI 登录的瓶颈在 LLM inference（每轮
                # 30-60s），10 步 ≈ 300-600s。300s 是中等慢度模型下的合理
                # 默认值；scripted/cookie 类型会自然提前完成，超时无副作用。
                per_template_timeout_seconds=300.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_precondition crashed: %s", pt.id)
            results.append({
                "template_id": str(pt.id),
                "name": pt.name,
                "type": pt.type,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "error_kind": "browser_error",
                "elapsed_ms": 0,
                "logs": [],
            })
            break

        results.append({
            "template_id": str(result.template_id),
            "name": result.template_name,
            "type": result.type,
            "success": result.success,
            "error": result.error,
            "error_kind": result.error_kind,
            "fell_back_to": result.fell_back_to,
            "elapsed_ms": result.elapsed_ms,
            "state_was_loaded": result.state_was_loaded,
            "state_was_stale": result.state_was_stale,
            "state_was_saved": result.state_was_saved,
            "logs": list(result.logs),
        })
        if not result.success:
            break

    return results


async def _check_stopped(deps: EngineDeps, execution_id: uuid.UUID) -> bool:
    fn = getattr(deps.persistence, "is_execution_stopped", None)
    if fn is None:
        return False
    try:
        return bool(await fn(execution_id))
    except Exception:  # noqa: BLE001
        return False


# ─── DB load helpers ─────────────────────────────────────────────────


async def _load_environment(
    db: "AsyncSession", environment_id: uuid.UUID | None,
) -> "TestEnvironment":
    """加载 environment；允许 environment_id=None 时构造一个最小 stub。"""
    if environment_id is None:
        return _MinimalEnvStub()  # type: ignore[return-value]
    from sqlalchemy import select

    from app.modules.ui_automation.models import TestEnvironment

    row = (
        await db.execute(select(TestEnvironment).where(TestEnvironment.id == environment_id))
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"environment {environment_id} not found")
    return row


@dataclass
class _MinimalEnvStub:
    """没有指定 environment 时给 SecurityGuard / Bundle 一个最低门槛配置。

    设计取舍：UI 测试理论上必须依附 environment 才有意义；这里只是为了兼
    容"环境未配置但用户通过 chat 触发"的边角场景，让流程不至于卡在 None。
    """

    base_url: str = "about:blank"
    allowed_hosts: list[str] = field(default_factory=list)
    token_budget: int = 50_000
    enable_browser_evaluate: bool = False
    headless: bool = True


async def _load_llm_config(
    db: "AsyncSession", llm_config_id: uuid.UUID | None,
) -> "LLMConfig | None":
    """加载 LLM 配置；``llm_config_id=None`` 时回落到默认配置。

    回落优先级：
    1. ``llm_config_id`` 指定的具体配置（找不到 → ``ValueError``）
    2. ``is_default=True`` 的 LLMConfig（最多一条）
    3. 库里第一条 LLMConfig（兜底，按 created_at 排序保证确定性）

    全部为空 → 返回 None；上层 ``_build_llm_proto`` 会抛错并把执行标记为
    failed，让用户在 ExecutionDetail 上看到明确原因（比"用 OpenAI 假 key
    打 401"友好得多）。
    """
    from sqlalchemy import asc, select

    from app.modules.llm.models import LLMConfig

    if llm_config_id is not None:
        row = (
            await db.execute(select(LLMConfig).where(LLMConfig.id == llm_config_id))
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"LLM 配置 {llm_config_id} 不存在")
        return row

    default_row = (
        await db.execute(
            select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
        )
    ).scalar_one_or_none()
    if default_row is not None:
        return default_row

    return (
        await db.execute(select(LLMConfig).order_by(asc(LLMConfig.created_at)).limit(1))
    ).scalar_one_or_none()


async def _load_testcases(
    db: "AsyncSession", testcase_ids: Sequence[uuid.UUID],
) -> list["Testcase"]:
    if not testcase_ids:
        return []
    from sqlalchemy import select

    from app.modules.testcases.models import Testcase

    rows = (
        await db.execute(select(Testcase).where(Testcase.id.in_(list(testcase_ids))))
    ).scalars().all()
    # 保持 inputs 顺序
    by_id = {r.id: r for r in rows}
    ordered: list[Testcase] = []
    for tid in testcase_ids:
        row = by_id.get(tid)
        if row is not None:
            ordered.append(row)
    return ordered


async def _load_module_entry_paths(
    db: "AsyncSession",
    module_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, str | None]:
    """一次性把本批用例涉及的所有 module 的 entry_path 拉出来。

    返回 ``{module_id: entry_path | None}``。无 module_id 的用例自然不查；
    查不到（用例所属 module 已被删）也不报错——结果里就是缺这个 key。
    """
    cleaned = list({mid for mid in module_ids if mid is not None})
    if not cleaned:
        return {}
    from sqlalchemy import select

    from app.modules.testcases.models import TestcaseModule

    rows = (
        await db.execute(
            select(TestcaseModule.id, TestcaseModule.entry_path)
            .where(TestcaseModule.id.in_(cleaned))
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _resolve_target_url(
    *,
    tc: "Testcase",
    environment: Any,
    module_entry_map: dict[uuid.UUID, str | None],
    module_entry_overrides: dict[uuid.UUID, str],
) -> str | None:
    """计算单条用例的 target_url（用于注入 step_runner prompt）。

    优先级：``module_entry_overrides[module_id]`` → ``module.entry_path`` → None

    拼接规则：
    - 入口是绝对 URL（``http://`` / ``https://``）→ 原样使用
    - 入口是相对路径（``/admin/users``）→ 拼到 ``environment.base_url`` 上
    - 入口为空串 / None → 返回 None（prompt 不展示 target_url 块）

    None 含义：让 AI prompt 里没有 target_url 字段，行为退回现状（依赖
    用例 step 的自然语言指令决定目标地址）。这是向后兼容的 fallback。
    """
    module_id = getattr(tc, "module_id", None)
    if module_id is None:
        return None

    raw_entry: str | None = None
    if module_id in module_entry_overrides:
        # 显式空串：表示"本次跑该模块时不附带 entry_path"
        ov = (module_entry_overrides[module_id] or "").strip()
        raw_entry = ov or None
    else:
        raw_entry = module_entry_map.get(module_id)
        if raw_entry is not None:
            raw_entry = raw_entry.strip() or None

    if not raw_entry:
        return None

    # 完整 URL（含 scheme）→ 直接用
    if raw_entry.startswith(("http://", "https://")):
        return raw_entry

    base_url = (getattr(environment, "base_url", "") or "").rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/{raw_entry.lstrip('/')}"


# 让 lint 不报"unused import"（用于 type checking 即可）
_ = MissingDataAlert
_ = StepRunResult


__all__ = [
    "EngineDeps",
    "ExecutionEngine",
    "ExecutionInputs",
    "ExecutionOutcome",
]

