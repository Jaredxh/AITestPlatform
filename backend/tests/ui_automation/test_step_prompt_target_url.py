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


# ─── 二期验收 #第二批：催促语句去除 + 数据 fallback 指引 ───
#
# 验收反馈：AI 报告里的"深度思考"经常出现"用一句话"/"立即"/"每轮不超过 3 次"
# 这类催促性约束，导致：
# 1. 推理模型 reasoning_content 被截断（典型 #f6513ebb 案例）
# 2. AI 不敢做必要的多轮探查
# 3. 用例步骤里写的占位数据失败后不会主动改用物料里的真实值
#
# 这组测试锁定上述修复，避免被人无意改回去。


def test_prompt_no_more_pressuring_phrases() -> None:
    """system prompt 里不能再出现"催促"性短语——这些短语会让推理模型
    截断思考链 / 不敢多探查。"""
    out = build_step_system_prompt(step_description="任意步骤")
    forbidden = [
        "用一句话",
        "用一句简短",
        "一句话总结",
        "立即基于",
        "尽快",
        "每轮 tool_call 不要超过 3",
    ]
    hits = [p for p in forbidden if p in out]
    assert not hits, (
        f"step_runner system prompt 不应再含催促性短语，但检出：{hits}。"
        "如确需限制，请用建议性而非命令性语句。"
    )


def test_prompt_contains_data_fallback_section() -> None:
    """system prompt 必须告诉 AI：用例步骤里硬编码的 ID/账号/名称等占位数据
    操作失败时，要主动调 platform_get_test_data 查物料里的真实值再试。

    没有这段时，AI 看到「未找到」也只会原地用占位反复重试，整条用例失败。"""
    out = build_step_system_prompt(step_description="查询创作者 ID 1234567")
    assert "数据使用与兜底原则" in out, "必须有「数据使用与兜底原则」小节"
    assert "platform_get_test_data" in out, "必须明确告诉 AI 调 platform_get_test_data"
    # 触发条件关键词
    triggers = ["未找到", "不存在", "无权限"]
    hits = [t for t in triggers if t in out]
    assert hits, "必须列出触发 fallback 的页面信号关键词"
    # 必须强调"业务语义匹配"，而不是要求 key 完全一致
    assert "语义" in out, "必须强调按业务语义匹配物料 key（如 creator_id 类）"


def test_user_message_no_more_one_sentence_constraint() -> None:
    """user message 也不能再要求"用一句话告诉我"——这是验收里反映的另一处催促点。"""
    from app.modules.ui_automation.prompts.step_runner_system import (
        build_step_user_message,
    )

    msg = build_step_user_message("点击登录", expected="进入主页")
    assert "用一句话" not in msg
    assert "一句话告诉" not in msg
    # 同时必须保留"完成后请告诉我做了什么"这个语义
    assert "执行完成后" in msg or "做了" in msg
