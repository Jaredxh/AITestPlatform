"""MCPBridge — 把 MCP server 暴露的 tools 桥接到一期 ``TOOL_REGISTRY``。

设计目标（对应 Task 7.2）：
1. **复用一期 agent tool-calling**：MCP 工具最终都包装成 ``ToolFn``（``async (dict) -> dict``）
   注册到 ``app.modules.llm.agent_tools.TOOL_REGISTRY``，模型那边和调用 ``web_search``
   完全同一套机制，零侵入。
2. **命名空间隔离**：``<execution_id>:browser_navigate`` 这种前缀，让多个并发
   execution 不会互相污染对方的工具表，结束时一行 ``unregister_namespace``
   就能清干净。
3. **进程生命周期管理**：MCP server 是子进程（典型 ``npx @playwright/mcp``）。
   bridge 起子进程 → stdio handshake → ``initialize`` → ``list_tools`` 拿描述。
   执行期出错支持重启（最多 3 次），结束时优雅 close。
4. **不绑死 Playwright MCP**：本类对外暴露 ``server_command + server_args``
   配置，方便 Task 9.x 拉起其他 MCP server（platform tools、自研脚本等）。

部署链路（详见 ``docs/PHASE2_DEPLOYMENT_NOTES.md``）：
- Python 侧：``mcp`` (PyPI 包 ≥1.27) ✅ Task 7.2 已加入 pyproject.toml
- 子进程 runtime：Node.js + ``npx @playwright/mcp`` ⏳ Task 11.3 进 Dockerfile
- 浏览器二进制：Chromium（约 300MB） ⏳ Task 7.3 + Task 11.3
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.modules.llm.agent_tools import (
    ToolFn,
    register_tool,
    unregister_namespace,
)

logger = logging.getLogger(__name__)


# ─── 类型别名 ────────────────────────────────────────────────────────────
# OpenAI tool spec dict 的形状非常稳定，但 SDK 没暴露官方 TypedDict，这里
# 用 dict 注解 + 注释说明字段，避免引入额外依赖。
OpenAIToolSpec = dict[str, Any]
# 形状：{"type": "function", "function": {"name": str, "description": str,
#                                         "parameters": {...JSONSchema...}}}


# 子进程启动失败时的重试策略：固定 3 次 + 指数 backoff（0.5s / 1s / 2s）。
# 重试只覆盖"启动 / handshake"阶段；运行中 call_tool 失败由调用方按业务
# 决定是否重试，不在 bridge 这层兜（避免黑盒重复消耗 token）。
_START_MAX_RETRIES = 3
_START_BACKOFF_BASE = 0.5


class MCPBridgeError(RuntimeError):
    """MCPBridge 层的基类异常。调用方可捕获后降级或重试。"""


class MCPNotStartedError(MCPBridgeError):
    """在 ``start()`` 之前调用了 ``call_tool`` / ``discover_tools``。"""


class MCPBridge:
    """单个 MCP server 子进程的连接 + 工具注册 wrapper。

    典型用法：
    ```python
    bridge = MCPBridge.for_playwright(cdp_endpoint="http://...")
    await bridge.start()
    try:
        tools = await bridge.discover_tools()
        bridge.register_into_agent_tools(tools, execution_id=exec_id)
        # ... 业务逻辑（agent loop 调用 TOOL_REGISTRY 里的 <exec_id>:browser_* ）
    finally:
        await bridge.unregister(execution_id=exec_id)
        await bridge.close()
    ```

    支持作为 async context manager 用：``async with MCPBridge(...) as b:``。
    """

    def __init__(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        name: str = "mcp-server",
    ) -> None:
        self._params = StdioServerParameters(
            command=command,
            args=list(args or []),
            env=env,
            cwd=cwd,
        )
        self._name = name
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._registered_namespaces: set[str] = set()

    # ── 工厂方法 ─────────────────────────────────────────────────────
    @classmethod
    def for_playwright(
        cls,
        *,
        cdp_endpoint: str,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> "MCPBridge":
        """构造一个连到外部 Chromium 的 Playwright MCP bridge。

        CDP endpoint 由 Task 7.3 ``BrowserBundle`` 提供（Python Playwright
        SDK 启动浏览器后暴露）。这里只是封装 ``npx @playwright/mcp`` 的常用
        参数，方便测试 / 调试。

        ``--cdp-endpoint`` 参数对应 Playwright MCP ≥0.0.27 的 CLI 风格
        （历史上曾叫 ``--browser-cdp-endpoint``，0.0.30 后改名）。我们镜像
        里装的是 ``@playwright/mcp@latest``（构建时实测 0.0.73），按新名传。
        """
        args = [
            "--yes",  # 自动 accept npx 第一次安装时的 prompt
            "@playwright/mcp@latest",
            f"--cdp-endpoint={cdp_endpoint}",
        ]
        if extra_args:
            args.extend(extra_args)
        return cls(
            command="npx",
            args=args,
            env=env,
            name=f"playwright-mcp[{cdp_endpoint}]",
        )

    # ── 生命周期 ─────────────────────────────────────────────────────
    async def start(self) -> None:
        """起子进程 + 建立 stdio + initialize 握手。

        固定重试 3 次：处理 "npx 第一次拉包慢 / Node 还没准备好" 之类瞬时
        失败。每次失败都把上次的 stack 拆掉再重建，避免半启动状态泄漏。
        """
        if self._session is not None:
            logger.debug("MCPBridge[%s] already started, skip", self._name)
            return

        last_exc: BaseException | None = None
        for attempt in range(1, _START_MAX_RETRIES + 1):
            stack = AsyncExitStack()
            try:
                read, write = await stack.enter_async_context(
                    stdio_client(self._params)
                )
                session = await stack.enter_async_context(
                    ClientSession(read, write)
                )
                # 握手必须在所有调用之前完成，否则 list_tools 会被 server reject。
                await session.initialize()
                self._stack = stack
                self._session = session
                logger.info(
                    "MCPBridge[%s] started (attempt %d/%d)",
                    self._name, attempt, _START_MAX_RETRIES,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                # 拆掉半启动的 stack；忽略关闭过程中的次生异常，避免覆盖原始错误。
                try:
                    await stack.aclose()
                except Exception:  # noqa: BLE001
                    logger.exception("MCPBridge[%s] stack close failed during retry", self._name)
                if attempt < _START_MAX_RETRIES:
                    backoff = _START_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "MCPBridge[%s] start failed (attempt %d/%d): %s — retry in %.1fs",
                        self._name, attempt, _START_MAX_RETRIES, exc, backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "MCPBridge[%s] start failed permanently after %d attempts",
                        self._name, _START_MAX_RETRIES,
                    )

        raise MCPBridgeError(
            f"MCP server '{self._name}' failed to start after "
            f"{_START_MAX_RETRIES} attempts"
        ) from last_exc

    async def close(self) -> None:
        """优雅关闭子进程 + 清理所有命名空间注册。"""
        for ns in list(self._registered_namespaces):
            unregister_namespace(ns)
        self._registered_namespaces.clear()

        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        except Exception:  # noqa: BLE001
            logger.exception("MCPBridge[%s] close raised; subprocess may be zombied", self._name)
        finally:
            self._stack = None
            self._session = None

    async def __aenter__(self) -> "MCPBridge":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def is_started(self) -> bool:
        return self._session is not None

    # ── 工具发现 / 调用 ──────────────────────────────────────────────
    async def discover_tools(self) -> list[OpenAIToolSpec]:
        """list MCP tools → 转成 OpenAI function-calling spec 列表。

        每个 spec 形状：
            {"type": "function", "function": {
                "name": "<tool>",  # 此时不带 namespace
                "description": "...",
                "parameters": {...JSONSchema...},
            }}

        ``register_into_agent_tools`` 会在最后才加 ``<execution_id>:`` 前缀，
        这样调用方可以先看 raw 工具列表（便于权限审查），再决定要不要注册
        给某个 execution 用。
        """
        session = self._require_session()
        result = await session.list_tools()
        specs: list[OpenAIToolSpec] = []
        for tool in result.tools:
            specs.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": _normalize_input_schema(tool.inputSchema),
                },
            })
        logger.info("MCPBridge[%s] discovered %d tools", self._name, len(specs))
        return specs

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用 MCP server 上的某个工具，返回归一化的 dict。

        归一化策略：把 MCP 协议的 ``CallToolResult.content``（一组 TextContent
        / ImageContent / ...）拍平成一个 dict：
            {"content": "<合并后的文本>", "is_error": bool, "raw": [...]}

        - ``content`` 是模型最常用的部分（让 ``run_tool`` 直接 json.dumps 给
          模型看）
        - ``is_error`` 来自 MCP ``isError`` 字段，便于 SecurityGuard / Engine
          快速判定异常
        - ``raw`` 保留原始结构，给 Engine 做更深处理时用（截图、refs 等）
        """
        session = self._require_session()
        result = await session.call_tool(name, arguments=arguments or {})

        text_parts: list[str] = []
        raw: list[dict[str, Any]] = []
        for item in (result.content or []):
            kind = getattr(item, "type", None)
            if kind == "text":
                text_parts.append(getattr(item, "text", "") or "")
                raw.append({"type": "text", "text": getattr(item, "text", "")})
            elif kind == "image":
                # 图像 base64 可能很大；保留到 raw 让上层（Engine）自行决定
                # 是否落盘为 step screenshot，但**不**进 ``content`` 给 LLM —
                # 让 LLM 看 base64 既浪费 token 又影响判定。
                # ``data_len`` 仍然保留方便日志/审计。
                img_data = getattr(item, "data", "") or ""
                raw.append({
                    "type": "image",
                    "mime_type": getattr(item, "mimeType", None),
                    "data": img_data,
                    "data_len": len(img_data),
                })
            else:
                # 未知类型原样塞进 raw，避免静默丢数据
                raw.append({"type": str(kind), "_repr": repr(item)[:200]})

        return {
            "content": "\n".join(text_parts),
            "is_error": bool(getattr(result, "isError", False)),
            "raw": raw,
        }

    # ── TOOL_REGISTRY 集成 ──────────────────────────────────────────
    def register_into_agent_tools(
        self,
        specs: list[OpenAIToolSpec],
        *,
        execution_id: uuid.UUID | str,
    ) -> list[OpenAIToolSpec]:
        """把 ``specs`` 加上 ``<execution_id>__`` 命名空间后注册到 TOOL_REGISTRY。

        返回**已重命名**的 specs 列表（``function.name`` 现在是
        ``<execution_id>__<tool>``），调用方应把它当作 ``tools=`` 参数传给
        ``stream_chat`` —— 模型看到的就是带前缀的名字，调用时自然发回带前缀
        的 tool_call.name，``run_tool`` 查 TOOL_REGISTRY 命中我们注册的
        executor。

        分隔符使用 ``__`` 而非 ``:``，因为 OpenAI Chat 接口要求
        ``tools[i].function.name`` 严格匹配 ``^[a-zA-Z0-9_-]+$``，``:`` 会触发
        ``BadRequestError: Invalid 'tools[0].function.name'``（实际触发于切换
        到严格 OpenAI-compatible provider 的场景）。
        """
        ns = str(execution_id)
        renamed: list[OpenAIToolSpec] = []
        for spec in specs:
            raw_name = spec["function"]["name"]
            ns_name = f"{ns}__{raw_name}"
            executor = self._make_tool_executor(raw_name)
            register_tool(ns_name, executor)
            new_spec = {
                "type": spec.get("type", "function"),
                "function": {
                    **spec["function"],
                    "name": ns_name,
                },
            }
            renamed.append(new_spec)
        self._registered_namespaces.add(ns)
        logger.info(
            "MCPBridge[%s] registered %d tools under namespace %s",
            self._name, len(renamed), ns,
        )
        return renamed

    async def unregister(self, *, execution_id: uuid.UUID | str) -> int:
        """清掉某个 execution 注册过的所有 namespaced tools。返回清掉的数量。

        对应 ``register_into_agent_tools`` 的反向操作，``BrowserBundle.close``
        / ``ExecutionEngine.finally`` 会调用。即便不显式调，``MCPBridge.close``
        里也会兜底批量清。
        """
        ns = str(execution_id)
        removed = unregister_namespace(ns)
        self._registered_namespaces.discard(ns)
        return removed

    # ── 内部 helpers ─────────────────────────────────────────────────
    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise MCPNotStartedError(
                f"MCPBridge[{self._name}] not started; call .start() first"
            )
        return self._session

    def _make_tool_executor(self, raw_tool_name: str) -> ToolFn:
        """为某个 raw tool name 构造 closure，注册到 TOOL_REGISTRY 当 executor。

        签名固定为 ``ToolFn = async (dict) -> dict``，与一期 ``web_search`` 等
        工具保持一致，方便 ``run_tool`` 透明调用。
        """

        async def _executor(args: dict[str, Any]) -> dict[str, Any]:
            try:
                return await self.call_tool(raw_tool_name, args)
            except MCPNotStartedError as exc:
                # bridge 已被 close 掉但 TOOL_REGISTRY 里还有旧条目（理论上
                # 不会发生，因为 close 会先清 namespace）。返回结构化错误而
                # 非 raise，避免 agent loop 崩。
                logger.error("MCP tool %s called after bridge close: %s", raw_tool_name, exc)
                return {"content": "", "is_error": True, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001
                logger.exception("MCP tool %s failed", raw_tool_name)
                return {"content": "", "is_error": True, "error": str(exc)}

        # 给 closure 一个有意义的 __name__，logger / 调试栈更好看
        _executor.__name__ = f"mcp_call_{raw_tool_name}"
        return _executor


# ─── 模块级 helpers ──────────────────────────────────────────────────────


def discover_mcp_tools(bridge: MCPBridge) -> "asyncio.Future[list[OpenAIToolSpec]]":
    """文档约定的命名 helper（等价 ``bridge.discover_tools()``）。

    保留这个名字主要是为了和 Task 7.2 plan 的"产出文件"小节对齐 —— plan 里
    显式列了 ``discover_mcp_tools(bridge)``，方便后续 grep。
    """
    return asyncio.ensure_future(bridge.discover_tools())


def make_tool_executor(bridge: MCPBridge, tool_name: str) -> Callable[[dict], Any]:
    """文档约定的命名 helper（等价 ``bridge._make_tool_executor(tool_name)``）。

    暴露公有 wrapper 而非直接使用 ``_make_tool_executor`` 的目的：让外部测试
    / 自定义注册流程可以构造 executor 但不调用 ``register_into_agent_tools``
    自动注册（典型场景：先 dry-run 看模型会不会用，再决定是否真的注册）。
    """
    return bridge._make_tool_executor(tool_name)


def _normalize_input_schema(schema: Any) -> dict[str, Any]:
    """把 MCP server 返回的 inputSchema 归一化到合法的 OpenAI function parameters。

    OpenAI function-calling 要求 ``parameters`` 是 JSON Schema object 形态：
    至少含 ``type: "object"`` 和 ``properties: {}``。少数 MCP server 会
    返回 ``None`` 或裸 dict，这里统一兜底。
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    out = dict(schema)
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


__all__ = [
    "MCPBridge",
    "MCPBridgeError",
    "MCPNotStartedError",
    "OpenAIToolSpec",
    "discover_mcp_tools",
    "make_tool_executor",
]


# ─── 子进程结果序列化辅助（给 run_tool 使用时方便）────────────────────────
def serialize_call_result(result: dict[str, Any]) -> str:
    """把 ``MCPBridge.call_tool`` 的归一化 dict 序列化成给 LLM 看的 JSON。

    不直接 ``json.dumps(result)`` 是为了：
    - 控制 ``raw`` 字段长度（图像 / 长文本会爆 token）
    - 把 ``is_error=True`` 的情况打成显眼前缀，让模型立刻知道工具失败
    """
    if result.get("is_error"):
        return json.dumps(
            {"error": True, "content": result.get("content", "")[:2000]},
            ensure_ascii=False,
        )
    return json.dumps(
        {"content": result.get("content", "")[:8000]},
        ensure_ascii=False,
    )
