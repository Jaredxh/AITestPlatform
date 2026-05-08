"""``system_ui_automation`` skill 的智能匹配器（Phase 13 / Task 13.2）。

- ``case_matcher.match_test_cases``：三策略级联——ID/编号 / title+tags 模糊 /
  步骤内容召回；返回带 relevance_score 的候选列表。
- ``env_priority.resolve_environment``：5 层环境优先级解析——用户提到 / 会话
  绑定 / 项目默认 / 用户上次用过 / fallback 低风险，全部缺失返回 missing。

设计文档：``docs/PHASE3_DESIGN.md §10.2 / §10.3``。
"""

from app.modules.skills.builtin.ui_automation.matchers.case_matcher import (
    CaseCandidate,
    CaseMatchStrategy,
    match_test_cases,
)
from app.modules.skills.builtin.ui_automation.matchers.env_priority import (
    EnvironmentResolution,
    EnvPriorityLayer,
    resolve_environment,
)

__all__ = [
    "CaseCandidate",
    "CaseMatchStrategy",
    "EnvPriorityLayer",
    "EnvironmentResolution",
    "match_test_cases",
    "resolve_environment",
]
