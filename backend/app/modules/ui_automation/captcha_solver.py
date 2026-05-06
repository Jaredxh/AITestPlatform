"""验证码识别（Task 8.3）。

两种模式：
- ``bypass`` —— 直接返回 config.bypass_value（典型用法："测试环境验证码总是 1234"）；
  不依赖 ddddocr，即便 ONNX runtime 没装也能跑。
- ``ocr`` —— 抓取页面上验证码图片字节流 → ``ddddocr.classification`` 识别 → 校验
  长度 / 正则 → 不通过则刷新重试，最多 ``max_retries`` 轮。

设计要点：
1. **DdddOcr 单例化** —— ONNX runtime 初始化耗时（实测 ≈30-40s 含 import），
   每个 execution 重新 init 不可接受。``CaptchaSolver`` 类级 ``_ocr_instance``
   缓存唯一实例；进程生命周期内只 init 一次。
2. **CPU-bound 不阻塞 event loop** —— ``classification()`` 是同步 ONNX 推理，
   走 ``loop.run_in_executor(None, ...)`` 丢线程池，避免在 asyncio 主循环上
   pin 200-500ms。
3. **抓图双通道** —— 优先 MCP ``browser_screenshot(ref=...)``（精确到元素，
   字节小）；MCP 不可用 / config 没给 ref 时回退到 Playwright SDK
   ``page.locator(selector).screenshot()``。两条路径都失败才报错。
4. **结果校验（防"识别看起来对其实错"）** —— ``expected_length`` /
   ``expected_pattern`` 让调用方约束输出形态：4 位纯数字、6 位字母数字混合等；
   不满足直接当本次失败处理，刷新验证码重试。
5. **Tool 注册解耦** —— ``register_for_execution`` 把 solver 包成
   ``platform_solve_captcha`` tool 注册到 ``agent_tools.TOOL_REGISTRY``
   命名空间 ``<execution_id>__platform_solve_captcha``；execution 收尾用
   ``unregister_namespace(execution_id)`` 一键清理（已在 Task 7.2 实现）。
   注：分隔符是 ``__``（双下划线）—— OpenAI / 兼容 Chat 接口要求
   ``tools[i].function.name`` 严格匹配 ``^[a-zA-Z0-9_-]+$``，``:`` 会触发
   ``BadRequestError: Invalid 'tools[0].function.name'``。

调用方（Task 9.4 / ai_login 流程）：
```python
solver = CaptchaSolver()
tool_name = solver.register_for_execution(execution_id, bundle)
# ... 跑 agent loop，LLM 自由调 platform_solve_captcha ...
agent_tools.unregister_namespace(str(execution_id))
```
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.modules.llm import agent_tools

if TYPE_CHECKING:
    from app.modules.ui_automation.browser_bundle import BrowserBundle

logger = logging.getLogger(__name__)


# ─── 配置数据类 ───────────────────────────────────────────────────────


@dataclass
class CaptchaConfig:
    """单次验证码识别请求的全部参数。

    既能由 ``register_for_execution`` 包出的 tool 从 LLM args 构造，也能由
    Task 8.2 ``ai_login`` 流程的代码直接构造。
    """

    mode: Literal["bypass", "ocr"] = "ocr"

    model: Literal["default", "beta"] = "default"
    """OCR 模型选择：
    - ``default`` —— ddddocr 默认模型，**英文 + 数字**精度最好（90%+）
    - ``beta`` —— ddddocr beta 模型，**中文 / 中英混合 / 复杂背景**精度更好
      （但纯英数字精度反而不如 default）

    选哪个看验证码字符内容：纯数字 / 字母（``ABCD`` ``1234``）→ default；
    包含汉字（``验证码`` ``春暖花开``）→ beta。AI 在 ai_login 流程里看到 snapshot
    上 placeholder 提示「请输入图中文字」「图中汉字」时应当传 ``model="beta"``。

    注：两个模型分别缓存（``_ocr_instance_default`` / ``_ocr_instance_beta``），
    第一次用各自首启 ~30s。"""

    bypass_value: str | None = None
    """``mode=bypass`` 时返回这个；为空字符串 / None 都视为"无万能码可用"
    并返回 None。"""

    captcha_ref: str | None = None
    """MCP browser_snapshot 给的元素 ref（typical: ``ref-abc123``）；优先用。"""

    captcha_selector: str | None = None
    """CSS / XPath selector，作为 captcha_ref 的回退。两个都给时优先 ref。"""

    refresh_ref: str | None = None
    """刷新验证码的元素 ref（点一下重新出图）。"""

    refresh_selector: str | None = None
    """刷新元素的 CSS selector，作为 refresh_ref 的回退。"""

    max_retries: int = 3
    """最大尝试次数（含首次）。3 = 首次失败后再刷新 2 次。"""

    expected_length: int | None = None
    """期望识别结果字符数；不匹配视为本次失败。None = 不约束。"""

    expected_pattern: str | None = None
    """python regex 全匹配（fullmatch）；不匹配视为本次失败。None = 不约束。
    示例：``r"^\\d{4}$"`` 限定纯 4 位数字。"""

    refresh_wait_ms: int = 300
    """点完刷新按钮后等多久再抓新图（让验证码图换出来）。0 = 不等。"""

    def __post_init__(self) -> None:
        if self.max_retries < 1 or self.max_retries > 10:
            raise ValueError(f"max_retries={self.max_retries} 越界（1..10）")
        if self.expected_length is not None and self.expected_length < 1:
            raise ValueError(f"expected_length={self.expected_length} 必须 ≥1")
        if self.expected_pattern is not None:
            # fail-fast：非法 regex 在构造时就炸，不留到 OCR 流程里
            try:
                re.compile(self.expected_pattern)
            except re.error as exc:
                raise ValueError(f"expected_pattern 编译失败：{exc}") from exc
        if self.refresh_wait_ms < 0 or self.refresh_wait_ms > 10_000:
            raise ValueError(f"refresh_wait_ms={self.refresh_wait_ms} 越界（0..10000）")


@dataclass
class CaptchaResult:
    """``CaptchaSolver.solve`` 的返回。

    用 dataclass 而非"返回 str | None"是因为 LLM 看到 ``{"text": null,
    "success": false, "attempts": 3, "reason": "..."}`` 比看到一个字符串能
    做更好的下一步决策（比如"换种方式登录"）。
    """

    success: bool
    text: str | None
    attempts: int
    reason: str | None = None
    """失败时填："no_image" / "ocr_returned_empty" / "constraint_failed" /
    "ocr_unavailable" / "config_error"。"""


# ─── CaptchaSolver ────────────────────────────────────────────────────


class CaptchaSolver:
    """验证码识别器。线程安全；可在多个 execution 间共享同一实例。

    ``_ocr_instance`` 是类级 cache —— 全进程共享同一个 DdddOcr，避免每次
    new CaptchaSolver() 都触发 ~40s 的 ONNX init。测试可直接 patch 这个
    类属性塞 fake。
    """

    _ocr_instance_default: Any = None  # ddddocr.DdddOcr() —— 英数字模型
    _ocr_instance_beta: Any = None  # ddddocr.DdddOcr(beta=True) —— 中文模型
    _ocr_lock: asyncio.Lock | None = None

    @classmethod
    def _get_ocr(cls, model: Literal["default", "beta"] = "default") -> Any:
        """惰性创建对应模型的 DdddOcr。**同步**返回；每个模型首次调用会卡 ~30-40s。

        两个 slot 独立缓存：``default`` 和 ``beta`` 用的是 ddddocr 的两套权重
        （前者 charset=英数字优化，后者支持中文 / 复杂背景）。生产里通常只用
        其中一个，但同一个进程混用也安全 —— ddddocr 自身线程安全。

        没用 asyncio.Lock 是因为类属性赋值原子；首次并发最多多 init 几次（浪费
        但不损坏）。
        """
        if model == "beta":
            if cls._ocr_instance_beta is None:
                try:
                    import ddddocr
                except ImportError as exc:
                    raise CaptchaUnavailableError(
                        f"ddddocr 未安装：{exc}（pip install ddddocr 或参考部署文档）"
                    ) from exc
                cls._ocr_instance_beta = ddddocr.DdddOcr(beta=True, show_ad=False)
                logger.info("ddddocr DdddOcr(beta=True) 单例已初始化（中文模型）")
            return cls._ocr_instance_beta

        if cls._ocr_instance_default is None:
            try:
                import ddddocr
            except ImportError as exc:
                raise CaptchaUnavailableError(
                    f"ddddocr 未安装：{exc}（pip install ddddocr 或参考部署文档）"
                ) from exc
            cls._ocr_instance_default = ddddocr.DdddOcr(show_ad=False)
            logger.info("ddddocr DdddOcr() 单例已初始化（英数字模型）")
        return cls._ocr_instance_default

    async def solve(
        self,
        bundle: "BrowserBundle",
        config: CaptchaConfig,
    ) -> CaptchaResult:
        """主入口。"""
        if config.mode == "bypass":
            return self._solve_bypass(config)
        if config.mode == "ocr":
            return await self._solve_ocr(bundle, config)
        return CaptchaResult(
            success=False, text=None, attempts=0,
            reason=f"config_error: 未知 mode={config.mode!r}",
        )

    def _solve_bypass(self, config: CaptchaConfig) -> CaptchaResult:
        if not config.bypass_value:
            return CaptchaResult(
                success=False, text=None, attempts=1,
                reason="bypass mode 但 bypass_value 为空",
            )
        return CaptchaResult(
            success=True, text=config.bypass_value, attempts=1,
        )

    async def _solve_ocr(
        self,
        bundle: "BrowserBundle",
        config: CaptchaConfig,
    ) -> CaptchaResult:
        """识别 OCR 验证码 —— **关键不变量**：成功路径 0 次 refresh。

        循环结构：

        .. code-block:: text

            attempt=1：直接抓现有图 → OCR → 校验
              ├─ OK    → 立刻 return（不调 refresh，不修改页面任何状态）
              └─ 失败  → 进入 attempt=2

            attempt=2..N（仅识别失败时才走到）：
              先点 refresh 按钮换新图 → 等 refresh_wait_ms → 再抓 → OCR → 校验
              ├─ OK    → 立刻 return
              └─ 失败  → 继续

        即"识别成功 → 立刻返回 text，整个 page 不被修改"。
        调用方（ai_login_runner）拿到 text 后可以立刻 ``browser_type`` 填进
        输入框 + click 登录，期间验证码图片仍然是 OCR 识别的那张，不会被换。
        换言之：``platform_solve_captcha`` tool 在成功路径上对页面是只读的。

        历史回归保护：测试 ``test_solve_ocr_success_path_never_refreshes`` 显式
        断言"_try_refresh 调用次数 == 0"，杜绝以后改动里把 refresh 误放进
        attempt=1 / 成功路径上。
        """
        if not (config.captcha_ref or config.captcha_selector):
            return CaptchaResult(
                success=False, text=None, attempts=0,
                reason="config_error: ocr mode 必须给 captcha_ref 或 captcha_selector",
            )

        last_reason: str | None = None
        for attempt in range(1, config.max_retries + 1):
            # ⭐ 不变量：仅 ``attempt > 1`` 才点 refresh —— 也就是说，仅"上一次
            # 识别失败 / 校验不通过"才换图。第 1 次永远直接抓现成图，OCR 成功
            # 立刻 return，绝不刷新。
            if attempt > 1:
                refreshed = await self._try_refresh(bundle, config)
                if not refreshed:
                    # 刷不动就别再循环，省时间
                    last_reason = "refresh_failed"
                    break
                if config.refresh_wait_ms > 0:
                    await asyncio.sleep(config.refresh_wait_ms / 1000)

            try:
                img_bytes = await self._fetch_captcha_image(bundle, config)
            except CaptchaUnavailableError as exc:
                # 抓图通道完全不可用，没必要重试
                return CaptchaResult(
                    success=False, text=None, attempts=attempt,
                    reason=str(exc),
                )

            if not img_bytes:
                last_reason = "no_image"
                continue

            try:
                text = await self._classify(img_bytes, config.model)
            except CaptchaUnavailableError as exc:
                # ddddocr 不可用 —— 重试也救不了，直接报
                return CaptchaResult(
                    success=False, text=None, attempts=attempt,
                    reason=str(exc),
                )
            except Exception as exc:  # noqa: BLE001
                last_reason = f"ocr_error: {type(exc).__name__}: {exc}"
                logger.warning("OCR 第 %d 次失败：%s", attempt, exc)
                continue

            text = (text or "").strip()
            if not text:
                last_reason = "ocr_returned_empty"
                continue

            ok, why = _validate_text(text, config)
            if ok:
                logger.info(
                    "captcha solved in %d attempt(s): len=%d "
                    "(no page refresh on success path)",
                    attempt, len(text),
                )
                # 成功路径：直接 return，**不进入下一轮 attempt**，
                # 所以下一轮开头的 ``_try_refresh`` 永远不会被触发。
                return CaptchaResult(
                    success=True, text=text, attempts=attempt,
                )
            last_reason = f"constraint_failed: {why}（识别到 {text!r}）"

        return CaptchaResult(
            success=False, text=None,
            attempts=config.max_retries,
            reason=last_reason or "all_attempts_exhausted",
        )

    # ── 抓图：MCP 优先 + Playwright 兜底 ──────────────────────────────

    async def _fetch_captcha_image(
        self,
        bundle: "BrowserBundle",
        config: CaptchaConfig,
    ) -> bytes | None:
        """返回验证码图片字节流（PNG）。两条通道都失败返回 None。"""
        # 通道 A：MCP ``browser_take_screenshot(ref=..., element=...)``
        # ⚠️ 历史 bug：之前调的工具名是 ``browser_screenshot`` —— 那不是
        # ``@playwright/mcp`` 暴露的工具名，真正的工具叫 ``browser_take_screenshot``，
        # MCP 收到不存在的工具直接 isError，captcha_solver 一律拿 None 返回 no_image。
        # 同时 playwright/mcp 要求传 ref 时必带 ``element``（human-readable
        # description），缺少会被服务端拒绝。
        if config.captcha_ref and not bundle.mcp_unavailable and bundle.mcp_bridge:
            try:
                payload = await bundle.mcp_bridge.call_tool(
                    "browser_take_screenshot",
                    {
                        "ref": config.captcha_ref,
                        "element": "captcha image",
                        "type": "png",
                    },
                )
                img = _extract_image_bytes(payload)
                if img:
                    return img
                logger.info(
                    "MCP browser_take_screenshot returned no image bytes "
                    "(is_error=%s, raw_types=%s)",
                    payload.get("is_error") if isinstance(payload, dict) else "?",
                    [
                        item.get("type")
                        for item in (payload.get("raw") or [])
                        if isinstance(item, dict)
                    ] if isinstance(payload, dict) else "?",
                )
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "MCP browser_take_screenshot 失败（回退到 Playwright）：%s", exc,
                )

        # 通道 B：Playwright page.locator(selector).screenshot() —— 兜底，
        # AI 没传 selector 就到不了这里，但保留给未来"selector-only"调用方。
        if config.captcha_selector and bundle.context is not None:
            try:
                pages = bundle.context.pages
                if not pages:
                    return None
                page = pages[0]
                locator = page.locator(config.captcha_selector)
                return await locator.screenshot(type="png")
            except Exception as exc:  # noqa: BLE001
                logger.info("Playwright locator.screenshot 失败：%s", exc)

        return None

    async def _try_refresh(
        self,
        bundle: "BrowserBundle",
        config: CaptchaConfig,
    ) -> bool:
        """点击刷新验证码按钮。两条通道都失败返回 False。

        没配 refresh ref/selector 时返回 True —— 视为"调用方就是想直接重试
        同一张图（让 OCR 多试几次）"，不阻塞流程。
        """
        if not (config.refresh_ref or config.refresh_selector):
            return True

        # 通道 A：MCP browser_click(ref=...)
        if config.refresh_ref and not bundle.mcp_unavailable and bundle.mcp_bridge:
            try:
                await bundle.mcp_bridge.call_tool(
                    "browser_click", {"ref": config.refresh_ref},
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("MCP refresh 失败（回退）：%s", exc)

        # 通道 B：Playwright page.locator(selector).click()
        if config.refresh_selector and bundle.context is not None:
            try:
                pages = bundle.context.pages
                if not pages:
                    return False
                await pages[0].locator(config.refresh_selector).click()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Playwright refresh 失败：%s", exc)

        return False

    # ── OCR：CPU-bound 转线程池 ──────────────────────────────────────

    async def _classify(
        self, img_bytes: bytes, model: Literal["default", "beta"] = "default",
    ) -> str:
        """调 ddddocr.classification；CPU-bound 走 executor 不阻塞 loop。"""
        ocr = self._get_ocr(model)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, ocr.classification, img_bytes)

    # ── Tool 注册（让 LLM 在 ai_login 流程中能调）───────────────────

    def register_for_execution(
        self,
        execution_id: uuid.UUID | str,
        bundle: "BrowserBundle",
        *,
        default_max_retries: int = 3,
        default_expected_length: int | None = None,
        default_expected_pattern: str | None = None,
    ) -> str:
        """注册 ``platform_solve_captcha`` tool，返回 namespaced tool name。

        这套 tool 名约定：``<execution_id>__platform_solve_captcha``。
        与 Task 7.2 ``MCPBridge.unregister(execution_id)`` 共享相同的命名空间，
        execution 结束统一 ``unregister_namespace(str(execution_id))`` 即可清理。

        注：分隔符使用 ``__``（双下划线）。OpenAI / 兼容 Chat 接口要求
        ``tools[i].function.name`` 严格匹配 ``^[a-zA-Z0-9_-]+$``，``:`` 会被拒。
        """
        ns = str(execution_id)
        name = f"{ns}__platform_solve_captcha"

        async def _executor(args: dict[str, Any]) -> dict[str, Any]:
            try:
                config = CaptchaConfig(
                    mode=args.get("mode", "ocr"),
                    model=args.get("model", "default"),
                    bypass_value=args.get("bypass_value"),
                    captcha_ref=args.get("captcha_ref"),
                    captcha_selector=args.get("captcha_selector"),
                    refresh_ref=args.get("refresh_ref"),
                    refresh_selector=args.get("refresh_selector"),
                    max_retries=int(args.get("max_retries") or default_max_retries),
                    expected_length=args.get("expected_length") or default_expected_length,
                    expected_pattern=args.get("expected_pattern") or default_expected_pattern,
                    refresh_wait_ms=int(args.get("refresh_wait_ms") or 300),
                )
            except (ValueError, TypeError) as exc:
                return {
                    "success": False, "text": None, "attempts": 0,
                    "error": f"config_error: {exc}",
                }

            try:
                result = await self.solve(bundle, config)
            except Exception as exc:  # noqa: BLE001
                logger.exception("platform_solve_captcha tool error")
                return {
                    "success": False, "text": None, "attempts": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return {
                "success": result.success,
                "text": result.text,
                "attempts": result.attempts,
                "reason": result.reason,
            }

        agent_tools.register_tool(name, _executor)
        return name


# ─── OpenAI tool schema ──────────────────────────────────────────────


def platform_solve_captcha_openai_schema(execution_id: uuid.UUID | str) -> dict[str, Any]:
    """返回 OpenAI function-calling schema。

    Task 9.4 ExecutionEngine 在构造 ``tools=[...]`` 列表时把这个 schema 加进去，
    让模型知道有这个 tool 可调；具体实现已在 ``register_for_execution`` 注册到
    ``agent_tools.TOOL_REGISTRY``。
    """
    name = f"{execution_id}__platform_solve_captcha"
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": (
                "识别页面上的验证码图片。优先传 captcha_ref（来自 browser_snapshot 的元素 ref），"
                "也可改传 captcha_selector（CSS/XPath）。可选传 refresh_ref/refresh_selector "
                "在识别失败时点击刷新按钮重试。"
                "**重要**：识别中文验证码必须传 model='beta'（ddddocr 默认模型只准英数字）。"
                "返回 success/text/attempts/reason。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string", "enum": ["bypass", "ocr"],
                        "description": "默认 ocr。bypass 用于测试环境的固定万能码场景。",
                    },
                    "model": {
                        "type": "string", "enum": ["default", "beta"], "default": "default",
                        "description": (
                            "OCR 模型：default=英数字优化（纯字母数字验证码精度 90%+），"
                            "beta=中文/混合优化（包含汉字、复杂背景的验证码用这个）。"
                            "看 placeholder「请输入图中文字」「图中汉字」或 snapshot 里 alt 含\"中文\"等\n"
                            "提示，应当传 beta。"
                        ),
                    },
                    "bypass_value": {"type": "string"},
                    "captcha_ref": {"type": "string", "description": "browser_snapshot 元素 ref"},
                    "captcha_selector": {"type": "string", "description": "CSS / XPath selector"},
                    "refresh_ref": {"type": "string"},
                    "refresh_selector": {"type": "string"},
                    "max_retries": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                    "expected_length": {"type": "integer", "minimum": 1},
                    "expected_pattern": {
                        "type": "string",
                        "description": (
                            "Python regex fullmatch；纯数字写 '^\\\\d{4}$'，"
                            "中文验证码不要传（汉字组合无法用简单 regex 描述）"
                        ),
                    },
                    "refresh_wait_ms": {
                        "type": "integer", "minimum": 0, "maximum": 10000, "default": 300,
                    },
                },
                "required": [],
            },
        },
    }


# ─── 异常 ────────────────────────────────────────────────────────────


class CaptchaUnavailableError(RuntimeError):
    """OCR 引擎或抓图通道完全不可用 —— 与"识别失败"区分。

    "ddddocr 没装"是这种；"识别了但结果不满足 expected_length"是普通失败
    （重试还有救）。
    """


# 兼容别名（短名称读起来更像"业务状态"而非异常；保留一年后再删）
CaptchaUnavailable = CaptchaUnavailableError


# ─── helpers ─────────────────────────────────────────────────────────


def _validate_text(text: str, config: CaptchaConfig) -> tuple[bool, str | None]:
    """约束校验。返回 ``(ok, reason_if_not_ok)``。"""
    if config.expected_length is not None and len(text) != config.expected_length:
        return False, f"长度 {len(text)} != expected {config.expected_length}"
    if config.expected_pattern is not None:
        if not re.fullmatch(config.expected_pattern, text):
            return False, f"不匹配 pattern {config.expected_pattern!r}"
    return True, None


def _extract_image_bytes(payload: Any) -> bytes | None:
    """从 MCP ``browser_take_screenshot`` 返回的 payload 里抠出图片字节流。

    实际的返回形态由 ``MCPBridge.call_tool`` 统一归一化（见 mcp_bridge.py
    第 ~290 行）：

    .. code-block:: json

        {
            "content": "<合并的纯文本>",
            "is_error": false,
            "raw": [
                {"type": "image", "mime_type": "image/png", "data": "<base64>", ...},
                {"type": "text", "text": "..."}
            ]
        }

    所以**重点要在 ``payload["raw"]`` 数组里找 image block**——这是历史 bug：
    旧实现只看 dict 顶层 ``image_bytes`` / ``data`` / ``content`` 这些 key，永远
    抠不到 raw 里的图片，captcha_solver 全程返回 no_image。

    其它形态保留兼容（bytes / str / 顶层 base64 / list 形态），让 stub /
    自定义 MCP 实现也能用。
    """
    import base64

    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        try:
            return base64.b64decode(payload)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(payload, dict):
        # ⭐ 主路径：MCPBridge 归一化后的结构 —— image 在 ``raw`` 里
        raw = payload.get("raw")
        if isinstance(raw, list):
            got = _extract_image_bytes(raw)
            if got:
                return got
        for k in ("image_bytes", "data", "content"):
            v = payload.get(k)
            if isinstance(v, bytes) and v:
                return v
        for k in ("image_base64", "image", "base64", "data", "content"):
            v = payload.get(k)
            if isinstance(v, str) and v:
                try:
                    return base64.b64decode(v)
                except Exception:  # noqa: BLE001
                    pass
    if isinstance(payload, list):
        for block in payload:
            if isinstance(block, dict) and block.get("type") == "image":
                got = _extract_image_bytes(block)
                if got:
                    return got
    return None


__all__ = [
    "CaptchaConfig",
    "CaptchaResult",
    "CaptchaSolver",
    "CaptchaUnavailableError",
    "CaptchaUnavailable",  # 兼容别名
    "platform_solve_captcha_openai_schema",
]
