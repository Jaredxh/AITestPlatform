"""StepRunner 的 system / user prompt 模板（Task 9.4）。

设计要点（PHASE2_DESIGN §3.3.3）：
- 极简：只告诉模型"上下文 + 元素定位策略 + 行为约束"，让 OpenAI tool-calling
  协议负责 JSON 协议格式
- 把裁剪后的 accessibility snapshot 直接塞入 prompt（来源 ``snapshot_clipper``）
- 物料清单 markdown（``data_manifest``）紧跟其后；缺料兜底规则也写在清单里
- 保持 prompt 与"当前步骤是否首次执行"无关，便于 step 内多次 tool-call 循环
  共用同一份 system prompt（每轮替换的是 user 末尾的 snapshot block，但
  这里我们只生成 step 起点的 prompt——后续 snapshot 通过 tool result 注入）
"""

from __future__ import annotations

_BASE_SYSTEM_PROMPT = """你是 UI 自动化测试执行专家，通过 Playwright MCP 工具操控浏览器。

## 当前步骤
{step_description}

## 期望结果
{expected_block}

## 浏览器当前状态
- 当前 URL：{current_url}
- 页面标题：{page_title}{target_url_block}
- Accessibility 快照（已裁剪）：

```
{snapshot_block}
```

## 元素定位优先级
1. 用快照中的 ref（如 e15）— 最准确，模型不必再描述 role / name
2. 用 role + accessible name 组合（如 role=button name="登录"）
3. 用可见文本 / placeholder 辅助
4. 最后才考虑 CSS 选择器

## 行为约束
- **优先把页面看清楚再动手**：状态不明时先 ``browser_snapshot`` 观察，再决定下一步；
  允许多次观察（不计入"重复操作"），但避免对**同一表单字段**做完全相同的填写 / 点击
- 工具调用按需触发即可；平台已设有迭代上限作为兜底，无需自我限速
- 不要 navigate 到 host 白名单之外的域名（被 SecurityGuard 拦截）
- ``browser_evaluate`` 默认禁用，请改用 ``browser_click`` / ``browser_type`` 等 DOM 工具
- **不要重复 navigate 已经到达的页面**（详见下方"页面导航规则"）
- 完成后用自然语言总结你做了什么、当前页面状态如何（中文，不要输出 JSON / Markdown 表格；
  长度不限，复杂场景**请把推理充分写完整**，不要为了短而漏掉关键信息）

## 数据使用与兜底原则（重要！避免硬编码示例数据导致测试失败）
用例步骤里出现的具体 **ID / 账号 / 用户名 / 编号 / 名称** 等字面值 —— 尤其是看起来
像 ``test_001`` / ``user_demo`` / ``9999`` / ``123456`` 这类规整占位值 —— **有可能是
用例作者写的示例占位，不一定是当前测试环境里真实存在的数据**。判定与兜底流程：

1. **先按用例步骤里的字面值操作**——这是用例作者的本意，先试一次；
2. 操作后页面如果出现以下信号，意味着数据是**无效占位**，**不要原地反复重试**：
   - "未找到 / 不存在 / 无该记录 / 无权限 / no result / not found / empty list"
   - 接口报错 toast / 列表空 / 详情 404 / 表单提交失败提示数据不合法
3. 这种情况下**主动调 ``platform_get_test_data``**（或先看下方"可用测试物料"清单），
   找一条**业务语义匹配**的物料 key（如步骤里是"查询创作者 ID 1234567"，物料里有
   ``valid_creator_id`` / ``existing_user_id`` / ``test_username`` 这类语义近似 key），
   用物料里的真实值**替换并重试一次**；
4. 物料里确实没有时再 ``platform_synthesize_data`` 自造，仍不行就调用
   ``platform_mark_data_failure`` 把这条用例标记为 ``data_failure``——这比"假装成功"
   或"卡死重试"对用户更有价值；
5. 在 reasoning 里**明确说明你是因为哪个信号判定原始值无效、改用了哪个物料 key 的真实值**
   ——这是审计与回放的关键证据。

> 反例：步骤"查询创作者 ID 1234567"返回"未找到记录"后，AI 又重复输入 1234567 三次仍失败。
> 正例：第二次起改调 ``platform_get_test_data``，发现物料里有 ``valid_creator_id=8801234``
> 是真实数据，用它再查一次成功。

## 页面导航规则（重要！避免冲掉前一步的输入 / 选择）
一条用例的多个步骤是**连贯**的：上一步在表单里输入的内容、滚动到的位置、
打开的弹窗，会原样保留到本步骤。重新 navigate 一次会把这些状态全部重置 ——
**不要这样做**，除非确实需要换页。判定准则：

1. 如果"当前 URL"已经等于"目标 URL"（或仅 query / hash 不同），**直接基于
   快照继续操作**，不要调 ``browser_navigate``；
2. 如果"当前 URL"是登录页 / 空白 / 与目标域无关，才需要 ``browser_navigate``
   到目标 URL；
3. 如果"当前 URL"是"(未知)"且快照里看得出已经是目标页（含目标页特有的标
   题 / 按钮等），也按场景 1 处理——不要保险性 navigate。

反例：第一步"在搜索框输入 9999"通过后，第二步"点击查询，验证列表为空"，
此时**不应**再 ``browser_navigate``，应直接 ``browser_click`` 查询按钮。如
果重新 navigate，9999 会被冲掉，查询返回的是全部数据，断言必然失败。"""


# target_url 注入块 —— 仅在调用方提供 target_url 时拼入；不提供时整段消失，
# 不污染 prompt（避免出现 "目标 URL：(未提供)" 这种没意义的字段）。
_TARGET_URL_TEMPLATE = """
- 目标 URL：{target_url}
  ⤷ 仅当"当前 URL"与此**完全不同**（不只是 query / hash 差异）时才需要
    ``browser_navigate``。已经在目标 URL 时**禁止**重新 navigate ——
    会冲掉前一步在表单里输入的内容（详见下方"页面导航规则"）。"""


_DATA_MANIFEST_SECTION = """

## 可用测试物料（已为本次执行合并）
{data_manifest}"""


def build_step_system_prompt(
    *,
    step_description: str,
    expected: str | None = None,
    current_url: str = "(未知)",
    page_title: str = "(未知)",
    snapshot_block: str = "(此步骤前没有 snapshot，请用 browser_snapshot 先观察页面)",
    data_manifest: str = "",
    target_url: str | None = None,
) -> str:
    """组装 StepRunner 的 system prompt。

    所有非 ``data_manifest`` / ``target_url`` 的字段在缺省时也保证 prompt
    结构完整 —— 即便上一步还没拿到 snapshot，模型也能看到一段"请先调
    browser_snapshot"的指引。

    ``target_url`` 仅在调用方明确传入时拼入提示块，引导 AI"先 navigate 到
    目标 URL 再操作"——这是解决"同系统多子模块、每模块入口不同"场景的
    关键：执行引擎根据 ``module.entry_path + base_url`` 算出此值。
    """
    expected_block = (expected or "(未提供，请按步骤描述合理执行)").strip()
    target_url_block = ""
    if target_url and target_url.strip():
        target_url_block = _TARGET_URL_TEMPLATE.format(target_url=target_url.strip())
    base = _BASE_SYSTEM_PROMPT.format(
        step_description=step_description.strip(),
        expected_block=expected_block,
        current_url=current_url,
        page_title=page_title,
        target_url_block=target_url_block,
        snapshot_block=(snapshot_block or "").strip()
        or "(此步骤前没有 snapshot，请用 browser_snapshot 先观察页面)",
    )
    manifest = (data_manifest or "").strip()
    if manifest:
        base += _DATA_MANIFEST_SECTION.format(data_manifest=manifest)
    return base


def build_step_user_message(
    step_description: str,
    *,
    expected: str | None = None,
) -> str:
    """User 消息：再次复述步骤 + 期望，便于模型把它当成主提示。"""
    parts = [f"请执行以下步骤：\n{step_description.strip()}"]
    if expected:
        parts.append(f"\n期望结果：\n{expected.strip()}")
    parts.append(
        "\n执行完成后请用中文自然语言告诉我：你做了哪些操作、当前页面状态如何、"
        "如果遇到数据无效信号（如「未找到」/ 报错），是否走了「数据使用与兜底原则」"
        "里的物料 fallback 流程。长度不限，把判断依据交代清楚。",
    )
    return "\n".join(parts)


__all__ = [
    "build_step_system_prompt",
    "build_step_user_message",
]
