---
name: system_ui_automation
display_name: 内置 · UI 自动化（Agent 化）
version: 2.0.0
description: |
  对话内驱动 UI 自动化测试：搜索用例 → 选环境 → 预览物料 → 生成 ConfirmationCard
  → 用户在前端确认后由专门 API 派发执行。LLM 不能直接触发执行（run_ui_test 不
  在工具集中）；这是杜绝模型越过用户偷跑生产环境的最后一道安全闸门。
trigger_keywords:
  - 跑 UI 测试
  - 跑用例
  - 自动化测试
  - 执行 UI 用例
  - 跑下登录
  - 帮我跑
activation_mode: agent_callable
category: system
tools:
  - system__ui_automation__search_test_cases
  - system__ui_automation__list_environments
  - system__ui_automation__list_test_data_sets
  - system__ui_automation__propose_execution_plan
---

# 内置 · UI 自动化（Agent 化）

## 何时使用

用户提到"跑 UI 测试 / 跑用例 / 帮我跑 xxx 流程 / 执行 #编号"等明确执行意图时
使用。**询问历史 / 通过率 / 怎么写用例**等学习/查询意图不要使用本技能（NLU
IntentClassifier 会在两段式校验时把本技能从候选池剔除）。

## 安全约束

- **永远不要**尝试调用 `platform_run_ui_execution` / `run_ui_test` / 任何"直接派发
  执行"的工具；这些工具不在你的工具集中。
- 真正派发执行**必须**通过 `propose_execution_plan` 生成 ConfirmationCard，由用户
  在前端点"确认执行"后走专门 API 触发。
- 高风险环境（`risk_level=high`）必须经用户输入挑战短语（`YES PROD`）二次确认。

## 执行顺序建议（标准 Happy Path）

1. **`system__ui_automation__search_test_cases`**：根据用户描述找到候选用例。
   - 1 条命中 → 直接进入下一步
   - N 条命中 → 把列表呈现给用户让其选
   - 0 条命中 → M1 暂时回复"未找到匹配用例"；M2 task 13.6 接通 adhoc 路径
2. **`system__ui_automation__list_environments`**：列出可选环境。
   - 用户已指定（"用 staging 跑"）→ 直接选用户提的
   - 未指定 + 仅 1 个 low 风险环境 → 默认选它
   - 未指定 + 多个环境 → 反问用户选哪个
   - 涉及 high 风险环境 → 一定要在 ConfirmationCard 里走 strict 强度
3. **（可选）`system__ui_automation__list_test_data_sets`**：用户提"用 alice 跑"
   或物料默认值不确定时调；M1 仅按 scope / is_default 列摘要，M2 接 semantic 后
   会按用例 `required_test_data` 精准匹配。
4. **`system__ui_automation__propose_execution_plan`**：用前 3 步选定的
   `case_ids` + `environment_id` 装配 ConfirmationCard。返回 `plan_id` + 完整
   payload；前端会直接渲染卡片让用户确认。
   - 不要省略 `environment_id`（不允许 AI 默认这个参数）
   - 用户可以在卡片上修改字段重新提交，AI 不应主动二次调用本 tool 重复刷卡片

## 反例

- ❌ 用户问"昨天跑用例的失败率多少" → 这是 query_history 意图，不要触发本技能。
- ❌ 用户问"怎么写好登录用例" → 这是 learn 意图，不要触发本技能。
- ❌ 直接调 `platform_run_ui_execution` 跳过 ConfirmationCard ——该 tool 已不在
  你的工具集中；尝试调用会被服务端拒绝。
