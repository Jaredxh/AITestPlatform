"""``system__ui_automation__*`` 工具集（Phase 13 / Task 13.1）。

- ``search_test_cases``：按标题模糊搜（M1）；M2 task 13.2 升级为三策略级联
- ``list_environments``：列项目环境 + 启发式 risk_level
- ``list_test_data_sets``：列项目物料集 + scope / item_count
- ``propose_execution_plan``：装配 ConfirmationCard payload，返回 plan_id

四个 tool 的 OpenAI schema 与执行函数同时通过 ``ensure_ui_automation_tools_
registered()`` 注册到 ``TOOL_REGISTRY``；由 ``platform_tools`` 暴露的 chat
runtime（``ChatPlatformRuntime``）提供 db / user / project_id。
"""

from app.modules.skills.builtin.ui_automation.tools.list_environments import (
    LIST_ENVIRONMENTS_SCHEMA,
    LIST_ENVIRONMENTS_TOOL_NAME,
    exec_list_environments,
)
from app.modules.skills.builtin.ui_automation.tools.list_test_data_sets import (
    LIST_TEST_DATA_SETS_SCHEMA,
    LIST_TEST_DATA_SETS_TOOL_NAME,
    exec_list_test_data_sets,
)
from app.modules.skills.builtin.ui_automation.tools.propose_execution_plan import (
    PROPOSE_EXECUTION_PLAN_SCHEMA,
    PROPOSE_EXECUTION_PLAN_TOOL_NAME,
    exec_propose_execution_plan,
)
from app.modules.skills.builtin.ui_automation.tools.search_test_cases import (
    SEARCH_TEST_CASES_SCHEMA,
    SEARCH_TEST_CASES_TOOL_NAME,
    exec_search_test_cases,
)

#: 设计文档 §10.7：``run_ui_test`` 永远不在此 list 中——LLM 不能直接派发
#: 执行；只能调 ``propose_execution_plan`` 走前端用户 confirm 路径。
UI_AUTOMATION_TOOL_NAMES: tuple[str, ...] = (
    SEARCH_TEST_CASES_TOOL_NAME,
    LIST_ENVIRONMENTS_TOOL_NAME,
    LIST_TEST_DATA_SETS_TOOL_NAME,
    PROPOSE_EXECUTION_PLAN_TOOL_NAME,
)


def ui_automation_chat_openai_schemas() -> dict[str, dict]:
    """4 个 ``system__ui_automation__*`` tool 的 OpenAI Chat tool spec。"""
    return {
        SEARCH_TEST_CASES_TOOL_NAME: SEARCH_TEST_CASES_SCHEMA,
        LIST_ENVIRONMENTS_TOOL_NAME: LIST_ENVIRONMENTS_SCHEMA,
        LIST_TEST_DATA_SETS_TOOL_NAME: LIST_TEST_DATA_SETS_SCHEMA,
        PROPOSE_EXECUTION_PLAN_TOOL_NAME: PROPOSE_EXECUTION_PLAN_SCHEMA,
    }


_REGISTERED = False


def ensure_ui_automation_tools_registered() -> None:
    """进程级一次性注册到 ``TOOL_REGISTRY``；同 ``ensure_platform_tools_registered``
    幂等可重复调用（已注册的会触发 ``register_tool`` warning，但不会重复执行）。
    """
    global _REGISTERED
    if _REGISTERED:
        return
    from app.modules.llm.agent_tools import register_tool

    register_tool(SEARCH_TEST_CASES_TOOL_NAME, exec_search_test_cases)
    register_tool(LIST_ENVIRONMENTS_TOOL_NAME, exec_list_environments)
    register_tool(LIST_TEST_DATA_SETS_TOOL_NAME, exec_list_test_data_sets)
    register_tool(PROPOSE_EXECUTION_PLAN_TOOL_NAME, exec_propose_execution_plan)
    _REGISTERED = True


__all__ = [
    "LIST_ENVIRONMENTS_TOOL_NAME",
    "LIST_TEST_DATA_SETS_TOOL_NAME",
    "PROPOSE_EXECUTION_PLAN_TOOL_NAME",
    "SEARCH_TEST_CASES_TOOL_NAME",
    "UI_AUTOMATION_TOOL_NAMES",
    "ensure_ui_automation_tools_registered",
    "ui_automation_chat_openai_schemas",
]
