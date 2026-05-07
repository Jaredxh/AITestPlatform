"""BrowserBundle — Playwright Python SDK + MCP Bridge 的捆绑生命周期管理器。

设计文档：``docs/PHASE2_DESIGN.md`` §3.2 + §3.3。

为什么是"捆绑（Bundle）"：
UI 自动化执行需要两条能力同时在线：
1. **Playwright Python SDK** 干"管家活儿"：起浏览器、录视频、抓 trace、
   导出 storage_state、关闭。这些是确定性、不需要 LLM 决策的工程操作。
2. **Playwright MCP** 干"AI 操作活儿"：把 navigate/click/type/snapshot
   等动作以 MCP tool 形式暴露给 LLM agent loop。

两边必须共享**同一个 Chromium 实例 / 同一个 BrowserContext**，否则会出
现"录的视频里啥都没发生"或"AI 点击的页面 SDK 拿不到 cookie"这类灵异
现象。共享方式：SDK launch 时开 ``--remote-debugging-port=N``，MCP 通过
``--cdp-endpoint=http://127.0.0.1:N`` 连过来（旧版 MCP 叫 ``--browser-cdp-endpoint``，
0.0.30+ 已改名）。

## 关键架构（务必保留 launch_persistent_context）

实测教训（2026-05 排查 http_login 注入 cookie 后 MCP 看不到的事故）：
**Chromium 单进程内同时存在多个 BrowserContext** —— 通过 ``chromium.launch()``
后再 ``new_context()``，chromium 内部就有 2 个 BrowserContext：

  1. **SDK incognito context** —— ``browser.new_context()`` 显式创建，SDK 端
     ``browser.contexts`` 列出的就是它；
  2. **CDP default profile context** —— chromium 启动时隐式存在的 profile
     context，**MCP 通过 ``connectOverCDP`` attach 后看到的 ``contexts[0]`` 就
     是它**（而**不是** SDK 的那个 incognito）。

后果：``self.context.add_cookies(...)`` 注入到 BC1，AI 通过 MCP
``browser_navigate`` 时浏览器走的是 BC2 —— **cookie 完全不参与请求**，业务后
端检测未登录立刻 302 回登录页，``http_login`` 前置成功也没用。

修复：改用 ``chromium.launch_persistent_context()``，整个 chromium 只有 **一个**
持久化 context，SDK 操作的 ``self.context`` 与 MCP 通过 CDP 看到的
``contexts[0]`` 是同一对象，cookie 注入立即对 MCP 可见。代价：
- ``browser`` 对象不再有意义（``self.context.browser`` 可能为 ``None``）；
- 必须给一个 ``user_data_dir``（用 ``tempfile.mkdtemp`` 隔离 + close 时清理）。

**强烈不要回滚成 ``launch + new_context``**——任何对此处的"简化"都会重现
"双 context 隔离"事故。

失败回退（design doc §3.2）：
MCP 子进程启动失败时（npx 拉不到包 / Node 没装 / OS 不兼容），bundle 标
记 ``mcp_unavailable=True``，**仍然返回可用对象**，让上层 ExecutionEngine
能选择"纯 SDK 模式 fallback 跑一些预定义脚本"或者"标记本次执行 status=
mcp_unavailable 直接 abort"。这样 MCP 故障不会让整个二期模块瘫痪。

部署链路（详见 ``docs/PHASE2_DEPLOYMENT_NOTES.md``）：
- ✅ Task 7.3 已完成：playwright Python 包装入 .venv
- ⏳ Task 11.3 待集成：Chromium 二进制（``playwright install chromium``）
- ⏳ Task 11.3 待集成：Node + ``@playwright/mcp`` 全局安装
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,  # noqa: F401  保留导出，类型注解仍可能用到
    BrowserContext,
    Playwright,
    async_playwright,
)

from app.config import settings
from app.modules.ui_automation.mcp_bridge import (
    MCPBridge,
    MCPBridgeError,
)
from app.modules.ui_automation.security import EnvironmentLike

logger = logging.getLogger(__name__)


def _is_container_environment() -> bool:
    """是否运行在容器 / CI 环境里。

    判断依据：``/.dockerenv`` 文件（标准 docker） / ``CI`` 环境变量（GH Actions
    等）。任意一条命中即认为是 "无 GUI 显示器" 的部署环境。
    """
    return (
        os.path.exists("/.dockerenv")
        or os.environ.get("CI", "").lower() in ("1", "true", "yes")
    )


def _docker_chromium_safety_args() -> list[str]:
    """容器 / CI 里跑 Chromium 常见需要关闭 sandbox + 避免 /dev/shm 过小。"""
    if _is_container_environment():
        return ["--no-sandbox", "--disable-dev-shm-usage"]
    return []


def _has_display_server() -> bool:
    """是否存在可用 X11 / Wayland 显示器。

    Linux 容器里默认两者都没有，``headless=False`` 启动 Chromium 一定会 crash
    （报 "Missing X server or $DISPLAY" 之类）。Engine 据此自动降级到 headless。
    macOS / Windows 下这两个 env 不存在不代表没有显示器（系统自带），所以仅在
    Linux/容器场景上判定。
    """
    if os.environ.get("DISPLAY"):
        return True
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


_URL_RE = re.compile(r"https?://[^\s\"',)\]<>]+")


def _scan_first_url(text: str) -> str | None:
    """从一段任意文本里找第一条 ``http(s)://...`` 子串。

    用在 ``get_current_url_via_mcp`` 解析 MCP ``browser_tabs_list`` /
    ``browser_evaluate`` / ``browser_snapshot`` 返回的多种文本格式上。

    截断字符集排除空白、引号、括号 / 尖括号——这些字符通常是 url 的边界，
    无论是 yaml 里的 ``url: "https://x.com"`` 还是 markdown table 里的
    ``| https://x.com |`` 都能正确切出 URL。
    """
    if not text:
        return None
    m = _URL_RE.search(text)
    return m.group(0) if m else None


# ─── 配置 ─────────────────────────────────────────────────────────────


@dataclass
class BundleOptions:
    """启动 BrowserBundle 时的可调参数。

    暴露所有"跨执行可能不同"的配置；Engine 根据 environment / 用户选择
    构造一个 options 传给 ``BrowserBundle.open``，避免 Bundle 内部到处看
    全局 settings。
    """

    headless: bool = True
    """容器 / CI 默认 True；本地调试可设 False 直观看浏览器。"""

    storage_state_path: str | None = None
    """已保存的 storage_state JSON 文件路径（典型来自前置登录步骤）。
    None = 全新 context。Task 8.1 ``state_manager`` 决定具体路径。"""

    record_video_dir: str | None = None
    """非 None 时启用视频录制；目录由 Engine 创建（按 execution_id 隔离）。"""

    record_har_path: str | None = None
    """非 None 时录 HAR 到该路径，便于失败时复盘网络请求。"""

    extra_browser_args: list[str] = field(default_factory=list)
    """额外 Chromium 命令行参数（``--no-sandbox`` 等）。``--remote-debugging-port``
    由 Bundle 内部自动追加，不要在这里传。"""

    mcp_enabled: bool = True
    """False 时纯 SDK 模式启动（不起 MCP 子进程），用于"已知 MCP 不可用
    但仍想跑确定性脚本"的兜底场景。"""

    mcp_extra_args: list[str] = field(default_factory=list)
    """传给 ``npx @playwright/mcp`` 的额外参数（``--isolated`` 等）。"""

    browser_proxy: str | None = None
    """浏览器出口代理地址（HTTP / HTTPS / SOCKS5）。

    macOS Docker Desktop 场景下 VPN 跑在宿主机 utun 接口，容器流量到不了 VPN
    隧道。把这里设成宿主机上的代理（如 ``http://host.docker.internal:8118``），
    chromium 通过 ``--proxy-server`` 出口走代理 → 代理在宿主机命中 VPN 路由 → 通。

    None / 空串 = 直连。格式遵循 Playwright ``proxy.server`` 字段：
    ``http://[user:pwd@]host:port`` 或 ``socks5://host:port``。
    """

    browser_proxy_bypass: str | None = None
    """逗号分隔的代理白名单（这些 host 直连不走代理）。
    例：``"*.cdn.local,localhost,127.0.0.1"``。"""


# ─── storage_state 注入 ───────────────────────────────────────────────


async def _inject_storage_state_after_launch(
    context: BrowserContext,
    state_path: str,
    *,
    execution_id: uuid.UUID | str | None = None,
) -> None:
    """把 ``storage_state`` JSON 文件里的 cookies / localStorage 注入到 context。

    背景：``chromium.launch_persistent_context`` 的签名里 **没有**
    ``storage_state`` 参数（Playwright Python API 仅允许在 ``browser.new_context``
    / ``browser.new_page`` 上传 storage_state，详见 1.40+ 文档；1.59 实测 launch
    时硬塞会抛 ``TypeError: ... got an unexpected keyword argument
    'storage_state'``）。我们又必须用 persistent_context（详见模块顶部 docstring
    "关键架构"），所以这里做"等价回放"——读 state JSON 然后：

    - **cookies** → ``context.add_cookies``。Playwright 接受的字段格式与
      ``storage_state`` 导出的完全一致，无需转换。
    - **localStorage** → 走 ``context.add_init_script``，每次 page 加载前在
      目标 origin 上 ``localStorage.setItem``。这条路径是必要的：localStorage
      是 per-origin 的，且只能在已经导航到对应 origin 的 page 内 JS 上下文
      里写——init_script 正好满足"导航到 origin 后立刻执行"的要求。

    任何错误（文件缺失 / JSON 解析失败 / Playwright add_cookies 报错）只记
    日志，**不**抛异常打断 bundle.open——后续 ``state_inject`` 模板会自己检
    测过期并触发重新登录，比这里直接当掉整个 execution 友好。
    """
    p = Path(state_path)
    if not p.exists() or not p.is_file():
        logger.info(
            "BrowserBundle[%s] storage_state file missing, skip injection: %s",
            execution_id, state_path,
        )
        return

    try:
        with p.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "BrowserBundle[%s] failed to read storage_state %s: %s",
            execution_id, state_path, exc,
        )
        return

    cookies = state.get("cookies") or []
    if cookies:
        try:
            await context.add_cookies(cookies)
            logger.info(
                "BrowserBundle[%s] storage_state: injected %d cookies",
                execution_id, len(cookies),
            )
        except Exception as exc:  # pragma: no cover - 防御 playwright 内部异常
            logger.warning(
                "BrowserBundle[%s] storage_state add_cookies failed: %s",
                execution_id, exc,
            )

    origins = state.get("origins") or []
    if origins:
        # init_script 在每个 page 创建后、首条业务 JS 之前注入；脚本内自己按
        # ``location.origin`` 匹配应用，这样只有访问目标 origin 的 page 才会真
        # 写 localStorage——其它 origin（about:blank、第三方分析域名）静默跳过。
        script = (
            "(() => {\n"
            f"  const __origins = {json.dumps(origins, ensure_ascii=False)};\n"
            "  try {\n"
            "    const cur = location.origin;\n"
            "    for (const o of __origins) {\n"
            "      if (!o || o.origin !== cur) continue;\n"
            "      for (const it of (o.localStorage || [])) {\n"
            "        try { localStorage.setItem(it.name, it.value); }\n"
            "        catch (_) { /* quota / 不可写 */ }\n"
            "      }\n"
            "    }\n"
            "  } catch (_) { /* about:blank / data: 等场景下 location 不可用 */ }\n"
            "})();\n"
        )
        try:
            await context.add_init_script(script)
            logger.info(
                "BrowserBundle[%s] storage_state: registered localStorage init_script for %d origins",
                execution_id, len(origins),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "BrowserBundle[%s] storage_state add_init_script failed: %s",
                execution_id, exc,
            )


# ─── BrowserBundle ────────────────────────────────────────────────────


class BrowserBundle:
    """Playwright Browser + Context + MCPBridge 的统一所有者。

    使用方式（Engine 视角）：
    ```python
    bundle = await BrowserBundle.open(env, execution_id, options=BundleOptions(...))
    try:
        if bundle.mcp_unavailable:
            # 走纯 SDK 兜底分支 / 抛错终止 execution
            ...
        else:
            # 注册 MCP tools 到 TOOL_REGISTRY，跑 agent loop
            await bundle.register_mcp_tools_for_agent()
            ...
    finally:
        await bundle.close()
    ```

    亦可作为 async context manager：``async with await BrowserBundle.open(...) as b:``
    """

    def __init__(
        self,
        *,
        environment: EnvironmentLike,
        execution_id: uuid.UUID,
        options: BundleOptions,
    ) -> None:
        self.environment = environment
        self.execution_id = execution_id
        self.options = options

        # 运行时填充
        self.pw: Playwright | None = None
        # browser 字段保留是为兼容旧调用链上的 ``getattr(self.browser, ...)``，
        # 改用 launch_persistent_context 后 chromium 只暴露一个 context，没有
        # 单独的 Browser 对象。所有"遍历 browser.contexts"的逻辑改成 fallback
        # 到 ``[self.context]``（见 _all_contexts()）。
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.mcp_bridge: MCPBridge | None = None
        self.cdp_endpoint: str | None = None
        # 持久化 context 必须有 ``user_data_dir`` —— 我们用 tempdir 隔离，
        # close 时清理，避免不同 execution 之间互相污染 cookie / cache。
        self._user_data_dir: str | None = None

        # 失败回退标记
        self.mcp_unavailable: bool = False
        self.mcp_unavailable_reason: str | None = None

        self._closed: bool = False

    # ── 内部 helper ──────────────────────────────────────────────
    def _all_contexts(self) -> list[BrowserContext]:
        """返回 bundle 当前持有的 BrowserContext 列表（统一兼容入口）。

        ``launch_persistent_context`` 模式下只有 ``self.context`` 这一个
        BrowserContext。旧代码大量调 ``getattr(self.browser, "contexts", [])``，
        换成 persistent 后 ``self.browser`` 是 None；统一通过本函数获取，避免
        每个调用点都写 ``or [self.context]`` fallback。

        如果 ``self.context.browser`` 凑巧不为 None（理论上 persistent context 也
        可能挂在某个 ``Browser`` 对象下），还会顺带把它的其他 context 也收进来，
        万一未来 Playwright 版本变了行为不至于漏掉 page。
        """
        if self.context is None:
            return []
        result: list[BrowserContext] = [self.context]
        owner = getattr(self.context, "browser", None)
        if owner is not None:
            for c in getattr(owner, "contexts", []) or []:
                if c is not self.context:
                    result.append(c)
        return result

    # ── 工厂 / 入口 ──────────────────────────────────────────────
    @classmethod
    async def open(
        cls,
        environment: EnvironmentLike,
        execution_id: uuid.UUID,
        *,
        options: BundleOptions | None = None,
    ) -> "BrowserBundle":
        """构造 + 启动一个 BrowserBundle。

        启动顺序：
        1. ``async_playwright().start()``
        2. ``chromium.launch`` + remote-debugging-port 暴露 CDP
        3. ``new_context``（含 storage_state / 录像 / HAR 配置）
        4. 启动 ``MCPBridge.for_playwright(cdp_endpoint=...)``
            - 失败时 `mcp_unavailable=True`，但 Bundle 仍可用（见 §3.2）
        """
        opts = options or BundleOptions()
        bundle = cls(environment=environment, execution_id=execution_id, options=opts)
        try:
            await bundle._launch_browser()
            if opts.mcp_enabled:
                await bundle._launch_mcp_bridge()
        except Exception:
            # 启动半途失败：先把已开的资源关掉，再把异常抛出去
            await bundle._safe_close_partial()
            raise
        return bundle

    # ── 内部启动步骤 ─────────────────────────────────────────────
    async def _launch_browser(self) -> None:
        """启动 Chromium（``launch_persistent_context``）+ 暴露 CDP endpoint。

        架构关键（详见模块级 docstring "关键架构"小节）：
        必须用 ``launch_persistent_context``，**不能**用 ``launch + new_context`` —
        否则 chromium 内部会有两个 BrowserContext，SDK 注入的 cookie MCP 看不到。

        端口分配策略：先用 ``socket(SOCK_STREAM).bind(('127.0.0.1', 0))``
        让 OS 分配一个空闲端口再 close 让出 → 把这个端口传给 Chromium
        ``--remote-debugging-port=<port>``。这个"先借再还"窗口理论上有
        race condition（其他进程在间隙抢走端口），但实际 < 1ms 内 Chromium
        就 bind 上了，工程上完全可接受。

        相比"传 0 让 Chromium 自己分配"的方案：Chromium 把分配到的端口
        写到 stderr 一行 ``DevTools listening on ws://...`` 才能读出来，
        异步解析不稳定。固定端口模式更直接。
        """
        port = _allocate_free_port()
        self.cdp_endpoint = f"http://127.0.0.1:{port}"

        # entrypoint.sh 会在 exec uvicorn **之前** export DISPLAY=:99，但部分运行
        # 方式（自定义 CMD、进程管理器、或未继承完整 env 的中间层）可能让 Python
        # 进程里看不到 DISPLAY——``_has_display_server`` 误判 → 强行 headless，
        # Chromium 不向 Xvfb 画像素 → noVNC 整屏黑。这里用 settings 与 Xvfb 默认对齐。
        if _is_container_environment() and not self.options.headless:
            if not (os.environ.get("DISPLAY") or "").strip():
                raw = (settings.UI_VNC_DISPLAY or ":99").strip() or ":99"
                os.environ["DISPLAY"] = raw if raw.startswith(":") else f":{raw}"
                logger.info(
                    "BrowserBundle[%s] DISPLAY was unset; exporting DISPLAY=%s for headed Xvfb",
                    self.execution_id,
                    os.environ["DISPLAY"],
                )

        self.pw = await async_playwright().start()

        # 容器 / 无显示器场景下若用户配了 headed 模式，强制降级到 headless：
        # Chromium 没有 X server 启动会立刻 crash，我们与其让 execution 直接报
        # "Missing X server" 这种用户看不懂的底层错误，不如自动降级 + 在监控流
        # 里挂一个清晰的提示（执行 / Engine 层观察 ``self.headless_downgraded``）。
        effective_headless = self.options.headless
        self.headless_downgraded = False
        if not effective_headless and _is_container_environment() and not _has_display_server():
            logger.warning(
                "BrowserBundle[%s] forced headless=True: container detected and no DISPLAY available "
                "(set headless=True in environment, or deploy a Xvfb-enabled image to run headed mode)",
                self.execution_id,
            )
            effective_headless = True
            self.headless_downgraded = True

        browser_args = [
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            *_docker_chromium_safety_args(),
            *self.options.extra_browser_args,
        ]
        if not effective_headless and _is_container_environment():
            # Xvfb 无真实 GPU；近年 Chromium 默认 Ozone/EGL 在虚拟帧缓冲上易出现
            # 「VNC 全黑但执行仍可走」——显式 X11 + 关 GPU 合成，与 Dockerfile 内有头栈一致。
            browser_args.extend(
                (
                    "--ozone-platform=x11",
                    "--disable-gpu",
                    "--disable-gpu-compositing",
                ),
            )

        # persistent context 要求一个真实存在的 user_data_dir。每个 execution
        # 用独立 tempdir → 互不污染 + close 时整目录删掉。
        self._user_data_dir = tempfile.mkdtemp(
            prefix=f"ui-bundle-{self.execution_id}-",
        )

        # ``launch_persistent_context`` 的 kwargs 把 launch_options + context_options
        # 合二为一。出口代理 / record_video / record_har 都直接走这一个函数。
        #
        # ⚠️ 注意：``launch_persistent_context`` 的签名里 **没有** ``storage_state``
        # 参数（Playwright 1.59.0 实测：报 ``TypeError: ... got an unexpected
        # keyword argument 'storage_state'``）。``storage_state`` 只在
        # ``browser.new_context()`` / ``browser.new_page()`` 上才接受。这里改
        # 为：launch 之后再走 ``_inject_storage_state_after_launch`` 手动把
        # JSON 文件里的 cookies / localStorage 注入到刚拿到的 context。
        kwargs: dict[str, Any] = {
            "user_data_dir": self._user_data_dir,
            "headless": effective_headless,
            "args": browser_args,
            # accept_downloads 默认 True，多数业务后台导出 / 下载文件会用到。
            "accept_downloads": True,
        }
        if self.options.record_video_dir:
            kwargs["record_video_dir"] = self.options.record_video_dir
        if self.options.record_har_path:
            kwargs["record_har_path"] = self.options.record_har_path

        # 出口代理：VPN 场景下必须走宿主机代理才能访问公司内网（详见 BundleOptions
        # 字段 docstring）。
        proxy_server = (self.options.browser_proxy or "").strip()
        if proxy_server:
            proxy_cfg: dict[str, Any] = {"server": proxy_server}
            bypass = (self.options.browser_proxy_bypass or "").strip()
            if bypass:
                proxy_cfg["bypass"] = bypass
            kwargs["proxy"] = proxy_cfg
            logger.info(
                "BrowserBundle[%s] using browser_proxy=%s (bypass=%r)",
                self.execution_id, proxy_server, bypass or "(none)",
            )

        self.context = await self.pw.chromium.launch_persistent_context(**kwargs)
        # browser 字段保留 None；持久化 context 不一定挂在 Browser 上（取决于
        # Playwright 版本），就算挂上了也只是 cosmetic 的，业务代码统一用
        # ``self._all_contexts()`` 兜底。
        self.browser = getattr(self.context, "browser", None)

        # storage_state 注入必须 launch 之后做（见 kwargs 注释）。失败只记日志，
        # 不让整个 bundle 起不来——失效的 state 只是触发后续 state_inject 模板
        # 检测到 expired，走重新登录路径，而不是当掉整次执行。
        if self.options.storage_state_path:
            await _inject_storage_state_after_launch(
                self.context,
                self.options.storage_state_path,
                execution_id=self.execution_id,
            )

        # 等 CDP HTTP server 真的在 listen 再返回；MCP 启动太快连不上会重试，
        # 提前 wait 几次能省一次 npm 重试 backoff。
        await _wait_cdp_ready(port, timeout=5.0)
        logger.info(
            "BrowserBundle[%s] launched chromium (persistent context) with CDP %s",
            self.execution_id, self.cdp_endpoint,
        )

    async def _launch_mcp_bridge(self) -> None:
        """启动 MCP 子进程；失败时设置 mcp_unavailable 而非 raise。"""
        assert self.cdp_endpoint is not None
        bridge = MCPBridge.for_playwright(
            cdp_endpoint=self.cdp_endpoint,
            extra_args=self.options.mcp_extra_args,
        )
        try:
            await bridge.start()
        except (MCPBridgeError, FileNotFoundError, OSError) as exc:
            # FileNotFoundError = 系统没装 npx；OSError 涵盖 PATH 问题等
            self.mcp_unavailable = True
            self.mcp_unavailable_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "BrowserBundle[%s] MCP unavailable, falling back to SDK-only: %s",
                self.execution_id, self.mcp_unavailable_reason,
            )
            try:
                await bridge.close()
            except Exception:  # noqa: BLE001
                pass
            return
        self.mcp_bridge = bridge

    # ── 产物捕获辅助（step screenshot / video 路径发现） ─────────
    def get_primary_page(self) -> Any:
        """返回当前最可能由 agent 操作的 page。

        ``launch_persistent_context`` 模式下 SDK 与 MCP 共享同一个 context，
        所以理论上 ``self.context.pages`` 就够了；但保留 ``_all_contexts``
        遍历是为兼容未来 Playwright 版本可能行为变更。
        """
        pages: list[Any] = []
        for ctx in self._all_contexts():
            for p in getattr(ctx, "pages", []) or []:
                if p and not p.is_closed():
                    pages.append(p)
        if not pages:
            return None
        return pages[-1]

    async def get_current_url_via_mcp(self) -> str | None:
        """通过 MCP 工具拿当前 active page 的真实 URL —— 绕开 SDK BrowserContext 同步问题。

        为什么需要这个：
        Playwright SDK 通过 ``chromium.launch()`` 启动浏览器，``new_context()`` 创建
        BrowserContext A；MCP 通过 CDP 连接同一个 chromium 进程，但 MCP 调
        ``browser_navigate`` 时**不一定**把 page 创建在 SDK 视角能看到的 context 里
        —— 实际很多场景下 SDK 的 ``self.browser.contexts`` 里**根本没有 MCP 创建的 page**，
        所以 ``get_primary_page().url`` 经常返回 None / 空串。

        MCP 工具调用是直接走 CDP，一定能看到自己创建的 page。下面用三条路径兜底
        （任何一条成功即返回 URL）：
        1. ``browser_tabs_list`` —— 列出所有 tab，取最后一个 tab 的 url
        2. ``browser_evaluate`` —— 执行 ``window.location.href`` 直接读 URL
        3. ``browser_snapshot`` —— 有些 MCP 实现会在 snapshot 头部带 url=...

        都失败返回 None；调用方应自己兜底（比如取 navigate 历史的最后 URL）。
        """
        if self.mcp_bridge is None or self.mcp_unavailable:
            return None

        # 路径 1：browser_tabs_list（playwright/mcp v0.x 暴露这个工具）
        for tool_name in ("browser_tabs_list", "browser_tabs"):
            try:
                payload = await self.mcp_bridge.call_tool(tool_name, {})
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict) or payload.get("is_error"):
                continue
            content = payload.get("content") or ""
            if not content:
                continue
            # tabs_list 的输出通常是文本格式 "1. <url>" 或 yaml；扫一行行抓 http(s) 开头的串
            url = _scan_first_url(str(content))
            if url:
                return url

        # 路径 2：browser_evaluate(JS) —— 最准（但有些 MCP 部署禁用 evaluate）
        for tool_name, args in (
            ("browser_evaluate", {"function": "() => window.location.href"}),
            ("browser_evaluate", {"expression": "window.location.href"}),
        ):
            try:
                payload = await self.mcp_bridge.call_tool(tool_name, args)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict) or payload.get("is_error"):
                continue
            content = (payload.get("content") or "").strip()
            url = _scan_first_url(content) or (
                content if content.startswith(("http://", "https://")) else None
            )
            if url:
                return url

        # 路径 3：browser_snapshot 头部—很多 mcp 实现会把"page url: ..."写在 snapshot 第一行
        try:
            payload = await self.mcp_bridge.call_tool("browser_snapshot", {})
        except Exception:  # noqa: BLE001
            return None
        if isinstance(payload, dict) and not payload.get("is_error"):
            content = payload.get("content") or ""
            url = _scan_first_url(str(content))
            if url:
                return url
        return None

    async def capture_step_screenshot_via_mcp(
        self, dest_path: str, *, image_type: str = "png",
    ) -> str | None:
        """通过 MCP 的 ``browser_take_screenshot`` 工具抓当前 active tab 截图。

        相比 ``capture_step_screenshot``（走 Playwright Python SDK 的
        ``page.screenshot()``）：MCP 与 Python SDK 共享 Chromium 实例但**不**
        一定共享 BrowserContext —— SDK 启动时创建的 context 里可能根本没有
        page（MCP 自己开的 page 落在另一个 incognito context 上），导致
        ``page.screenshot()`` 永远拿不到画面。
        而 ``browser_take_screenshot`` MCP tool 一定作用于 MCP 当前控制的
        active page，可靠性最高。

        失败（MCP 未启动 / call_tool 抛错 / 没拿到 image data）返回 ``None``，
        不抛错，让 Engine fallback 走 SDK 路径。
        """
        if self.mcp_bridge is None:
            return None
        import base64

        try:
            result = await self.mcp_bridge.call_tool(
                "browser_take_screenshot",
                {"type": image_type},
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "BrowserBundle[%s] capture_step_screenshot_via_mcp call_tool failed: %s",
                self.execution_id, exc,
            )
            return None

        if result.get("is_error"):
            logger.info(
                "BrowserBundle[%s] browser_take_screenshot returned is_error=True (content=%s)",
                self.execution_id, (result.get("content") or "")[:200],
            )
            return None

        # 取 raw 列表里第一张 image
        img_b64: str | None = None
        for item in result.get("raw") or []:
            if item.get("type") == "image" and item.get("data"):
                img_b64 = item["data"]
                break
        if not img_b64:
            logger.info(
                "BrowserBundle[%s] browser_take_screenshot returned no image data (raw types=%s)",
                self.execution_id, [it.get("type") for it in result.get("raw") or []],
            )
            return None

        try:
            with open(dest_path, "wb") as f:
                f.write(base64.b64decode(img_b64))
            return dest_path
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "BrowserBundle[%s] write screenshot to %s failed: %s",
                self.execution_id, dest_path, exc,
            )
            return None

    async def capture_step_screenshot(
        self, dest_path: str, *, full_page: bool = False, image_type: str = "png",
    ) -> str | None:
        """把当前 page 截图保存到 ``dest_path``；失败返回 None（不抛错）。

        传入绝对路径；目录需要调用方保证存在。``image_type`` 仅影响参数
        签名，真的落盘格式由 ``dest_path`` 的后缀决定 —— 两者保持一致即可。

        失败原因（page=None / screenshot exception）会用 INFO 级别打 log，方
        便用户在监控页报"截图缺失"时直接读后端日志定位。
        """
        page = self.get_primary_page()
        if page is None:
            all_ctxs = self._all_contexts()
            page_count = sum(len(getattr(c, "pages", []) or []) for c in all_ctxs)
            logger.info(
                "BrowserBundle[%s] capture_step_screenshot: no page found "
                "(contexts=%d, pages=%d) — likely MCP closed / navigated away",
                self.execution_id, len(all_ctxs), page_count,
            )
            return None
        try:
            kwargs: dict[str, Any] = {"path": dest_path, "type": image_type, "full_page": full_page}
            await page.screenshot(**kwargs)
            return dest_path
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "BrowserBundle[%s] capture_step_screenshot failed at %s: %s",
                self.execution_id, getattr(page, "url", "?"), exc,
            )
            return None

    async def finalize_videos(self) -> list[str]:
        """关 context 之前收集所有 page 的 video，关之后返回已写盘的 .webm 路径。

        **必须**在 ``close()`` 之前调用一次（close 会释放 context 引用）。
        调用方：Engine 在 `bundle.close()` 前调，把返回的第一条写入
        ``UIExecution.video_path``。
        """
        videos: list[Any] = []
        for ctx in self._all_contexts():
            for p in getattr(ctx, "pages", []) or []:
                v = getattr(p, "video", None)
                if v is not None:
                    videos.append(v)
        self._pending_videos = videos  # type: ignore[attr-defined]
        return []

    async def collect_video_paths(self) -> list[str]:
        """``close()`` 之后拿 video 实际路径。Playwright 要求 context 已 close 才能读。"""
        videos = getattr(self, "_pending_videos", None) or []
        out: list[str] = []
        for v in videos:
            try:
                path = await v.path()
                if path:
                    out.append(str(path))
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "BrowserBundle[%s] video.path() failed: %s",
                    self.execution_id, exc,
                )
        return out

    # ── 用例间页面状态清理 ─────────────────────────────────────────
    async def reset_for_next_case(self, *, settle_timeout: float = 5.0) -> dict[str, Any]:
        """两条用例之间做"页面级"清理，让下一条用例从干净起跑。

        触发场景：批量执行 N 条用例时，用例 A 跑完会把 chromium 停在它最后
        操作的页面上——含未关闭的弹窗 / 未提交的表单 / 内存里的 JS 状态 /
        sessionStorage。下一条用例 B 进来如果不强制重置，AI 会基于 A 留下的
        污染状态做判断，常见后果：表单验证残留、modal dialog 挡住操作、
        SPA 路由因 history 状态未清而跳到非预期页面。

        清理动作（**保留登录态**）：
        1. 关闭除 *主 page* 以外的所有 page —— 把 popup / 多余 tab 收掉。
           主 page = ``self.context.pages[0]``（playwright/mcp 一般也只在
           contexts[0] 上工作）。
        2. 主 page ``goto("about:blank")`` —— 强制释放 DOM/JS 内存、取消
           ``onbeforeunload`` 弹窗、间接触发 sessionStorage 清空（about:blank
           跨 origin，sessionStorage 不会带过去）。
        3. **不动** ``cookies`` / ``localStorage`` / storage_state —— 这些
           是登录态载体，清掉等于强制每条用例都重登，得不偿失。

        失败处理：所有步骤都 try/except + log，最后返回 dict 报告做了啥
        （便于上游 stream 事件展示），不抛——next case 即便没清干净也还能
        靠自己 navigate 兜底，至少不会因 reset 失败让整批崩。
        """
        report: dict[str, Any] = {
            "closed_extra_pages": 0,
            "navigated_to_blank": False,
            "errors": [],
        }
        if self.context is None:
            report["errors"].append("context is None")
            return report

        all_pages: list[Any] = []
        for ctx in self._all_contexts():
            for p in getattr(ctx, "pages", []) or []:
                if p and not p.is_closed():
                    all_pages.append(p)

        if not all_pages:
            # 极端情况：context 没有任何 page。MCP 后续 navigate 会自动开
            # 一个新 page，啥也不用做。
            return report

        primary = all_pages[0]
        extras = all_pages[1:]
        for p in extras:
            try:
                await p.close()
                report["closed_extra_pages"] += 1
            except Exception as exc:  # noqa: BLE001
                msg = f"close extra page failed: {type(exc).__name__}: {exc}"
                report["errors"].append(msg)
                logger.debug("BrowserBundle[%s] %s", self.execution_id, msg)

        # 主 page 跳 about:blank。timeout 故意短（5s 默认）——这步只是清状
        # 态，卡住没意义。约定 ``commit`` 等待策略：等到 NavigationCommitted
        # 即可，不必等 ``load``，前者快很多且对 about:blank 已经够用。
        try:
            timeout_ms = max(1000, int(settle_timeout * 1000))
            try:
                await primary.goto("about:blank", wait_until="commit", timeout=timeout_ms)
            except TypeError:
                # 老 Playwright 不支持 wait_until=commit；退回 domcontentloaded
                await primary.goto(
                    "about:blank", wait_until="domcontentloaded", timeout=timeout_ms,
                )
            report["navigated_to_blank"] = True
        except Exception as exc:  # noqa: BLE001
            msg = f"goto about:blank failed: {type(exc).__name__}: {exc}"
            report["errors"].append(msg)
            logger.info("BrowserBundle[%s] %s", self.execution_id, msg)

        logger.info(
            "BrowserBundle[%s] reset_for_next_case: closed=%d blank=%s errors=%d",
            self.execution_id,
            report["closed_extra_pages"],
            report["navigated_to_blank"],
            len(report["errors"]),
        )
        return report

    # ── MCP tool 注册（仅 mcp_bridge 可用时）─────────────────────
    async def register_mcp_tools_for_agent(self) -> list[dict[str, Any]]:
        """发现 MCP 暴露的所有工具 + 注册到 TOOL_REGISTRY，返回 OpenAI
        function spec 列表（已带 ``<execution_id>:`` 命名空间），可直接当
        ``tools=`` 参数传给一期 ``stream_chat``。

        ``mcp_unavailable=True`` 时返回空列表，调用方应转入纯 SDK 兜底分支。
        """
        if self.mcp_bridge is None:
            return []
        specs = await self.mcp_bridge.discover_tools()
        return self.mcp_bridge.register_into_agent_tools(
            specs, execution_id=self.execution_id,
        )

    # ── 关闭 ────────────────────────────────────────────────────
    async def close(self) -> None:
        """优雅关闭整个 bundle。幂等，可重复调。

        关闭顺序：MCP bridge 先关（避免它继续往关了的浏览器发 CDP）→
        BrowserContext → Browser → Playwright runtime。每一步异常都
        catch + log，不阻塞后续清理。
        """
        if self._closed:
            return
        self._closed = True

        if self.mcp_bridge is not None:
            try:
                await self.mcp_bridge.unregister(execution_id=self.execution_id)
                await self.mcp_bridge.close()
            except Exception:  # noqa: BLE001
                logger.exception("BrowserBundle[%s] mcp_bridge close failed", self.execution_id)
            self.mcp_bridge = None

        if self.context is not None:
            # persistent context 的 close() 会同时关掉 chromium 进程；不需要
            # 再单独 close ``self.browser``。但如果有 owner browser（罕见，
            # 取决于 Playwright 版本），出于幂等再尝试关一次。
            try:
                await self.context.close()
            except Exception:  # noqa: BLE001
                logger.exception("BrowserBundle[%s] context close failed", self.execution_id)
            self.context = None

        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:  # noqa: BLE001
                logger.exception("BrowserBundle[%s] browser close failed", self.execution_id)
            self.browser = None

        if self.pw is not None:
            try:
                await self.pw.stop()
            except Exception:  # noqa: BLE001
                logger.exception("BrowserBundle[%s] playwright stop failed", self.execution_id)
            self.pw = None

        # 清理 persistent context 的 user_data_dir。失败不阻塞主流程，
        # 重启 / cron 清理 tempdir 兜底即可。
        if self._user_data_dir:
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "BrowserBundle[%s] cleanup user_data_dir %s failed (non-fatal)",
                    self.execution_id, self._user_data_dir,
                )
            self._user_data_dir = None

        logger.info("BrowserBundle[%s] closed", self.execution_id)

    async def _safe_close_partial(self) -> None:
        """启动半途失败时的强制清理。和 close 一样，但不抱怨。"""
        try:
            await self.close()
        except Exception:  # noqa: BLE001
            logger.exception("BrowserBundle[%s] partial cleanup failed", self.execution_id)

    # ── async context manager 糖衣 ───────────────────────────────
    async def __aenter__(self) -> "BrowserBundle":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


# ─── 工具函数 ─────────────────────────────────────────────────────────


def _allocate_free_port() -> int:
    """让 OS 分配一个本地空闲 TCP 端口并立即归还。

    用 SO_REUSEADDR + SO_REUSEPORT 可减小被立刻抢占的窗口，但跨平台不一致；
    实际工程上 <1ms 的 race window 完全可接受，这里只用最简单实现。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_cdp_ready(port: int, timeout: float = 5.0) -> None:
    """轮询 ``127.0.0.1:port`` 是否能接受 TCP 连接。

    比 HTTP `/json/version` 探测更轻量，避免 import httpx 增加依赖；
    Chromium 一旦能接 TCP，CDP 就 ready 了。
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port),
                timeout=0.5,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(0.1)
    # 超时不 raise，让 MCP 自己重试（它有 3 次内置重试）；这里只是 best-effort 预热
    logger.debug("CDP port %d not ready within %.1fs; proceeding anyway", port, timeout)


__all__ = [
    "BrowserBundle",
    "BundleOptions",
]
