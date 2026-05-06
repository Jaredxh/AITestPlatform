"""Task 9.5 — AssertionJudge 单测。"""

from __future__ import annotations

import pytest

from app.modules.ui_automation.assertion_judge import (
    AssertionJudge,
    AssertionLLMConfig,
)


@pytest.mark.asyncio
async def test_no_expected_passes() -> None:
    judge = AssertionJudge()
    v = await judge.judge(expected=None, snapshot="anything")
    assert v.passed is True
    assert v.method == "no_expected"

    v2 = await judge.judge(expected="   ", snapshot="anything")
    assert v2.passed is True
    assert v2.method == "no_expected"


@pytest.mark.asyncio
async def test_no_snapshot_fails() -> None:
    judge = AssertionJudge()
    v = await judge.judge(expected="登录成功", snapshot=None)
    assert v.passed is False
    assert v.method == "skipped"

    v2 = await judge.judge(expected="登录成功", snapshot="")
    assert v2.passed is False
    assert v2.method == "skipped"


@pytest.mark.asyncio
async def test_full_text_match_passes() -> None:
    judge = AssertionJudge()
    v = await judge.judge(
        expected="跳转到首页",
        snapshot="- main\n  - heading 'Welcome'\n  - text '跳转到首页 已完成'",
    )
    assert v.passed is True
    assert v.method == "text_search"
    assert "跳转到首页" in v.reason


@pytest.mark.asyncio
async def test_multi_keyword_match_passes() -> None:
    judge = AssertionJudge()
    v = await judge.judge(
        expected="登录成功，欢迎回来",
        snapshot="- main\n  - heading '登录成功！'\n  - text '欢迎回来 admin'",
    )
    assert v.passed is True
    assert v.method == "text_search"


@pytest.mark.asyncio
async def test_text_miss_without_llm_fails_text_search() -> None:
    judge = AssertionJudge()
    v = await judge.judge(
        expected="找不到的文本",
        snapshot="- main\n  - link '其他东西'",
    )
    assert v.passed is False
    assert v.method == "text_search"


@pytest.mark.asyncio
async def test_llm_fallback_when_text_miss() -> None:
    captured = {}

    async def fake_complete(*, provider, model, messages, api_key, base_url, temperature, max_tokens):  # noqa: ANN001
        captured["called"] = True
        captured["provider"] = provider
        captured["model"] = model
        return '{"passed": true, "reason": "页面内容语义匹配", "evidence": "Welcome user"}'

    judge = AssertionJudge(completion_fn=fake_complete)
    v = await judge.judge(
        expected="用户登录成功并欢迎",
        snapshot="- main\n  - heading 'Welcome user'",
        step_description="点击登录",
        llm_config=AssertionLLMConfig(provider="openai", model="gpt-4o-mini"),
    )
    assert captured["called"] is True
    assert v.passed is True
    assert v.method == "llm"
    assert v.evidence == "Welcome user"


@pytest.mark.asyncio
async def test_llm_returns_invalid_json_marks_unavailable() -> None:
    async def fake_complete(**_):
        return "随便输出一段不是 JSON 的话"

    judge = AssertionJudge(completion_fn=fake_complete)
    v = await judge.judge(
        expected="特定内容",
        snapshot="不相关的 snapshot",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is False
    assert v.method == "llm_unavailable"
    # 非空内容应原样回显（前 200 字符），方便调试
    assert "随便输出" in v.reason


@pytest.mark.asyncio
async def test_llm_returns_empty_content_gives_explicit_reason() -> None:
    """**关键回归（修复 #f6513ebb）**：thinking 模式下 ``content=""`` 是常见
    症状（reasoning_content 把 max_tokens 用光），早期实现把空串拼到 reason
    里给用户看到的就是 ``"LLM 输出无法解析为 JSON："`` 后面光秃秃 —— 完全没
    诊断价值。修复后应给出明确的"返回空内容 / 检查 max_tokens"提示。"""
    async def empty_complete(**_):
        return ""

    judge = AssertionJudge(completion_fn=empty_complete)
    # expected 在 snapshot 里**不**直接命中，强制走到 LLM 兜底分支
    v = await judge.judge(
        expected="模型必须深度判断的内容",
        snapshot="- main\n  - text 'unrelated'",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is False
    assert v.method == "llm_unavailable"
    # reason 必须明确指出空 content 的常见根因 + 解决方向
    assert "空内容" in v.reason
    assert "max_tokens" in v.reason
    # 不能再是早期那种"LLM 输出无法解析为 JSON：" + 空串的截断式错误
    assert not v.reason.endswith("无法解析为 JSON：")


@pytest.mark.asyncio
async def test_llm_returns_whitespace_only_content_treated_as_empty() -> None:
    """全是空白字符（thinking 模式偶尔会返回 "  \\n  "）也按"空 content"处理。"""
    async def whitespace_complete(**_):
        return "   \n  \t  "

    judge = AssertionJudge(completion_fn=whitespace_complete)
    v = await judge.judge(
        expected="模型必须深度判断的内容",
        snapshot="- main\n  - text 'unrelated'",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is False
    assert "空内容" in v.reason


def test_assertion_llm_config_default_max_tokens_is_thinking_friendly() -> None:
    """``max_tokens`` 默认值应足以覆盖 thinking 模式（GLM / doubao thinking-pro
    等）的内部思考 + final JSON 输出 —— 512 太小会导致空 content 故障。"""
    cfg = AssertionLLMConfig(provider="openai", model="x")
    assert cfg.max_tokens >= 2048, (
        f"AssertionLLMConfig.max_tokens={cfg.max_tokens} 太小，thinking 模式下"
        "容易 reasoning 用满 → final content 被截空，详见 #f6513ebb"
    )


@pytest.mark.asyncio
async def test_llm_call_exception_falls_back_to_unavailable() -> None:
    async def boom(**_):
        raise RuntimeError("network down")

    judge = AssertionJudge(completion_fn=boom)
    v = await judge.judge(
        expected="特定内容",
        snapshot="不相关的 snapshot",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is False
    assert v.method == "llm_unavailable"
    assert "RuntimeError" in v.reason


@pytest.mark.asyncio
async def test_llm_extracts_json_from_markdown_fence() -> None:
    async def fake_complete(**_):
        return '```json\n{"passed": false, "reason": "未找到", "evidence": ""}\n```'

    judge = AssertionJudge(completion_fn=fake_complete)
    v = await judge.judge(
        expected="找不到的文本",
        snapshot="- main",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is False
    assert v.method == "llm"
    assert v.reason == "未找到"


@pytest.mark.asyncio
async def test_llm_extracts_object_buried_in_text() -> None:
    async def fake_complete(**_):
        return '一些前导的废话\n{"passed": true, "reason": "OK"}\n后续无关'

    judge = AssertionJudge(completion_fn=fake_complete)
    v = await judge.judge(
        expected="某种内容",
        snapshot="- main\n  - other",
        llm_config=AssertionLLMConfig(provider="openai", model="x"),
    )
    assert v.passed is True
    assert v.method == "llm"
