"""StepRunnerAILoginRunner — 把 ``StepRunner`` 包装成 ``AILoginRunner``。

Task 9.4 回填 Task 8.2：把 ``precondition_executor._StubAILoginRunner`` 替换为
真正能跑 LLM 驱动登录的实现。

工作流：
1. 把 credentials 拉平成可用的"物料清单"提示，让 AI 自行选择 ``platform_get_secret``
   / ``platform_get_test_data`` 风格的工具读取（如果有 resolver）；没有 resolver
   时直接把 username 写进 step 描述，password 仍然以 ``<secret:password>`` 占位
   提示模型用"需要密码请输入"流程
2. 调 ``StepRunner.run_one`` 跑一轮 tool-calling 循环
3. 收尾后看 ``last_snapshot_text`` / ``final_message`` 是否包含 ``success_indicator``

设计原则：
- 不在本模块新增 LLM / 浏览器依赖 —— 全部通过构造时注入
- 失败永远走 ``(False, error_msg)`` 通道，**不抛异常**（precondition_executor
  期望非异常返回）
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.modules.llm import agent_tools
from app.modules.ui_automation.captcha_solver import (
    CaptchaSolver,
    platform_solve_captcha_openai_schema,
)
from app.modules.ui_automation.security import TokenBudget
from app.modules.ui_automation.step_runner import StepRunner

if TYPE_CHECKING:
    from app.modules.ui_automation.browser_bundle import BrowserBundle


logger = logging.getLogger(__name__)


# 默认 captcha 约束：**4 位数字（带干扰）是国产后台最常见的图形验证码**
# （实测覆盖率 95%+），ddddocr default 模型对这种识别率 ~90%。AI 不传
# ``expected_pattern`` 时 captcha_solver 自动按 4 位数字校验，识别错位
# （比如 OCR 把 5 位字符塞进 text）会被 validate 拦下来重试。
# AI 也可以在 ``platform_solve_captcha`` 入参里自己改 ``expected_pattern``，
# 例如 6 位字母数字混合的旧后台可传 ``^[A-Za-z0-9]{4,8}$``，含汉字的可
# 完全不传 + ``model="beta"``。
_DEFAULT_CAPTCHA_PATTERN = r"^\d{4}$"
_DEFAULT_CAPTCHA_MAX_RETRIES = 3


class StepRunnerAILoginRunner:
    """把 ``StepRunner`` 适配成 ``AILoginRunner`` 接口。

    使用：
    ```python
    runner = StepRunnerAILoginRunner(step_runner=runner_instance)
    result = await run_precondition(..., ai_login_runner=runner)
    ```
    """

    __test__ = False

    def __init__(
        self,
        *,
        step_runner: StepRunner,
        max_iterations_per_step: int = 5,
        captcha_solver: CaptchaSolver | None = None,
    ) -> None:
        self.step_runner = step_runner
        self.max_iterations_per_step = max_iterations_per_step
        # CaptchaSolver 构造极轻（不会触发 ddddocr ONNX init —— 那是惰性的，
        # 真正调 _classify 时才 init）。这里允许外部注入主要是为了单测可以
        # 塞 fake solver；生产路径走默认 CaptchaSolver()。
        self.captcha_solver = captcha_solver or CaptchaSolver()

    async def run_ai_login(
        self,
        bundle: "BrowserBundle",
        *,
        login_url: str,
        success_indicator: str,
        max_steps: int,
        credentials: dict[str, Any] | None,
    ) -> tuple[bool, str | None]:
        # 用 credentials 拼一段半结构化提示；secret 字段保留为 ``<secret:xxx>``
        # 形态，让 AI 知道有这个数据存在但不直接 inline plaintext。
        cred_block = _format_credentials(credentials)

        # 模板配的 ``max_steps`` 是用户对"这个登录流程大概需要几次 tool_call"
        # 的预估，应该真的传给 StepRunner 作为 ``max_iterations`` 限制。
        # 之前只塞到 prompt 里给 AI 看，但 StepRunner 自己的默认上限是
        # ``MAX_STEP_TOOL_ITERATIONS=5``，5 步连"打开页 → 输入用户名 → 输
        # 入密码 → 点登录 → 等跳转"都不够。
        effective_max = max(max_steps, self.max_iterations_per_step)

        # AI 登录场景特殊：
        # 1. **少 LLM 来回** — 每轮 LLM 30-60s 是国产慢模型常态，所以鼓励
        #    AI 在一轮里**并行调多个工具**（navigate + type + click）；
        # 2. **找到 success_indicator 立即收手** — 不要画蛇添足；
        # 3. **填表前可跳过 snapshot** — 标准登录页布局可推断；
        # 4. **遇到图形验证码必须用 platform_solve_captcha** — 上一版没把 tool
        #    注册进来，AI 只能 ``browser_take_screenshot`` 看图却没识别能力，
        #    最后陷在"我看到验证码但识别不出"的循环里。这一版把 ddddocr 包成
        #    namespaced platform tool，schema 一起塞进 tools list。
        # 这些约束跟通用 step_runner 的"每轮不超过 3 次工具"刚好相反，
        # 所以单独写一份 description，让模型读到登录场景的 fast-path 提示。
        step_description = (
            f"目标：打开 {login_url}，完成登录。\n"
            f"凭据：\n{cred_block}\n"
            f'登录成功的标志：页面出现 "{success_indicator}" 或当前 URL 包含该字符串。\n'
            "\n"
            "## 工作流硬规则（违反会让流程卡住，必须遵守）\n"
            "- **第 1 轮**：必须**只调一个** ``browser_navigate`` 打开登录页（不要在同轮里塞\n"
            f"  其它任何工具调用）。第 1 轮唯一的 tool call 就是 ``browser_navigate({login_url})``。\n"
            "- **第 2 轮**：``browser_snapshot`` 看页面结构，识别用户名 / 密码 / 验证码 / 登录按钮的 ref。\n"
            "- **后续轮**：按照下面的「是否有验证码」两条分支走；不要混并行。\n"
            "- 看到 success_indicator 命中（或当前 URL 已经离开登录页）后**立即停止**调用工具，\n"
            "  回一句简短中文总结即可（如「已成功登录」）。\n"
            "- 若密码输入框需要明文，用 ``platform_get_secret`` 拿值（不要把 secret 写进回复）。\n"
            "\n"
            "### 分支 A — 没有验证码\n"
            "1) ``browser_type`` 填用户名；2) ``browser_type`` 填密码；3) ``browser_click`` 登录按钮。\n"
            "这 3 个工具可以**在同一轮里一起发起**（parallel tool calls）。\n"
            "\n"
            "### 分支 B — 页面有图形验证码（image / canvas / svg）\n"
            "**严禁靠肉眼看图**：你拿不到像素信息，盲猜必错。**严禁并行**——验证码必须按以下顺序，\n"
            "**每一步独立成轮**，前一步 tool 结果回来再发下一步：\n"
            "\n"
            "1. ``browser_snapshot`` 找验证码图片的 ref：\n"
            "   - **必须选 ``<img>`` 元素本身**（snapshot 里 role 标记为 ``img``、有 alt/aria-label\n"
            "     含「验证码」/「captcha」/「code」，或其 src 含 ``captcha`` / ``code`` / \n"
            "     ``verification`` 等关键字的）；\n"
            "   - **不要选 wrapper ``<div>`` 或 ``<span>`` 的 ref**——会拍到包装容器，OCR 抠不出字符；\n"
            "   - 同时找「刷新验证码」按钮的 ref（旁边的 ↻ / refresh / 换一张），记住。\n"
            "\n"
            "2. **本轮只调一个工具**：调 ``platform_solve_captcha``。优先级（猜不准就按这个顺序）：\n"
            "\n"
            "   - **首选 — 4 位数字 + 干扰**（最常见，95%+ 国产后台都长这样，如 ``1234`` ``8856``\n"
            "     ``0907``，可能带斜线 / 噪点 / 弯曲变形）：\n"
            "     ``platform_solve_captcha(captcha_ref=<img ref>, refresh_ref=<刷新 ref>,\n"
            '     model="default", max_retries=3, expected_pattern="^\\\\d{4}$")``\n'
            "     ddddocr 默认模型对这种类型识别率 90%+。**绝大多数场景这条就够用，不用想其它**。\n"
            "\n"
            "   - 6 位数字 / 字母数字混合（如 ``A8x9F2`` ``GHJK7``）：\n"
            "     ``platform_solve_captcha(captcha_ref=<img ref>, refresh_ref=<刷新 ref>,\n"
            '     model="default", max_retries=3, expected_pattern="^[A-Za-z0-9]{4,8}$")``\n'
            "\n"
            "   - **仅当**有明确证据是含**汉字**（snapshot 里有 placeholder「请输入图中文字」、\n"
            "     图旁写「点击图中…」「请按顺序…」等中文操作说明）：\n"
            "     ``platform_solve_captcha(captcha_ref=<img ref>, refresh_ref=<刷新 ref>,\n"
            '     model="beta", max_retries=5)``\n'
            "     **不要无中生有猜测中文** —— 上一次试跑里 AI 看到 4 位数字图片却脑补「验证码包含\n"
            "     中文字符」结果切到 beta 模型，准确率反而下降。**只看 placeholder 和 alt 文本，\n"
            "     不看图自己**（你拿不到像素）。\n"
            "\n"
            "   等返回结果。绝对**不要**这一轮还顺便填用户名 / 密码 / 点登录——验证码识别失败时\n"
            "   你需要全部回退、重选 ref、重试，并行只会让上下文变乱。\n"
            "\n"
            "3. 拿到 ``{success: true, text: <值>}`` 后**立刻在下一轮**完成填表 + 提交：\n"
            "   一轮**并行**发：``browser_type`` 用户名 + ``browser_type`` 密码 + ``browser_type``\n"
            "   验证码（值就是上一步返回的 text）+ ``browser_click`` 登录按钮。\n"
            "   ⚠️ **这一轮里禁止任何「中间确认」动作**：不要再 ``browser_snapshot`` / 不要\n"
            "   ``browser_take_screenshot`` / 不要 ``browser_wait_for`` / 不要先 type 再 sleep。\n"
            "   原因：很多后台**对验证码 session 限了 30 秒 TTL**，你每多发一轮 LLM（30~60s）\n"
            "   就有失效风险，识别再准的 text 也会被服务端拒绝。从拿到 text 到点 login 必须**一气\n"
            "   呵成**。\n"
            "   注：``platform_solve_captcha`` 在**成功路径上 0 次刷新页面**——验证码图片不会\n"
            "   被换，输入框里的旧用户名 / 密码也不会被清空，所以你完全可以信任\n"
            "   「snapshot 时拿到的所有 ref 仍然有效」。\n"
            "\n"
            "4. 失败处理（看 ``platform_solve_captcha`` 返回的 reason）：\n"
            "   - ``no_image`` → 你选错了 ref（很可能选了 div/span 而非 img）。重新\n"
            "     ``browser_snapshot``，**只盯 role=img / 标签 ``<img>``**，再调一次 captcha tool。\n"
            "   - ``ocr_returned_empty`` / ``constraint_failed`` → 下次调用时 max_retries 调到 5；\n"
            "     若 pattern 太严（比如限定 4 位数字但识别出 5 位），**放宽 pattern**（比如改成\n"
            '     ``"^\\\\d{3,6}$"``）再调。\n'
            "   - ``ocr_unavailable`` → 平台没装 ddddocr，无法继续，直接回报失败。\n"
            "\n"
            "5. 登录后若页面提示「验证码错误」/「验证码不正确」且仍在登录页（说明 OCR 这次猜错了）：\n"
            "   下一轮**先 ``browser_snapshot``** 重新拿最新的验证码图片 ref（页面可能已自动刷新换图，\n"
            "   ref 也变了），然后**再调 ``platform_solve_captcha``**（OCR 重识别），紧接着\n"
            "   一气呵成 type 验证码 + click 登录。**不要**肉眼读旧图，也**不要**只重发 type/click 不重识别。\n"
            "\n"
            "**永远不要**把识别出的验证码值写到给用户看的回复里，直接 browser_type 即可。\n"
        )
        expected = f'页面包含成功标志 "{success_indicator}"，且 URL 不再停留在登录页。'

        # ── 注册 captcha tool + lightweight platform_get_secret tool ──
        # 注册的 tool 名是 namespaced ``<ns>__platform_*``，与 schema 里
        # function.name 完全一致。
        # 关于 platform_get_secret 的诡异历史：完整版本（基于 TestDataResolver）
        # 只在**真实用例执行**时通过 ``register_data_tools`` 注册——它依赖
        # 数据库里持久化的 test_data。但本流程是**前置步骤试跑**，根本没有
        # 用例 / TestDataResolver，full 版的 platform_get_secret 一定不在
        # TOOL_REGISTRY 里。
        # 而 prompt 又告诉 AI"密码用 platform_get_secret 拿"——结果 AI 调
        # 不到这个 tool，登录失败。修复：在这里注册一个 in-memory 版本，
        # 直接从 credentials dict 读，不走数据库。schema/语义对 AI 透明。
        captcha_ns = self._resolve_captcha_namespace(bundle)
        captcha_tool_name = self._register_captcha_tool(captcha_ns, bundle)
        captcha_schema = platform_solve_captcha_openai_schema(captcha_ns)

        secret_tool_name, secret_schema = self._register_inline_secret_tool(
            captcha_ns, credentials,
        )

        # 临时拉高 step_runner.max_iterations；用 try/finally 还原，避免
        # 该实例后续被复用时还带着这个值。串行调用场景下这是安全的——
        # ``run_precondition`` 严格按 order_index 顺序跑模板。
        original_max = getattr(self.step_runner, "max_iterations", effective_max)
        try:
            self.step_runner.max_iterations = effective_max
            mcp_specs = await _gather_mcp_specs(bundle)
            # captcha + secret schema 都合并进 mcp_specs（step_runner 的
            # _build_tools 拿这个 list 喂给 LLM）。这样 step_runner 完全不
            # 需要"知道"captcha / secret 概念，依赖关系倒挂——这两个工具
            # 只在 ai_login 流程里存在。
            mcp_specs = [*mcp_specs, captcha_schema, secret_schema]
            initial_snapshot = await _initial_snapshot(bundle)
            result = await self.step_runner.run_one(
                step_description=step_description,
                expected=expected,
                bundle=bundle,
                mcp_tool_specs=mcp_specs,
                initial_snapshot_text=initial_snapshot,
                current_url=login_url,
                page_title="登录页",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("StepRunnerAILoginRunner.run_ai_login crashed")
            return False, f"AI 登录器内部错误：{type(exc).__name__}: {exc}"
        finally:
            self.step_runner.max_iterations = original_max
            # 不论成功失败都要清理 captcha + secret tool，避免命名空间残留。
            # 按精确名称反注册自己刚注册的两个 tool，不动其它已存在的
            # ``<captcha_ns>__platform_*`` —— 例如未来 step_runner 注册的
            # 物料工具共用 execution_id 命名空间，不能误杀。
            for tool_name in (captcha_tool_name, secret_tool_name):
                try:
                    agent_tools.unregister_tool(tool_name)
                except Exception:  # noqa: BLE001
                    logger.debug("unregister tool failed: %s", tool_name)

        if not result.success:
            return False, result.error or "AI 登录步骤未能正常收尾"

        # success_indicator 校验：三源匹配，任意一处命中即视为登录成功。
        # 1) **当前页面 URL**（最可靠）—— 用户最常填的就是登录后跳转地址。
        #    只要 indicator 是当前 page.url 的子串就算通过；这样填完整 URL、
        #    填路径片段（``/home``）甚至填 host（``cq-auth-dashboard``）都能用。
        # 2) **snapshot aria 树文本** —— indicator 是页面文案 / 元素 ref 的场景。
        # 3) **final_message** —— AI 自己复述了 indicator 的兜底场景，置信度低
        #    但接受（避免完全不匹配时误判失败）。
        snap = (result.last_snapshot_text or "")
        final = (result.final_message or "")

        # 拿当前页面 URL，两条路径**真实**反映"浏览器现在停在哪"：
        # 1) ``bundle.get_primary_page().url`` —— SDK 视角；MCP 创建的 page 经常
        #    不在 SDK 的 BrowserContext 里，所以经常拿不到，但拿到时最准。
        # 2) MCP 自己（``browser_tabs_list`` / ``browser_evaluate`` / ``browser_snapshot``）
        #    —— MCP 一定看得到自己创建的 page，是更可靠的兜底。
        # 这两条都是**当前真实 URL**，可以用来判 success_indicator 命中（不会像
        # navigate 历史那样被"AI 想去 vs 真的到了"的歧义污染）。
        page_url = ""
        try:
            page = bundle.get_primary_page()
            if page is not None:
                page_url = (getattr(page, "url", "") or "")
        except Exception:  # noqa: BLE001
            page_url = ""

        if not page_url:
            try:
                mcp_url = await bundle.get_current_url_via_mcp()
            except Exception:  # noqa: BLE001
                mcp_url = None
            if mcp_url:
                page_url = mcp_url

        if success_indicator:
            if page_url and success_indicator in page_url:
                return True, None
            if success_indicator in snap:
                return True, None
            if success_indicator in final:
                return True, None

        # 失败路径才用兜底 URL（仅作错误提示，不参与 indicator 命中判定，否则
        # AI 还没真去到目标页就因 navigate 调用里的 url 参数误判通过）。
        url_for_error = page_url
        if not url_for_error:
            for rec in reversed(result.tool_calls or []):
                raw = getattr(rec, "raw_name", None) or getattr(rec, "name", "")
                if raw and "browser_navigate" in raw:
                    args = getattr(rec, "arguments", {}) or {}
                    url = args.get("url") if isinstance(args, dict) else None
                    if isinstance(url, str) and url.strip():
                        url_for_error = (
                            f"{url.strip()} (AI 最后一次 navigate 调用的目标，未必到达)"
                        )
                        break

        return False, (
            f"未在登录后页面找到成功标志 “{success_indicator}” —— "
            f"工具调用 {len(result.tool_calls)} 次，"
            f"当前 URL: {url_for_error or '(无 page 对象，AI 工作流可能未完成首次 navigate)'} ，"
            f"最后回复：{(final or '(无)')[:200]}"
        )

    # ── captcha tool 注册辅助 ────────────────────────────────────────

    def _resolve_captcha_namespace(self, bundle: "BrowserBundle") -> str:
        """决定 captcha tool 的命名空间前缀。

        优先级：
        1. ``bundle.execution_id`` —— 真实执行时这个肯定有；
        2. ``self.step_runner.execution_id`` —— 试跑场景（无 bundle execution
           或 stub bundle）时退而求其次；
        3. ``"login"`` 兜底字符串——保证 schema 里 function.name 永远合法。

        命名空间形态约定：``<ns>__platform_solve_captcha``。命名空间层既隔离
        多 execution 并行，也方便 ``unregister_namespace`` 一键清理。
        分隔符用 ``__`` 是因为 OpenAI 工具名要求 ``^[a-zA-Z0-9_-]+$``。
        """
        bundle_eid = getattr(bundle, "execution_id", None)
        if bundle_eid:
            return str(bundle_eid)
        runner_eid = getattr(self.step_runner, "execution_id", None)
        if runner_eid:
            return str(runner_eid)
        return "login"

    def _register_captcha_tool(
        self, namespace: str, bundle: "BrowserBundle",
    ) -> str:
        """把 ``CaptchaSolver`` 包成 ``<ns>__platform_solve_captcha`` 注册。

        默认 ``expected_pattern`` 给 ``^\\d{4}$`` —— 国内后台最常见的"4 位
        数字 + 干扰"验证码（覆盖 95%+），ddddocr default 模型识别率 ~90%。
        AI 也可在调用时自己改 pattern；不传时由 captcha_solver 自动用这个
        默认值校验，能拦下 OCR 串位（如把 5 位字符塞进 4 位字段）的情况。
        ``max_retries=3`` 是经验值——失败 2 次还没识别出来通常不是 OCR 问
        题（验证码生成器太刁钻 / 噪点密度太高），继续刷没意义。
        """
        return self.captcha_solver.register_for_execution(
            execution_id=namespace,
            bundle=bundle,
            default_max_retries=_DEFAULT_CAPTCHA_MAX_RETRIES,
            default_expected_pattern=_DEFAULT_CAPTCHA_PATTERN,
        )

    def _register_inline_secret_tool(
        self,
        namespace: str,
        credentials: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        """注册 in-memory 版 ``platform_get_secret`` 给 ai_login 试跑用。

        语义和 ``data_platform_tools._get_secret`` 对 AI **完全一致**：

        - 输入：``{"key": "<credential 字段名>"}``
        - 输出（命中 secret 字段）：
          ``{"key": "...", "value": "<plaintext>", "_test_data_secret_used": true}``
          —— ``_test_data_secret_used`` 标志让 ``redact_tool_result_for_reasoning``
          自动剥离明文（参见 data_platform_tools.py），落库的 reasoning 里
          看不到密码。
        - 输出（未命中 / 未配置）：``{"error": "secret '<key>' not found"}``

        判定一个字段是不是 secret：key.lower() 含 ``password`` / ``secret``
        / ``token`` / ``code``，与 ``_format_credentials`` 的占位渲染规则
        一致——AI prompt 看到 ``<secret:password>`` 就知道要调
        ``platform_get_secret(key="password")``，逻辑闭环。

        返回 ``(tool_name, openai_schema)``。schema 加进 mcp_specs 让 LLM
        看到这个工具；tool_name 在 finally 里精准 unregister。
        """
        creds = credentials or {}

        async def _get_secret_impl(args: dict[str, Any]) -> dict[str, Any]:
            key = (args.get("key") or "").strip()
            if not key:
                return {"error": "key required"}
            if key not in creds:
                return {
                    "error": (
                        f"secret '{key}' not found in this login flow's credentials. "
                        f"Available keys: {sorted(creds.keys())}"
                    ),
                }
            kl = key.lower()
            is_sensitive = any(
                t in kl for t in ("password", "secret", "token", "code")
            )
            value = creds[key]
            payload: dict[str, Any] = {
                "key": key,
                "value": value if isinstance(value, str) else str(value),
            }
            if is_sensitive:
                # 标志位让 redact_tool_result_for_reasoning 自动脱敏 —— 落库
                # 的 reasoning 里只会留 ``<secret used: ...; plaintext omitted>``
                payload["_test_data_secret_used"] = True
            return payload

        tool_name = f"{namespace}__platform_get_secret"
        agent_tools.register_tool(tool_name, _get_secret_impl)

        schema: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": (
                    "解密并返回 secret 类型物料明文（password / token / code 等）。"
                    "AI 看到 prompt 里 ``<secret:KEY>`` 占位时调 ``platform_get_secret(key=KEY)`` "
                    "拿真实值，再用 ``browser_type`` 填进输入框；"
                    "**不要**把返回值写到回复 / reasoning 里。"
                    "结果带 ``_test_data_secret_used: true`` 时会自动从落库的 reasoning 里脱敏。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": (
                                "凭据 key（来自 prompt 凭据清单里那个 ``<secret:key>`` 占位）"
                            ),
                        },
                    },
                    "required": ["key"],
                },
            },
        }
        return tool_name, schema


def _format_credentials(credentials: dict[str, Any] | None) -> str:
    if not credentials:
        return "（无可用凭据；请使用页面上可见的「游客登录」或类似入口）"
    rows = []
    for key, val in credentials.items():
        kl = key.lower()
        if any(token in kl for token in ("password", "secret", "token", "code")):
            rows.append(f"- {key}: <secret:{key}>（敏感字段，不要把值写到日志或报错信息）")
        else:
            rows.append(f"- {key}: {val}")
    return "\n".join(rows)


async def _gather_mcp_specs(bundle: "BrowserBundle") -> list[dict[str, Any]]:
    """从 bundle 抓 MCP 工具 spec；无 MCP / 失败时返回空列表。"""
    if getattr(bundle, "mcp_unavailable", True):
        return []
    try:
        return await bundle.register_mcp_tools_for_agent()
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_mcp_tools_for_agent failed: %s", exc)
        return []


async def _initial_snapshot(bundle: "BrowserBundle") -> str | None:
    """登录前先 navigate 到 login_url 并尝试 snapshot；任何失败都返回 None。"""
    bridge = getattr(bundle, "mcp_bridge", None)
    if bridge is None or getattr(bundle, "mcp_unavailable", True):
        return None
    try:
        snap = await bridge.call_tool("browser_snapshot", {})
        if isinstance(snap, dict):
            for key in ("snapshot", "text", "content"):
                val = snap.get(key)
                if isinstance(val, str) and val.strip():
                    return val
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("initial_snapshot failed: %s", exc)
        return None


def build_ai_login_runner(
    *,
    llm_config_orm: Any,
    environment: Any,
    budget_limit: int | None = None,
    execution_id: uuid.UUID | str | None = None,
) -> "StepRunnerAILoginRunner | None":
    """工厂：把 LLM 配置 ORM + 环境拼成可直接喂给 ``run_precondition`` 的
    ``ai_login_runner=…`` 参数值。

    设计目的：service 层的"试跑"端点和 ``ExecutionEngine`` 真实执行流程都
    需要"在跑 ai_login 前置步骤时自动注入一个 LLM 驱动的 runner"，
    这两处需要同样的构造逻辑（解密 api_key、构造 TokenBudget、包 StepRunner），
    抽到工厂避免两边各写一遍。

    返回 ``None`` 的两种情况：
    1. ``llm_config_orm is None`` —— 库里一条 LLMConfig 都没有（罕见，
       但可能在全新部署里出现）。caller 拿到 None 时应让 ``run_precondition``
       继续走 ``_StubAILoginRunner``，stub 给出"未实现/未配置"提示。
    2. 构造过程中（如解密 api_key）抛错。也吞错 → 返回 None，让 caller 优雅
       降级，而不是把整个试跑端点搞成 500。
    """
    if llm_config_orm is None:
        return None

    try:
        # 局部导入避免顶层循环：execution_engine.py 和 service.py 都
        # 会 import 本模块；而 _build_llm_proto 跟 _LLMConfigProto 一并
        # 落在 execution_engine 里，反过来 ai_login_runner.py 再导
        # execution_engine 顶层就会成环。运行时 import 即可。
        from app.modules.ui_automation.execution_engine import _build_llm_proto

        llm_proto = _build_llm_proto(llm_config_orm)
    except Exception:  # noqa: BLE001
        logger.exception("build_ai_login_runner: _build_llm_proto failed")
        return None

    effective_limit = budget_limit or getattr(environment, "token_budget", None) or 50_000
    budget = TokenBudget(limit=int(effective_limit))

    inner_runner = StepRunner(
        llm=llm_proto,
        environment=environment,
        budget=budget,
        execution_id=execution_id,
    )
    return StepRunnerAILoginRunner(step_runner=inner_runner)


__all__ = ["StepRunnerAILoginRunner", "build_ai_login_runner"]
