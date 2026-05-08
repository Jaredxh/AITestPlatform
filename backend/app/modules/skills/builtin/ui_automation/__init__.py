"""``system_ui_automation`` skill 的代码侧实现（Phase 13）。

M1 阶段：
- task 13.0：NLU IntentClassifier
- task 13.1：4 个查询/装配 tool + ConfirmationCard 协议（schemas.py /
  plan_builder.py / tools/*）
"""

from app.modules.skills.builtin.ui_automation.intent_classifier import (
    UI_AUTOMATION_INTENT_GUARDED,
    IntentResult,
    classify,
)
from app.modules.skills.builtin.ui_automation.tools import (
    UI_AUTOMATION_TOOL_NAMES,
    ensure_ui_automation_tools_registered,
    ui_automation_chat_openai_schemas,
)

__all__ = [
    "IntentResult",
    "UI_AUTOMATION_INTENT_GUARDED",
    "UI_AUTOMATION_TOOL_NAMES",
    "classify",
    "ensure_ui_automation_tools_registered",
    "ui_automation_chat_openai_schemas",
]
