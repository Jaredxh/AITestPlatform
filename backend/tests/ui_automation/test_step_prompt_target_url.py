"""``build_step_system_prompt`` 的 ``target_url`` 注入行为测试。

本组用例关心：
- 不传 ``target_url`` 时 prompt 中**完全没有** "目标 URL：" 这一段
- 传了 ``target_url`` 时块被插入，且 AI 看到决策规则
- 空字符串 / 纯空白 ``target_url`` 等价于不传
- "页面导航规则"小节不论是否传 target_url 都存在（避免 step 间冲掉输入）
"""
from __future__ import annotations

from app.modules.ui_automation.prompts.step_runner_system import (
    build_step_system_prompt,
)


def test_prompt_without_target_url_has_no_section() -> None:
    out = build_step_system_prompt(
        step_description="点击登录按钮",
        expected="跳转到首页",
        current_url="https://example.com",
        page_title="登录",
    )
    # 即使没有 target_url，也不应出现 "目标 URL：…" 这一**字段**
    assert "目标 URL：" not in out


def test_prompt_with_target_url_inserts_section() -> None:
    out = build_step_system_prompt(
        step_description="点击登录按钮",
        target_url="https://app.example.com/admin/users",
    )
    assert "目标 URL：https://app.example.com/admin/users" in out
    # 新版 prompt：决策导向（"仅当...才需要 navigate"），不再无条件让 AI navigate
    assert "browser_navigate" in out
    assert "完全不同" in out  # 当前 vs 目标 URL 的判定关键词


def test_prompt_with_blank_target_url_treated_as_missing() -> None:
    out_blank = build_step_system_prompt(
        step_description="X",
        target_url="   ",
    )
    out_empty = build_step_system_prompt(
        step_description="X",
        target_url="",
    )
    out_none = build_step_system_prompt(step_description="X", target_url=None)
    # 三种"无意义值"都不应出现 "目标 URL：" 这一字段
    assert "目标 URL：" not in out_blank
    assert "目标 URL：" not in out_empty
    assert "目标 URL：" not in out_none


def test_prompt_target_url_trims_whitespace() -> None:
    """两端空白会被 trim，不会出现 ``目标 URL：  http://...  ``。"""
    out = build_step_system_prompt(
        step_description="X",
        target_url="   https://app.example.com/x   ",
    )
    assert "目标 URL：https://app.example.com/x" in out


# ─── 修复 #3c95cf69：步骤间不连贯 / AI 重复 navigate 冲掉输入 ────


def test_prompt_always_includes_navigation_rules_section() -> None:
    """**关键回归**：prompt 里必须始终包含一段"页面导航规则"，明确告诉 AI
    已经在目标 URL 时不要重新 navigate（否则会冲掉前一步的表单输入）。

    实际故障 #3c95cf69：step 1 输入 9999 通过，step 2 第一动作就是
    ``browser_navigate(/author-list)`` 重置了表单，导致点击查询返回的是全部
    数据而不是空列表。"""
    out_with_target = build_step_system_prompt(
        step_description="点击查询",
        target_url="https://app.example.com/list",
    )
    out_without_target = build_step_system_prompt(step_description="点击查询")

    for prompt_text in (out_with_target, out_without_target):
        assert "页面导航规则" in prompt_text, (
            "prompt 必须有'页面导航规则'小节，否则 AI 会在每步开头保险性 navigate"
        )
        # 关键约束词必须存在（按当前 URL == 目标 URL 时不重 navigate）
        assert "不要重复 navigate" in prompt_text or "不要重新 navigate" in prompt_text


def test_prompt_navigation_rules_warn_about_form_reset() -> None:
    """规则里必须明确提示"重新 navigate 会重置前序步骤的输入"，不仅是规
    则口号 —— 给 AI 一个明确的"为什么不能 navigate"理由，效果远好于命令式
    "不要 navigate"。"""
    out = build_step_system_prompt(
        step_description="点击查询",
        target_url="https://app.example.com/list",
    )
    # 规则里要解释清楚后果
    assert "冲掉" in out or "重置" in out
    assert "输入" in out  # 强调表单输入会丢


def test_prompt_with_unknown_current_url_still_carries_rules() -> None:
    """current_url='(未知)' 是 step 1 的默认状态——规则也必须告诉 AI 这种
    情况下如果快照看出已经是目标页，**不要**保险性 navigate。"""
    out = build_step_system_prompt(
        step_description="点击查询",
        current_url="(未知)",
        target_url="https://app.example.com/list",
        snapshot_block="- main\n  - heading 'List'\n  - button '查询'",
    )
    # prompt 里要兜住 (未知) URL 的场景
    assert "(未知)" in out or "未知" in out
