"""Task 8.3 验证：CaptchaSolver。

测试策略：
- 99% 的测试用 mock，**不**真调 ddddocr（每次 init ~40s 不可接受）
- 用 ``CaptchaSolver._ocr_instance`` 类属性 patch 一个 fake OCR 实例
- 1 个真实 OCR 测试可选启用：``RUN_REAL_OCR=1 pytest tests/.../test_captcha.py::test_real_ocr_*``
- mock BrowserBundle.context / mcp_bridge，只关心 solver 与它们的交互契约
"""

from __future__ import annotations

import base64
import os
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.llm import agent_tools
from app.modules.ui_automation.captcha_solver import (
    CaptchaConfig,
    CaptchaResult,
    CaptchaSolver,
    CaptchaUnavailableError,
    _extract_image_bytes,
    _validate_text,
    platform_solve_captcha_openai_schema,
)

# ─── 测试夹具 ─────────────────────────────────────────────────────────


class FakeOCR:
    """模拟 ddddocr.DdddOcr 的最小 API。"""

    def __init__(self, sequence: list[str]):
        """sequence: 第 N 次调用返回 sequence[N-1]；用尽再调返回最后一个。"""
        self.sequence = list(sequence)
        self.calls: list[bytes] = []

    def classification(self, img_bytes: bytes) -> str:
        self.calls.append(img_bytes)
        if not self.sequence:
            return ""
        idx = min(len(self.calls) - 1, len(self.sequence) - 1)
        return self.sequence[idx]


@pytest.fixture
def mock_ocr(monkeypatch):
    """每个测试自带一个 FakeOCR；测试结束自动清理类属性。

    用 ``yield + monkeypatch.setattr`` 而非 ``setattr`` 是因为 monkeypatch
    会在 teardown 自动恢复，避免测试间 OCR 状态串台。
    """
    def _install(sequence: list[str]) -> FakeOCR:
        fake = FakeOCR(sequence)
        # captcha_solver 现在按模型分别缓存（_ocr_instance_default / _ocr_instance_beta），
        # 测试里把两个 slot 都 patch 成同一个 fake，让"用什么模型"对测试结果透明
        # （除非测试显式断言 fake.last_model）。
        monkeypatch.setattr(CaptchaSolver, "_ocr_instance_default", fake)
        monkeypatch.setattr(CaptchaSolver, "_ocr_instance_beta", fake)
        return fake
    return _install


def make_bundle(
    *,
    mcp_screenshot_payload=None,
    mcp_screenshot_error=None,
    mcp_unavailable=False,
    locator_screenshot_bytes: bytes | None = None,
    locator_screenshot_error: Exception | None = None,
    refresh_via_mcp_error: Exception | None = None,
    refresh_via_locator_error: Exception | None = None,
    has_pages: bool = True,
):
    """构造 BrowserBundle stub，可控所有抓图 / refresh 通道的成功失败。"""
    page = MagicMock()
    locator = MagicMock()
    if locator_screenshot_error is not None:
        locator.screenshot = AsyncMock(side_effect=locator_screenshot_error)
    else:
        locator.screenshot = AsyncMock(return_value=locator_screenshot_bytes or b"")
    if refresh_via_locator_error is not None:
        locator.click = AsyncMock(side_effect=refresh_via_locator_error)
    else:
        locator.click = AsyncMock()
    page.locator = MagicMock(return_value=locator)

    context = MagicMock()
    context.pages = [page] if has_pages else []

    mcp_bridge = None
    if not mcp_unavailable:
        mcp_bridge = MagicMock()

        async def _call_tool(name, args):
            # 真正的 playwright/mcp 工具叫 ``browser_take_screenshot`` —— 旧测试用
            # 错的工具名 ``browser_screenshot`` 也保留兼容（覆盖未来版本回退场景）
            if name in ("browser_take_screenshot", "browser_screenshot"):
                if mcp_screenshot_error is not None:
                    raise mcp_screenshot_error
                return mcp_screenshot_payload
            if name == "browser_click":
                if refresh_via_mcp_error is not None:
                    raise refresh_via_mcp_error
                return {"ok": True}
            return None

        mcp_bridge.call_tool = AsyncMock(side_effect=_call_tool)

    return SimpleNamespace(
        context=context, mcp_bridge=mcp_bridge,
        mcp_unavailable=mcp_unavailable, _page=page, _locator=locator,
    )


# ─── CaptchaConfig 边界 ──────────────────────────────────────────────


def test_config_max_retries_bounds() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        CaptchaConfig(mode="ocr", captcha_ref="r", max_retries=0)
    with pytest.raises(ValueError, match="max_retries"):
        CaptchaConfig(mode="ocr", captcha_ref="r", max_retries=11)


def test_config_expected_length_must_be_positive() -> None:
    with pytest.raises(ValueError):
        CaptchaConfig(mode="ocr", captcha_ref="r", expected_length=0)


def test_config_invalid_pattern_raises_at_construct() -> None:
    with pytest.raises(ValueError, match="pattern"):
        CaptchaConfig(mode="ocr", captcha_ref="r", expected_pattern="(unclosed")


def test_config_refresh_wait_ms_bounds() -> None:
    with pytest.raises(ValueError):
        CaptchaConfig(mode="ocr", captcha_ref="r", refresh_wait_ms=-1)
    with pytest.raises(ValueError):
        CaptchaConfig(mode="ocr", captcha_ref="r", refresh_wait_ms=10_001)


# ─── _validate_text ───────────────────────────────────────────────────


def test_validate_no_constraints_passes() -> None:
    ok, _ = _validate_text("anything", CaptchaConfig(mode="ocr", captcha_ref="r"))
    assert ok is True


def test_validate_length_pass_and_fail() -> None:
    ok, _ = _validate_text("1234", CaptchaConfig(mode="ocr", captcha_ref="r", expected_length=4))
    assert ok is True
    ok, why = _validate_text("12", CaptchaConfig(mode="ocr", captcha_ref="r", expected_length=4))
    assert ok is False and "长度" in (why or "")


def test_validate_pattern_pass_and_fail() -> None:
    cfg = CaptchaConfig(mode="ocr", captcha_ref="r", expected_pattern=r"^\d{4}$")
    assert _validate_text("1234", cfg)[0] is True
    assert _validate_text("ab12", cfg)[0] is False


# ─── _extract_image_bytes ────────────────────────────────────────────


def test_extract_image_bytes_direct_bytes() -> None:
    assert _extract_image_bytes(b"\x89PNG") == b"\x89PNG"


def test_extract_image_bytes_base64_string() -> None:
    s = base64.b64encode(b"IMG").decode()
    assert _extract_image_bytes(s) == b"IMG"


def test_extract_image_bytes_dict_bytes_field() -> None:
    assert _extract_image_bytes({"image_bytes": b"X"}) == b"X"
    assert _extract_image_bytes({"data": b"D"}) == b"D"


def test_extract_image_bytes_dict_b64_field() -> None:
    s = base64.b64encode(b"OK").decode()
    assert _extract_image_bytes({"image_base64": s}) == b"OK"
    assert _extract_image_bytes({"data": s}) == b"OK"


def test_extract_image_bytes_mcp_content_blocks() -> None:
    """MCP 标准的 content blocks 格式 [{"type":"image","data":"base64..."}]。"""
    s = base64.b64encode(b"BLOCK").decode()
    out = _extract_image_bytes([{"type": "image", "data": s, "mimeType": "image/png"}])
    assert out == b"BLOCK"


def test_extract_image_bytes_from_mcp_bridge_normalized_payload() -> None:
    """⭐ 真正的主路径：``MCPBridge.call_tool`` 归一化后的 payload 形态：

    .. code-block:: json

        {
            "content": "...",
            "is_error": false,
            "raw": [
                {"type": "image", "mime_type": "image/png", "data": "<b64>"}
            ]
        }

    历史 bug：旧实现只看 dict 顶层 ``image_bytes`` / ``data`` / ``image_base64``，
    永远抠不到 ``raw`` 数组里的 image block，captcha_solver 全程 no_image。
    这条用例守住"raw 字段必须被递归"的契约。
    """
    s = base64.b64encode(b"REAL_CAPTCHA").decode()
    payload = {
        "content": "（图片已返回，未拼到文本）",
        "is_error": False,
        "raw": [
            {"type": "image", "mime_type": "image/png", "data": s, "data_len": len(s)},
            {"type": "text", "text": "screenshot done"},
        ],
    }
    out = _extract_image_bytes(payload)
    assert out == b"REAL_CAPTCHA", (
        "MCPBridge 归一化后的 payload 必须从 ``raw`` 数组里抠 image bytes，"
        "否则 captcha_solver 在生产环境永远拿不到图（产生 no_image）。"
    )


def test_extract_image_bytes_skips_text_only_raw() -> None:
    """raw 里只有 text block 时（MCP 报错或未截到图），应该返回 None 让上层
    走 fallback / 报 ``no_image``，不能误把 text 内容当 image 字节。"""
    payload = {
        "is_error": True,
        "raw": [{"type": "text", "text": "Element not found"}],
    }
    assert _extract_image_bytes(payload) is None


def test_extract_image_bytes_returns_none_on_garbage() -> None:
    assert _extract_image_bytes(None) is None
    assert _extract_image_bytes({}) is None
    assert _extract_image_bytes("not-base64-!!!") is None  # 不是合法 b64
    assert _extract_image_bytes([{"type": "text", "text": "x"}]) is None


# ─── solve: bypass mode ───────────────────────────────────────────────


async def test_solve_bypass_returns_value() -> None:
    solver = CaptchaSolver()
    result = await solver.solve(
        bundle=make_bundle(),
        config=CaptchaConfig(mode="bypass", bypass_value="9999"),
    )
    assert result.success is True
    assert result.text == "9999"
    assert result.attempts == 1


async def test_solve_bypass_empty_value_fails() -> None:
    solver = CaptchaSolver()
    result = await solver.solve(
        bundle=make_bundle(),
        config=CaptchaConfig(mode="bypass", bypass_value=None),
    )
    assert result.success is False
    assert "bypass_value 为空" in (result.reason or "")


async def test_solve_bypass_does_not_touch_ocr(monkeypatch) -> None:
    """bypass 模式不应该触发 ddddocr 加载（即便它没装也能跑）。"""
    def _explode(*args, **kwargs):
        raise AssertionError("bypass 模式不应初始化 OCR")
    monkeypatch.setattr(CaptchaSolver, "_get_ocr", classmethod(lambda cls: _explode()))

    result = await CaptchaSolver().solve(
        bundle=make_bundle(),
        config=CaptchaConfig(mode="bypass", bypass_value="666"),
    )
    assert result.success is True


# ─── solve: ocr mode happy path ──────────────────────────────────────


async def test_solve_ocr_via_mcp_first_try(mock_ocr) -> None:
    mock_ocr(["1234"])
    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNGDATA"})
    result = await CaptchaSolver().solve(
        bundle=bundle,
        config=CaptchaConfig(mode="ocr", captcha_ref="ref-1"),
    )
    assert result.success is True
    assert result.text == "1234"
    assert result.attempts == 1
    # 必须用 ``browser_take_screenshot`` 而非 ``browser_screenshot`` —— 后者不是
    # ``@playwright/mcp`` 暴露的真实工具名，调它 100% 拿不到图。同时必须传 ``element``
    # 描述（mcp 服务端用 ref 时强制要求），否则会被服务端拒绝。
    bundle.mcp_bridge.call_tool.assert_any_await(
        "browser_take_screenshot",
        {"ref": "ref-1", "element": "captcha image", "type": "png"},
    )


async def test_solve_ocr_falls_back_to_playwright_when_mcp_fails(mock_ocr) -> None:
    mock_ocr(["abcd"])
    bundle = make_bundle(
        mcp_screenshot_error=RuntimeError("mcp boom"),
        locator_screenshot_bytes=b"PNG_VIA_PLAYWRIGHT",
    )
    result = await CaptchaSolver().solve(
        bundle=bundle,
        config=CaptchaConfig(
            mode="ocr", captcha_ref="ref-1",
            captcha_selector="img.captcha",
        ),
    )
    assert result.success is True
    assert result.text == "abcd"
    bundle._page.locator.assert_called_with("img.captcha")


async def test_solve_ocr_falls_back_when_mcp_unavailable(mock_ocr) -> None:
    mock_ocr(["xy12"])
    bundle = make_bundle(
        mcp_unavailable=True,
        locator_screenshot_bytes=b"PNG",
    )
    result = await CaptchaSolver().solve(
        bundle=bundle,
        config=CaptchaConfig(mode="ocr", captcha_selector="#cap"),
    )
    assert result.success is True
    assert result.text == "xy12"


# ─── solve: ocr mode 重试 ────────────────────────────────────────────


async def test_solve_ocr_retries_on_constraint_failure(mock_ocr) -> None:
    """前 2 次识别长度不对，第 3 次对了 → 应该返回成功，attempts=3。"""
    mock_ocr(["12", "abc12", "9876"])  # 第 3 次满足 length=4
    bundle = make_bundle(
        mcp_screenshot_payload={"image_bytes": b"PNG"},
        # 必须能 refresh，不然第 2 / 3 次没机会跑
    )
    # 给 refresh_ref 让 _try_refresh 走 MCP 通道
    config = CaptchaConfig(
        mode="ocr", captcha_ref="ref-1", refresh_ref="ref-refresh",
        max_retries=3, expected_length=4, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "9876"
    assert result.attempts == 3


async def test_solve_ocr_returns_none_after_all_retries(mock_ocr) -> None:
    """3 次都不满足约束 → success=False, attempts=3."""
    mock_ocr(["12", "ab", "x"])
    bundle = make_bundle(
        mcp_screenshot_payload={"image_bytes": b"PNG"},
    )
    config = CaptchaConfig(
        mode="ocr", captcha_ref="ref-1", refresh_ref="ref-refresh",
        max_retries=3, expected_length=4, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is False
    assert result.attempts == 3
    assert result.text is None
    assert "constraint_failed" in (result.reason or "")


async def test_solve_ocr_stops_when_refresh_fails(mock_ocr) -> None:
    """第 2 次需要刷新，但刷新失败 → 直接停，不再继续。"""
    mock_ocr(["wrong", "wont-be-called", "wont-be-called"])
    bundle = make_bundle(
        mcp_screenshot_payload={"image_bytes": b"PNG"},
        refresh_via_mcp_error=RuntimeError("click fail"),
        refresh_via_locator_error=RuntimeError("locator fail"),
    )
    config = CaptchaConfig(
        mode="ocr", captcha_ref="ref-1",
        refresh_ref="ref-r", refresh_selector="button.refresh",
        max_retries=3, expected_length=4, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is False
    assert result.reason == "refresh_failed"
    # 只调了 OCR 1 次（首次），重试因 refresh 失败而停
    fake = CaptchaSolver._ocr_instance_default  # type: ignore[attr-defined]
    assert len(fake.calls) == 1


async def test_solve_ocr_success_path_never_refreshes(mock_ocr) -> None:
    """⭐ 关键不变量：识别成功时，**绝不**调 ``_try_refresh``（哪怕 captcha
    + refresh 都配上了）。

    用户的真实诉求："验证码识别成功后到填写输入框这段时间内，页面不能换图"
    —— 一旦 captcha_solver 在成功路径上点了刷新按钮，验证码图片会换，AI 拿
    到的旧 text 就成了过期值，登录必然被拒。

    本测试守住这条不变量：
    1. 配上 refresh_ref / refresh_selector（让 _try_refresh 有事可做）
    2. OCR 第 1 次就成功
    3. 断言 mcp_bridge.call_tool 里 ``browser_click`` 一次都没被调
    4. 断言 locator.click 一次都没被调
    """
    mock_ocr(["1234"])

    # 用 patch 直接替换 _try_refresh，让它一旦被调就失败掉测试
    refresh_call_count = {"n": 0}
    original = CaptchaSolver._try_refresh

    async def _refresh_spy(self, bundle, config):
        refresh_call_count["n"] += 1
        return await original(self, bundle, config)

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG_OF_1234"})
    config = CaptchaConfig(
        mode="ocr",
        captcha_ref="captcha-img-ref",
        refresh_ref="refresh-btn-ref",       # 配上了刷新按钮 —— 但成功路径不该用它
        refresh_selector="button.refresh",
        max_retries=5,                        # 多重试不该影响
        expected_pattern=r"^\d{4}$",
        refresh_wait_ms=0,
    )
    solver = CaptchaSolver()
    solver._try_refresh = _refresh_spy.__get__(solver, CaptchaSolver)  # type: ignore[method-assign]

    result = await solver.solve(bundle=bundle, config=config)

    assert result.success is True
    assert result.text == "1234"
    assert result.attempts == 1
    assert refresh_call_count["n"] == 0, (
        f"成功路径绝不允许调 _try_refresh，实际调了 {refresh_call_count['n']} 次。"
        "若改动了 _solve_ocr 循环，必须保证 attempt=1 + 成功 return 之间不能"
        "走到刷新分支 —— 否则用户拿到的 captcha text 是过期值。"
    )

    # 双重保险：MCP browser_click（refresh_ref 路径）和 locator.click（refresh_selector
    # 路径）都不能被触发
    click_calls = [
        c for c in bundle.mcp_bridge.call_tool.await_args_list
        if c.args and c.args[0] == "browser_click"
    ]
    assert click_calls == [], (
        f"成功路径不该触发 MCP browser_click（refresh），实际触发了 {len(click_calls)} 次"
    )
    assert bundle._locator.click.await_count == 0, (
        "成功路径不该触发 Playwright SDK locator.click（refresh selector 通道）"
    )


async def test_solve_ocr_failure_then_success_only_refreshes_between(mock_ocr) -> None:
    """对偶不变量：**第 1 次失败**才会触发 refresh；**第 2 次成功**后不再 refresh。

    覆盖"识别失败 → 刷新换图 → 识别成功 → 立刻返回"的精确次数：
    refresh 调用次数应当 == 1（不是 0、也不是 2），证明：
    - 失败 → 刷新（合理：换图重试）
    - 成功后没刷新（合理：保护 text 不过期）
    """
    mock_ocr(["abc", "5678"])  # 第 1 次失败 length 不符（pattern 要 4 位数字）；第 2 次成功

    refresh_call_count = {"n": 0}
    original = CaptchaSolver._try_refresh

    async def _refresh_spy(self, bundle, config):
        refresh_call_count["n"] += 1
        return await original(self, bundle, config)

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", captcha_ref="r", refresh_ref="rb",
        max_retries=5,
        expected_pattern=r"^\d{4}$",
        refresh_wait_ms=0,
    )
    solver = CaptchaSolver()
    solver._try_refresh = _refresh_spy.__get__(solver, CaptchaSolver)  # type: ignore[method-assign]

    result = await solver.solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "5678"
    assert result.attempts == 2
    assert refresh_call_count["n"] == 1, (
        f"应该精准调 1 次 refresh（仅在失败 → 重试间），实际 {refresh_call_count['n']} 次"
    )


async def test_solve_ocr_no_refresh_config_retries_on_same_image(mock_ocr) -> None:
    """没配 refresh_ref/selector 时，_try_refresh 返回 True，允许重试同一张图。"""
    mock_ocr(["empty", "9999"])  # 第 2 次成功
    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", captcha_ref="ref-1",
        max_retries=3, expected_length=4, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "9999"
    assert result.attempts == 2


# ─── solve: ocr mode 异常路径 ────────────────────────────────────────


async def test_solve_ocr_no_image_returns_failure(mock_ocr) -> None:
    """两条通道都拿不到图 → success=False, reason=no_image。"""
    mock_ocr(["wont-be-called"])
    bundle = make_bundle(
        mcp_screenshot_payload=None,
        locator_screenshot_bytes=b"",  # 空字节
    )
    config = CaptchaConfig(
        mode="ocr", captcha_ref="r", captcha_selector="#cap",
        max_retries=2, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is False
    assert result.text is None


async def test_solve_ocr_returns_empty_string(mock_ocr) -> None:
    """OCR 返回空字符串视为本次失败，会重试。"""
    mock_ocr(["", "1234"])
    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", captcha_ref="r", expected_length=4,
        max_retries=3, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "1234"
    assert result.attempts == 2


async def test_solve_ocr_classify_exception_retries(mock_ocr) -> None:
    """classify 抛业务异常（非 CaptchaUnavailableError）→ 视为本次失败，重试。"""
    fake = mock_ocr(["1234"])
    # 让第 1 次 classify 抛错，第 2 次返回正常
    original_classify = fake.classification
    call_count = {"n": 0}

    def flaky(img):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient")
        return original_classify(img)
    fake.classification = flaky  # type: ignore[method-assign]

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", captcha_ref="r", max_retries=3, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "1234"


async def test_solve_ocr_unavailable_aborts_immediately(monkeypatch) -> None:
    """ddddocr 不可用（CaptchaUnavailableError）→ 不重试，立刻报错。"""
    def _no_ocr(cls, model="default"):
        # _get_ocr 现签名是 (cls, model) —— stub 也要带 model 关键字，
        # 否则会被当成 ocr_error: TypeError 而非 ocr_unavailable
        raise CaptchaUnavailableError("ddddocr 未安装")
    monkeypatch.setattr(CaptchaSolver, "_get_ocr", classmethod(_no_ocr))

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", captcha_ref="r", max_retries=5, refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is False
    assert "未安装" in (result.reason or "")
    assert result.attempts == 1  # 没有 5 次重试 — 立刻终止


async def test_solve_ocr_missing_target_returns_config_error() -> None:
    result = await CaptchaSolver().solve(
        bundle=make_bundle(),
        config=CaptchaConfig(mode="ocr"),  # 既没 ref 也没 selector
    )
    assert result.success is False
    assert "ocr mode 必须给" in (result.reason or "")


async def test_solve_unknown_mode_returns_config_error() -> None:
    cfg = CaptchaConfig(mode="ocr", captcha_ref="r")
    cfg.mode = "rocket_science"  # type: ignore[assignment]
    result = await CaptchaSolver().solve(bundle=make_bundle(), config=cfg)
    assert result.success is False
    assert "config_error" in (result.reason or "")


# ─── tool 注册 ───────────────────────────────────────────────────────


async def test_register_for_execution_creates_namespaced_tool(mock_ocr) -> None:
    mock_ocr(["7777"])
    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    solver = CaptchaSolver()
    exec_id = uuid.uuid4()

    name = solver.register_for_execution(exec_id, bundle)
    try:
        assert name == f"{exec_id}__platform_solve_captcha"
        assert name in agent_tools.TOOL_REGISTRY

        # 模拟 LLM 调用：传 args 走 ocr 通道
        result_str = await agent_tools.run_tool(
            name,
            '{"captcha_ref": "ref-1", "expected_length": 4, "max_retries": 1, "refresh_wait_ms": 0}',
        )
        import json
        result = json.loads(result_str)
        assert result["success"] is True
        assert result["text"] == "7777"
        assert result["attempts"] == 1
    finally:
        agent_tools.unregister_namespace(str(exec_id))
        assert name not in agent_tools.TOOL_REGISTRY


async def test_register_for_execution_handles_invalid_args(mock_ocr) -> None:
    """LLM 传入非法 max_retries → tool 执行器返回 error 而非崩溃。"""
    mock_ocr(["x"])
    bundle = make_bundle()
    solver = CaptchaSolver()
    exec_id = uuid.uuid4()
    name = solver.register_for_execution(exec_id, bundle)
    try:
        result_str = await agent_tools.run_tool(
            name,
            '{"captcha_ref": "r", "max_retries": 999}',  # 超上限
        )
        import json
        result = json.loads(result_str)
        assert result["success"] is False
        assert "config_error" in result["error"]
    finally:
        agent_tools.unregister_namespace(str(exec_id))


async def test_register_for_execution_uses_defaults(mock_ocr) -> None:
    """注册时给的 default_max_retries / default_expected_length 应在 args 缺时生效。"""
    mock_ocr(["12345"])
    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    solver = CaptchaSolver()
    exec_id = uuid.uuid4()
    name = solver.register_for_execution(
        exec_id, bundle,
        default_max_retries=1,
        default_expected_length=5,
    )
    try:
        import json
        result = json.loads(await agent_tools.run_tool(
            name, '{"captcha_ref": "r", "refresh_wait_ms": 0}',
        ))
        assert result["success"] is True
        assert result["text"] == "12345"
    finally:
        agent_tools.unregister_namespace(str(exec_id))


def test_openai_schema_shape_is_valid() -> None:
    exec_id = uuid.uuid4()
    schema = platform_solve_captcha_openai_schema(exec_id)
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == f"{exec_id}__platform_solve_captcha"
    assert "parameters" in fn
    assert fn["parameters"]["type"] == "object"
    # 必填字段为空（解释见 schema 注释）；模型自由组合
    assert fn["parameters"]["required"] == []


def test_openai_schema_exposes_model_choice() -> None:
    """schema 必须暴露 ``model: default | beta`` —— 关键回归：中文验证码必须用 beta，
    AI 看不到这个参数就只能用 default 模型，导致中文识别准确率从 ~80% 掉到 ~20%。
    """
    schema = platform_solve_captcha_openai_schema(uuid.uuid4())
    props = schema["function"]["parameters"]["properties"]
    assert "model" in props, "schema 必须暴露 model 参数（中文验证码识别必需）"
    assert props["model"]["enum"] == ["default", "beta"]
    desc = (props["model"].get("description") or "")
    assert "中文" in desc and "beta" in desc, "model 字段 description 必须告诉 AI 中文场景用 beta"


# ─── model 选择（default vs beta）────────────────────────────────────


class _RecordingFakeOCR:
    """记录每次 classification 用的是哪个模型实例（便于断言模型路由）。"""

    def __init__(self, name: str, returns: str = "测试"):
        self.name = name
        self.returns = returns
        self.calls: int = 0

    def classification(self, img_bytes: bytes) -> str:
        self.calls += 1
        return self.returns


async def test_solve_uses_default_model_when_unspecified(monkeypatch) -> None:
    """config.model 默认 default → 必须走 _ocr_instance_default，不能误用 beta。"""
    fake_default = _RecordingFakeOCR("default", returns="ABCD")
    fake_beta = _RecordingFakeOCR("beta", returns="不该被调")
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_default", fake_default)
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_beta", fake_beta)

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(mode="ocr", captcha_ref="r", refresh_wait_ms=0)
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "ABCD"
    assert fake_default.calls == 1
    assert fake_beta.calls == 0, "默认模型场景下 beta 实例不应被调用"


async def test_solve_uses_beta_model_when_specified(monkeypatch) -> None:
    """config.model='beta' → 必须走 _ocr_instance_beta，default 实例不应被调。"""
    fake_default = _RecordingFakeOCR("default", returns="错的英数字")
    fake_beta = _RecordingFakeOCR("beta", returns="春暖花开")
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_default", fake_default)
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_beta", fake_beta)

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    config = CaptchaConfig(
        mode="ocr", model="beta", captcha_ref="r", refresh_wait_ms=0,
    )
    result = await CaptchaSolver().solve(bundle=bundle, config=config)
    assert result.success is True
    assert result.text == "春暖花开"
    assert fake_beta.calls == 1
    assert fake_default.calls == 0, "beta 模式下不应误用 default 模型"


async def test_register_for_execution_passes_model_through(monkeypatch) -> None:
    """LLM 调 ``platform_solve_captcha(model="beta", ...)`` → 必须真的走 beta 实例。

    这条覆盖完整的传递链：tool args → CaptchaConfig.model → _classify(model)
    → _get_ocr(model) → 正确的 ddddocr 实例。任一环节断了 AI 选 beta 也没用。
    """
    fake_default = _RecordingFakeOCR("default", returns="not used")
    fake_beta = _RecordingFakeOCR("beta", returns="中文验证码")
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_default", fake_default)
    monkeypatch.setattr(CaptchaSolver, "_ocr_instance_beta", fake_beta)

    bundle = make_bundle(mcp_screenshot_payload={"image_bytes": b"PNG"})
    solver = CaptchaSolver()
    exec_id = uuid.uuid4()
    name = solver.register_for_execution(exec_id, bundle)
    try:
        import json
        result_str = await agent_tools.run_tool(
            name,
            '{"captcha_ref": "r", "model": "beta", "refresh_wait_ms": 0}',
        )
        result = json.loads(result_str)
        assert result["success"] is True
        assert result["text"] == "中文验证码"
        assert fake_beta.calls == 1
        assert fake_default.calls == 0
    finally:
        agent_tools.unregister_namespace(str(exec_id))


# ─── 真实 OCR 测试（默认跳过；RUN_REAL_OCR=1 启用）────────────────────


@pytest.mark.skipif(
    not os.getenv("RUN_REAL_OCR"),
    reason="跳过真实 OCR 测试（设 RUN_REAL_OCR=1 启用，首次运行约 +40s 用于 ONNX init）",
)
async def test_real_ocr_classify_simple_digits() -> None:
    """用 PIL 现造一张 4 位数字图 → 真 ddddocr.classification → 验证返回非空。

    这个测试不强求识别百分百正确（ddddocr 在自造图上准确率波动大），只验证：
    1. ddddocr 包能 import + 初始化
    2. classification 接受 PNG 字节流
    3. 返回字符串
    4. 整条 solve 链路 mock + 真 OCR 都能跑
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("PIL 未装")

    # 60×30 的灰底白字 PNG，写 "1234"
    img = Image.new("RGB", (80, 32), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 4), "1234", fill=(20, 20, 20), font=font)
    buf = BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    # 直接调 _classify（绕过抓图通道）
    solver = CaptchaSolver()
    text = await solver._classify(png_bytes)
    assert isinstance(text, str)
    assert len(text) >= 1, f"OCR 至少应返回 1 个字符，得到 {text!r}"


# ─── 其他 ────────────────────────────────────────────────────────────


def test_captcha_result_dataclass_serializable() -> None:
    """CaptchaResult 字段全是 builtin，能直接 dataclasses.asdict / json.dumps。"""
    import dataclasses
    import json
    r = CaptchaResult(success=True, text="ABCD", attempts=2, reason=None)
    d = dataclasses.asdict(r)
    s = json.dumps(d)
    assert "ABCD" in s
