"""StepRunner — 单步骤执行单元（Task 9.4）。

复刻一期 ``chat_service._handle_chat_stream`` 的 tool-calling 循环骨架：

1. 组 system / user prompt（含裁剪后 snapshot + 物料清单 ``data_manifest``）
2. tools = MCP browser_* 工具（来自 ``BrowserBundle.register_mcp_tools_for_agent``）
   + 可选 ``platform_*`` 物料工具（来自 ``data_resolver``）
3. for iter in range(MAX_STEP_TOOL_ITERATIONS):
    a. 调一轮 LLM（默认 ``stream_chat`` + chunk 累积；测试可注入 ``chat_round_fn``）
    b. budget.add(usage_total)；over_limit → BudgetExceededError
    c. 本轮没产生 tool_calls 则跳出（不依赖 finish_reason —— GLM 等
       gateway 在带 tool_calls 时仍会给出 ``finish_reason="stop"``）
    d. 每个 tool_call 走 ``SecurityGuard.check`` → ``run_tool`` → 把结果塞
       回 messages（tool 角色）→ 解析 snapshot 喂给 ``snapshot_clipper``
4. 返回 ``StepRunResult``（成功 / 失败 + 工具序列 + reasoning + tokens）

设计要点：
- **不**直接判定"步骤通过 / 失败"——这是 ``AssertionJudge`` 的职责
- 任何 secret 工具的 result（带 ``_test_data_secret_used``）都不进 reasoning
  日志，只留 ``<secret used>`` 占位
- ``LLMConfigLike`` Protocol 让 Engine 可以传 ORM 实例 / dataclass / 测试桩；
  本模块不依赖 LLMConfig 表
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.modules.llm.agent_tools import run_tool as _default_run_tool
from app.modules.llm.providers import stream_chat
from app.modules.ui_automation.data_platform_tools import (
    platform_tools_openai_schemas,
    redact_tool_result_for_reasoning,
)
from app.modules.ui_automation.prompts.step_runner_system import (
    build_step_system_prompt,
    build_step_user_message,
)
from app.modules.ui_automation.security import (
    BudgetExceededError,
    SecurityError,
    SecurityGuard,
    TokenBudget,
)
from app.modules.ui_automation.snapshot_clipper import (
    MAX_SNAPSHOT_CHARS,
    ClippedSnapshot,
    RefCache,
    clip_for_llm,
)

if TYPE_CHECKING:
    from app.modules.ui_automation.security import EnvironmentLike
    from app.modules.ui_automation.test_data_resolver import TestDataResolver


logger = logging.getLogger(__name__)


# 单步骤内 LLM **轮次**上限（每轮 = 一次 ``stream_chat``；一轮里可含多个 tool_calls）。
# 最后一轮强制 ``tool_choice="none"``，只产出文字总结，不占「再调一次 snapshot」的名额。
#
# 历史 5 → 8 → 12：
# - 5：连「数据兜底 + 重试」都不够；
# - 8：modal 场景常见「点确定 → 再等一轮 browser_snapshot 看清结果」时被末轮
#   总结截断（用户看到 reasoning 里「已到工具调用上限」但仍未拿到点击后快照）；
# - 12：在仍由 ``TokenBudget`` 防失控的前提下，多给约 4 轮纯工具空间，覆盖
#   导航 / 多段输入 / 一次物料 fallback / 提交 / **提交后再 snapshot**。
MAX_STEP_TOOL_ITERATIONS = 12


# ─── Public types ────────────────────────────────────────────────────


@runtime_checkable
class LLMConfigLike(Protocol):
    """StepRunner 调用 LLM 的最小契约。"""

    provider: str
    model: str
    temperature: float
    max_tokens: int
    base_url: str | None
    api_key: str | None
    """**已解密**的明文 api key；上层在传入前应调 ``crypto.decrypt``。"""


@runtime_checkable
class _BundleLike(Protocol):
    """StepRunner 只读 bundle 的少量字段，剩下的交给 SecurityGuard / tool 自行调度。"""

    execution_id: uuid.UUID


@dataclass
class ToolCallEmit:
    """模型本轮发起的一次工具调用（尚未执行）。"""

    id: str
    name: str
    arguments_json: str


@dataclass
class ChatRound:
    """一轮 LLM 调用的累积结果（不含 SSE）。"""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCallEmit] = field(default_factory=list)
    finish_reason: str | None = None
    usage_total: int = 0


ChatRoundFn = Callable[..., Awaitable[ChatRound]]
ToolRunner = Callable[[str, str], Awaitable[str]]


@dataclass
class ToolCallRecord:
    """已执行（或被拦截）的 tool_call 记录。"""

    name: str
    raw_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    duration_ms: int = 0
    blocked: bool = False
    error: str | None = None
    snapshot_after_text: str | None = None
    snapshot_after_chars: int = 0


@dataclass
class StepRunResult:
    """StepRunner 输出。``success=True`` 表示循环正常收尾；步骤通过 / 失败由
    ``AssertionJudge`` 判定。"""

    success: bool
    iterations: int
    tokens_used: int
    reasoning: str
    final_message: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    last_snapshot_text: str | None = None
    last_clipped: ClippedSnapshot | None = None
    error: str | None = None
    error_kind: str | None = None
    """``budget_exceeded`` / ``security_blocked`` / ``tool_failed`` / ``model_error`` /
    ``max_iterations`` / ``llm_error`` 之一；正常返回为 None。"""


# ─── default_chat_round：基于一期 stream_chat 的实现 ─────────────────


async def default_chat_round(
    *,
    llm: LLMConfigLike,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    tool_choice: str | dict | None = None,
) -> ChatRound:
    """流式跑一轮 chat completion，把 chunks 累积成 ChatRound。"""
    out = ChatRound()
    pending: dict[int, dict[str, Any]] = {}
    last_chunk = None
    try:
        async for chunk in stream_chat(
            provider=llm.provider,
            model=llm.model,
            messages=messages,
            api_key=llm.api_key,
            base_url=llm.base_url,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        ):
            last_chunk = chunk
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            piece_reason = getattr(delta, "reasoning_content", None)
            if piece_reason:
                out.reasoning += piece_reason
            piece_text = getattr(delta, "content", None)
            if piece_text:
                out.content += piece_text

            for tc in (delta.tool_calls or []):
                slot = pending.setdefault(
                    tc.index,
                    {"id": None, "name": "", "arguments": ""},
                )
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["arguments"] += fn.arguments

            if choice.finish_reason:
                out.finish_reason = choice.finish_reason
    except Exception as exc:  # noqa: BLE001
        logger.exception("default_chat_round LLM call failed")
        out.finish_reason = out.finish_reason or "error"
        out.content = out.content or f"[LLM ERROR] {type(exc).__name__}: {exc}"
        out.tool_calls = []
        return out

    if last_chunk is not None and getattr(last_chunk, "usage", None):
        usage_total = getattr(last_chunk.usage, "total_tokens", None)
        if usage_total:
            out.usage_total = int(usage_total)

    for idx in sorted(pending):
        item = pending[idx]
        out.tool_calls.append(
            ToolCallEmit(
                id=item["id"] or f"call_{idx}",
                name=item["name"],
                arguments_json=item["arguments"] or "{}",
            ),
        )
    return out


# ─── helpers ─────────────────────────────────────────────────────────


def _parse_args(arguments_json: str) -> dict[str, Any]:
    if not arguments_json:
        return {}
    try:
        parsed = json.loads(arguments_json)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _strip_namespace(name: str) -> str:
    # 命名空间分隔符为 ``__``（详见 ``data_platform_tools._tool_name`` /
    # ``mcp_bridge.MCPBridge.register_into_agent_tools``）。兼容旧记录里
    # 可能存在的 ``:`` 前缀（早期 namespaced tool 持久化在 ai_review.tool_calls /
    # state_snapshot 等地方），所以两种都剥。
    if "__" in name:
        return name.rsplit("__", 1)[-1]
    if ":" in name:
        return name.rsplit(":", 1)[-1]
    return name


def _extract_snapshot_text(result: dict[str, Any]) -> str | None:
    """从 MCP tool 返回里抽 snapshot 文本（兼容多种字段命名）。"""
    if not isinstance(result, dict):
        return None
    for key in ("snapshot", "text", "ariaSnapshot", "aria_snapshot"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val
    content = result.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return None


def _is_secret_tool_result(result: dict[str, Any]) -> bool:
    return isinstance(result, dict) and bool(result.get("_test_data_secret_used"))


# 视为"不会改变页面 a11y 状态"的只读 / 元信息工具集合 —— 这些工具调用之后**不**
# 需要 auto-finalize browser_snapshot（因为它们的 tool result 里通常已经带 a11y
# 文本，或者它们本就是查询性而无 mutation）。
#
# 反之，``browser_navigate`` / ``browser_type`` / ``browser_click`` / ``browser_fill_form``
# 这类**有副作用**的工具调用之后，playwright-mcp 0.x 默认**不**返回 inline 的 a11y
# 树（只给一行 ``[Snapshot](.playwright-mcp/page-...yml)`` 文件链接），所以必须由
# StepRunner 自己强制再调一次 ``browser_snapshot`` 兜底，否则后续 ``AssertionJudge``
# 拿到的 ``last_snapshot_text`` 是操作**之前**的状态——典型表现：
# "在 X 文本框输入 9999" 通过了，但断言阶段说"快照里看不到 9999"。
_NON_MUTATING_TOOLS: frozenset[str] = frozenset({
    "browser_snapshot",
    "browser_take_screenshot", "browser_screenshot",
    "browser_console_messages", "browser_network_requests",
    "browser_tabs", "browser_tabs_list",
    # platform_* 系列（凭据 / 物料拉取等）也不影响页面，不需要 finalize
    "platform_get_secret", "platform_solve_captcha",
})


def _is_mutating_tool(raw_name: str) -> bool:
    """判断 tool_call 是否是 mutation 类（需要在循环退出前 auto-finalize a11y）。"""
    if not raw_name:
        return False
    if raw_name in _NON_MUTATING_TOOLS:
        return False
    # 兜底白名单：MCP / platform 工具新版本可能加新名字，只要不是 known non-mutating
    # 都按 mutating 处理。代价：偶尔多一次冗余 snapshot，比"漏 finalize"代价低得多。
    return True


# ─── StepRunner ──────────────────────────────────────────────────────


class StepRunner:
    """复用一期 agent tool-calling 循环跑单步骤；不判断步骤通过 / 失败。"""

    __test__ = False

    def __init__(
        self,
        *,
        llm: LLMConfigLike,
        environment: EnvironmentLike,
        budget: TokenBudget,
        execution_id: uuid.UUID | str | None = None,
        chat_round_fn: ChatRoundFn | None = None,
        tool_runner: ToolRunner | None = None,
        ref_cache: RefCache | None = None,
        max_iterations: int | None = None,
    ) -> None:
        self.llm = llm
        self.environment = environment
        self.budget = budget
        self.execution_id = str(execution_id) if execution_id is not None else None
        self._chat_round = chat_round_fn or self._default_chat_round_default
        self._tool_runner = tool_runner or _default_run_tool
        self.ref_cache = ref_cache or RefCache()
        self.max_iterations = max_iterations or MAX_STEP_TOOL_ITERATIONS
        self._guard = SecurityGuard(environment=environment, budget=budget)

    async def _default_chat_round_default(self, **kw: Any) -> ChatRound:
        return await default_chat_round(llm=self.llm, **kw)

    async def run_one(
        self,
        *,
        step_description: str,
        expected: str | None = None,
        bundle: _BundleLike | None = None,
        data_manifest: str = "",
        data_resolver: TestDataResolver | None = None,
        prev_snapshot: str | None = None,
        focus_hint: str | None = None,
        mcp_tool_specs: list[dict[str, Any]] | None = None,
        current_url: str = "(未知)",
        page_title: str = "(未知)",
        initial_snapshot_text: str | None = None,
        target_url: str | None = None,
    ) -> StepRunResult:
        """执行单条步骤。失败不抛错，把状态写入 ``StepRunResult.error_kind``。"""
        execution_id = self._resolve_execution_id(bundle)

        clipped_initial: ClippedSnapshot | None = None
        if initial_snapshot_text:
            clipped_initial = clip_for_llm(
                initial_snapshot_text,
                prev_snapshot=prev_snapshot,
                max_chars=MAX_SNAPSHOT_CHARS,
                focus_hint=focus_hint,
            )
            self.ref_cache.update(clipped_initial.text)

        system_prompt = build_step_system_prompt(
            step_description=step_description,
            expected=expected,
            current_url=current_url,
            page_title=page_title,
            snapshot_block=(clipped_initial.text if clipped_initial else ""),
            data_manifest=data_manifest,
            target_url=target_url,
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_step_user_message(step_description, expected=expected)},
        ]

        tools = self._build_tools(execution_id, mcp_tool_specs, data_resolver)

        last_snapshot_text: str | None = (clipped_initial.text if clipped_initial else None)
        last_clipped: ClippedSnapshot | None = clipped_initial
        full_reasoning = ""
        full_content = ""
        tool_calls: list[ToolCallRecord] = []
        iterations = 0

        for iteration in range(self.max_iterations):
            iterations = iteration + 1

            # 进入下一轮 LLM 之前先做预算守卫 —— 已超的话直接终止
            if self.budget.over_limit:
                return StepRunResult(
                    success=False,
                    iterations=iteration,
                    tokens_used=self.budget.consumed,
                    reasoning=full_reasoning,
                    final_message=full_content,
                    tool_calls=tool_calls,
                    last_snapshot_text=last_snapshot_text,
                    last_clipped=last_clipped,
                    error=(
                        f"已超过 token 预算 {self.budget.limit:,}（消耗 {self.budget.consumed:,}）"
                    ),
                    error_kind="budget_exceeded",
                )

            is_last = iteration == self.max_iterations - 1
            tool_choice: str | None = None
            if is_last and iteration > 0:
                tool_choice = "none"
                # 最后一轮工具被强制关掉，给 AI 一个明确收尾指令；不限制长度，
                # 让推理模型把判断过程交代清楚（早期版本写"用一句话"会截断 reasoning，
                # 推理模型典型表现：reasoning_content 写满 max_tokens 后 final content
                # 截空，AssertionJudge 拿到空快照判失败 —— 二期验收 #f6513ebb 类）
                messages.append({
                    "role": "user",
                    "content": (
                        "本步骤的单步 LLM 轮次已达安全上限（防无限循环）；本轮不能再调用工具。\n"
                        "请基于**已经返回的**快照与工具结果用中文自然语言总结：\n"
                        "1) 你完成了哪些关键操作；\n"
                        "2) 就现有信息看，页面可能处于什么状态（若最后一轮操作后尚未来得及"
                        "快照，请明说，并指出哪些证据仍来自点击/提交**之前**的快照）；\n"
                        "3) 若曾按「数据使用与兜底原则」替换过占位数据，说明依据与所用物料 key。\n"
                        "推理写充分即可。步骤是否通过与期望比对由断言评判结合最新快照再判定。"
                    ),
                })

            try:
                round_out: ChatRound = await self._chat_round(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except (SecurityError, BudgetExceededError) as exc:
                return StepRunResult(
                    success=False,
                    iterations=iterations,
                    tokens_used=self.budget.consumed,
                    reasoning=full_reasoning,
                    final_message=full_content,
                    tool_calls=tool_calls,
                    last_snapshot_text=last_snapshot_text,
                    last_clipped=last_clipped,
                    error=str(exc),
                    error_kind=(
                        "budget_exceeded"
                        if isinstance(exc, BudgetExceededError)
                        else "security_blocked"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("StepRunner LLM round failed")
                return StepRunResult(
                    success=False,
                    iterations=iterations,
                    tokens_used=self.budget.consumed,
                    reasoning=full_reasoning,
                    final_message=full_content,
                    tool_calls=tool_calls,
                    last_snapshot_text=last_snapshot_text,
                    last_clipped=last_clipped,
                    error=f"{type(exc).__name__}: {exc}",
                    error_kind="llm_error",
                )

            full_reasoning += round_out.reasoning
            full_content += round_out.content
            self.budget.add(round_out.usage_total)

            if self.budget.over_limit:
                return StepRunResult(
                    success=False,
                    iterations=iterations,
                    tokens_used=self.budget.consumed,
                    reasoning=full_reasoning,
                    final_message=full_content,
                    tool_calls=tool_calls,
                    last_snapshot_text=last_snapshot_text,
                    last_clipped=last_clipped,
                    error=(
                        f"已超过 token 预算 {self.budget.limit:,}（消耗 {self.budget.consumed:,}）"
                    ),
                    error_kind="budget_exceeded",
                )

            if is_last:
                # 最后一轮强制不再 tool_call，直接收尾
                break

            # 终止条件：本轮模型没产生任何 tool_call。
            # 注意 ⚠️ 不能只看 ``finish_reason == "tool_calls"`` —— GLM (火山方舟) 等
            # 部分 OpenAI-compat 网关在带 tool_calls 的回复里仍然把 finish_reason
            # 标成 ``"stop"``，按 OpenAI 标准是 bug，但这是事实部署。我们以
            # ``tool_calls`` 是否为空作为唯一判据，更宽容也更稳。
            if not round_out.tool_calls:
                break

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": round_out.content or None,
                "tool_calls": [
                    {
                        "id": emit.id,
                        "type": "function",
                        "function": {"name": emit.name, "arguments": emit.arguments_json},
                    }
                    for emit in round_out.tool_calls
                ],
            }
            # 思维链回填：火山方舟 / 智谱 GLM 的 thinking 模式契约 ——
            # 模型返回的 ``reasoning_content`` 必须随下一轮 assistant message 一并回传，
            # 否则下一轮请求会 400 ``The reasoning_content in the thinking mode must
            # be passed back to the API``。OpenAI 标准 chat.completions 接口会忽略
            # 未识别字段，所以无脑回传对其它 provider 安全。
            if round_out.reasoning:
                assistant_msg["reasoning_content"] = round_out.reasoning
            messages.append(assistant_msg)

            for emit in round_out.tool_calls:
                rec, snapshot_for_next = await self._invoke_tool(emit, prev_snapshot=last_snapshot_text)
                tool_calls.append(rec)
                # 工具结果（脱敏后）回填到 messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": emit.id,
                    "content": json.dumps(
                        redact_tool_result_for_reasoning(emit.name, rec.result),
                        ensure_ascii=False,
                    ),
                })
                if rec.blocked and isinstance(rec.error, str):
                    # 安全拦截：不一定终止整个 step，让模型看到拒绝理由后选择放弃 / 改路径
                    pass
                if snapshot_for_next is not None:
                    last_snapshot_text = snapshot_for_next.text
                    last_clipped = snapshot_for_next

        # 退出循环：is_last 已强制 tool_choice="none"，正常拿到最终回答；
        # 标记 success=True，是否真"通过"由 AssertionJudge 二次判定。
        # 在 return 前 auto-finalize 一次 a11y 快照——保证 AssertionJudge 看到
        # 的是"操作之后"的页面，而不是"操作之前"。详见 _auto_finalize_snapshot
        # docstring（修复 #f6513ebb 案例：browser_type 后没 snapshot 导致断言
        # 总是说"快照里看不到刚输入的值"）。
        last_snapshot_text, last_clipped = await self._auto_finalize_snapshot(
            execution_id=execution_id,
            tool_calls=tool_calls,
            last_snapshot_text=last_snapshot_text,
            last_clipped=last_clipped,
        )

        return StepRunResult(
            success=True,
            iterations=iterations,
            tokens_used=self.budget.consumed,
            reasoning=full_reasoning,
            final_message=full_content.strip(),
            tool_calls=tool_calls,
            last_snapshot_text=last_snapshot_text,
            last_clipped=last_clipped,
            error=None,
            error_kind=None,
        )

    # ── internal ─────────────────────────────────────────────────

    async def _auto_finalize_snapshot(
        self,
        *,
        execution_id: str | None,
        tool_calls: list[ToolCallRecord],
        last_snapshot_text: str | None,
        last_clipped: ClippedSnapshot | None,
    ) -> tuple[str | None, ClippedSnapshot | None]:
        """循环退出前的 a11y 快照兜底刷新。

        触发条件（同时满足）：
        1. ``execution_id`` 已知（能拼 ``<exec_id>__browser_snapshot`` namespaced 名）
        2. 至少有过一次 tool_call（说明是真用了 MCP，不是空 step）
        3. 最后一次 tool_call 的 raw_name 是 mutating 类（见 ``_is_mutating_tool``）

        为什么必须做：playwright-mcp 0.x 在 ``browser_navigate / type / click / fill``
        这类副作用工具的 result 里**不**内联 a11y 树（只给文件链接），导致
        ``last_snapshot_text`` 一直停留在最后一次 ``browser_snapshot`` 的状态——
        如果 AI 在 type/click 之后没自觉再调 snapshot，断言阶段拿到的就是操作**前**
        的快照，文本搜索 / LLM 兜底都看不到刚刚输入 / 点击的产物，假阳性失败。

        失败容忍：tool_runner 抛错 / mcp_unavailable / 工具未注册 → INFO log，
        返回原 ``last_snapshot_text`` 不抛错，让步骤继续按原状态走断言。
        """
        if not execution_id:
            return last_snapshot_text, last_clipped
        if not tool_calls:
            return last_snapshot_text, last_clipped
        if not _is_mutating_tool(tool_calls[-1].raw_name):
            return last_snapshot_text, last_clipped

        ns_tool = f"{execution_id}__browser_snapshot"
        started = time.monotonic()
        try:
            raw_result = await self._tool_runner(ns_tool, "{}")
        except Exception as exc:  # noqa: BLE001
            # 不阻塞 step：兜底失败的最常见原因是 mcp_unavailable / 工具未在
            # 该 execution 注册（典型 mock 测试场景），交给 AssertionJudge 用
            # 既有 last_snapshot_text 走原流程。
            logger.info(
                "StepRunner auto-finalize browser_snapshot failed (%s); "
                "assertion will use the previous snapshot",
                f"{type(exc).__name__}: {exc}",
            )
            return last_snapshot_text, last_clipped
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            parsed = json.loads(raw_result) if raw_result else {}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
        except json.JSONDecodeError:
            parsed = {"raw": raw_result}

        snap_text = _extract_snapshot_text(parsed)
        if not snap_text:
            # MCP 返回了但里面没 a11y 文本（罕见）：保持原状态
            return last_snapshot_text, last_clipped

        clipped = clip_for_llm(
            snap_text,
            prev_snapshot=last_snapshot_text,
            max_chars=MAX_SNAPSHOT_CHARS,
            focus_hint=None,
        )
        self.ref_cache.update(clipped.text)

        # 把 auto-finalize 这次调用也记录进 tool_calls，方便前端审计 / 回放时
        # 看清"哦，最后一次 snapshot 是平台兜底刷的，不是模型主动调的"。
        tool_calls.append(
            ToolCallRecord(
                name=ns_tool,
                raw_name="browser_snapshot",
                arguments={"_auto_finalize": True},
                result=parsed,
                duration_ms=duration_ms,
                blocked=False,
                error=None,
                snapshot_after_text=clipped.text,
                snapshot_after_chars=clipped.clipped_chars,
            ),
        )
        return clipped.text, clipped

    def _resolve_execution_id(self, bundle: _BundleLike | None) -> str | None:
        if self.execution_id:
            return self.execution_id
        if bundle is not None and getattr(bundle, "execution_id", None) is not None:
            return str(bundle.execution_id)
        return None

    def _build_tools(
        self,
        execution_id: str | None,
        mcp_specs: list[dict[str, Any]] | None,
        data_resolver: TestDataResolver | None,
    ) -> list[dict[str, Any]] | None:
        merged: list[dict[str, Any]] = []
        merged.extend(mcp_specs or [])
        if data_resolver is not None:
            ns = execution_id or "default"
            merged.extend(platform_tools_openai_schemas(execution_id=ns))
        return merged or None

    async def _invoke_tool(
        self,
        emit: ToolCallEmit,
        *,
        prev_snapshot: str | None,
    ) -> tuple[ToolCallRecord, ClippedSnapshot | None]:
        args = _parse_args(emit.arguments_json)
        raw_name = _strip_namespace(emit.name)

        # 1) SecurityGuard：白名单 / 域名 / 预算
        try:
            self._guard.check(emit.name, args)
        except (SecurityError, BudgetExceededError) as exc:
            error_kind = (
                "budget_exceeded" if isinstance(exc, BudgetExceededError) else "security"
            )
            err_payload = {
                "error": str(exc),
                "error_kind": error_kind,
                "blocked_by_security": True,
            }
            return (
                ToolCallRecord(
                    name=emit.name,
                    raw_name=raw_name,
                    arguments=args,
                    result=err_payload,
                    duration_ms=0,
                    blocked=True,
                    error=str(exc),
                ),
                None,
            )

        started = time.monotonic()
        try:
            raw_result = await self._tool_runner(emit.name, emit.arguments_json or "{}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("StepRunner tool execution failed: %s", emit.name)
            return (
                ToolCallRecord(
                    name=emit.name,
                    raw_name=raw_name,
                    arguments=args,
                    result={"error": f"{type(exc).__name__}: {exc}"},
                    duration_ms=int((time.monotonic() - started) * 1000),
                    blocked=False,
                    error=str(exc),
                ),
                None,
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            parsed_result = json.loads(raw_result) if raw_result else {}
            if not isinstance(parsed_result, dict):
                parsed_result = {"value": parsed_result}
        except json.JSONDecodeError:
            parsed_result = {"raw": raw_result}

        # secret 工具：result 不会进 reasoning，由 redact 在写入 messages 时再处理
        if _is_secret_tool_result(parsed_result):
            logger.debug(
                "StepRunner secret tool used: %s (plaintext omitted from reasoning)",
                emit.name,
            )

        snap_text = _extract_snapshot_text(parsed_result)
        clipped: ClippedSnapshot | None = None
        if snap_text:
            clipped = clip_for_llm(
                snap_text,
                prev_snapshot=prev_snapshot,
                max_chars=MAX_SNAPSHOT_CHARS,
                focus_hint=None,
            )
            self.ref_cache.update(clipped.text)

        return (
            ToolCallRecord(
                name=emit.name,
                raw_name=raw_name,
                arguments=args,
                result=parsed_result,
                duration_ms=duration_ms,
                blocked=False,
                error=None,
                snapshot_after_text=clipped.text if clipped else None,
                snapshot_after_chars=clipped.clipped_chars if clipped else 0,
            ),
            clipped,
        )


__all__ = [
    "MAX_STEP_TOOL_ITERATIONS",
    "ChatRound",
    "ChatRoundFn",
    "LLMConfigLike",
    "StepRunResult",
    "StepRunner",
    "ToolCallEmit",
    "ToolCallRecord",
    "default_chat_round",
]
