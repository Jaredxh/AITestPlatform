"""Phase 13 / Task 13.1 — 4 个 system__ui_automation__* tool 注册 + schema +
runtime 校验单测。
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.llm import agent_tools
from app.modules.skills.builtin.ui_automation.tools import (
    LIST_ENVIRONMENTS_TOOL_NAME,
    LIST_TEST_DATA_SETS_TOOL_NAME,
    PROPOSE_EXECUTION_PLAN_TOOL_NAME,
    SEARCH_TEST_CASES_TOOL_NAME,
    UI_AUTOMATION_TOOL_NAMES,
    ensure_ui_automation_tools_registered,
    ui_automation_chat_openai_schemas,
)
from app.modules.skills.platform_tools import chat_platform_runtime_cm


def test_all_four_tools_registered() -> None:
    """启动钩子已在 conftest.py 调过；4 个 tool 都应在 ``TOOL_REGISTRY``。"""
    ensure_ui_automation_tools_registered()
    assert SEARCH_TEST_CASES_TOOL_NAME in agent_tools.TOOL_REGISTRY
    assert LIST_ENVIRONMENTS_TOOL_NAME in agent_tools.TOOL_REGISTRY
    assert LIST_TEST_DATA_SETS_TOOL_NAME in agent_tools.TOOL_REGISTRY
    assert PROPOSE_EXECUTION_PLAN_TOOL_NAME in agent_tools.TOOL_REGISTRY


def test_tool_names_use_double_underscore_namespace() -> None:
    """设计文档约定 ``system__<slug>__<tool>`` 命名空间；4 个 tool 必须严格遵守。"""
    for name in UI_AUTOMATION_TOOL_NAMES:
        assert name.startswith("system__ui_automation__"), name


def test_openai_schemas_complete_and_well_formed() -> None:
    schemas = ui_automation_chat_openai_schemas()
    assert set(schemas.keys()) == set(UI_AUTOMATION_TOOL_NAMES)

    for tool_name, spec in schemas.items():
        # OpenAI Chat tool 三件套：type / function.name / function.parameters
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"] == tool_name
        assert "description" in fn
        # description 不超过 1024 字符（DoD §自我检查）
        assert len(fn["description"]) <= 1024, tool_name
        # 必填 parameters，type=object
        params = fn["parameters"]
        assert params["type"] == "object"


def test_propose_execution_plan_schema_requires_case_ids_and_env() -> None:
    """``propose_execution_plan.required`` 必须含 case_ids + environment_id；
    防止 AI 偷偷默认 environment 派发到未知环境。"""
    spec = ui_automation_chat_openai_schemas()[PROPOSE_EXECUTION_PLAN_TOOL_NAME]
    required = set(spec["function"]["parameters"].get("required", []))
    assert "case_ids" in required
    assert "environment_id" in required


@pytest.mark.asyncio
async def test_handlers_require_active_runtime() -> None:
    """4 个 handler 在没有 ``chat_platform_runtime_cm`` 挂载时都应返回 error
    （而非 raise）；上游 LLM 拿到错误能继续推理，不会让会话崩。"""
    from app.modules.skills.builtin.ui_automation.tools.list_environments import (
        exec_list_environments,
    )
    from app.modules.skills.builtin.ui_automation.tools.list_test_data_sets import (
        exec_list_test_data_sets,
    )
    from app.modules.skills.builtin.ui_automation.tools.propose_execution_plan import (
        exec_propose_execution_plan,
    )
    from app.modules.skills.builtin.ui_automation.tools.search_test_cases import (
        exec_search_test_cases,
    )

    for fn, args in [
        (exec_search_test_cases, {}),
        (exec_list_environments, {}),
        (exec_list_test_data_sets, {}),
        (exec_propose_execution_plan, {
            "case_ids": [str(uuid.uuid4())],
            "environment_id": str(uuid.uuid4()),
        }),
    ]:
        result = await fn(args)
        assert "error" in result, fn.__name__


@pytest.mark.asyncio
async def test_propose_execution_plan_validates_uuid_inputs() -> None:
    """非 UUID 字符串入参直接拒，给 LLM 一个明确错误（不会落库脏 plan）。"""
    from app.modules.skills.builtin.ui_automation.tools.propose_execution_plan import (
        exec_propose_execution_plan,
    )

    db = AsyncMock()
    user = MagicMock()
    pid = uuid.uuid4()
    async with chat_platform_runtime_cm(db, user, pid, None, None):
        # 非法 case_id
        r = await exec_propose_execution_plan(
            {"case_ids": ["not-a-uuid"], "environment_id": str(uuid.uuid4())},
        )
        assert "error" in r and "case_id" in r["error"]

        # 缺 environment_id
        r = await exec_propose_execution_plan({"case_ids": [str(uuid.uuid4())]})
        assert "error" in r and "environment_id" in r["error"]


@pytest.mark.asyncio
async def test_search_test_cases_passes_query_through_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_test_cases tool 仅是 case_matcher 的薄壳；query 应原样传下去。

    详细的"#NNN 走 id_exact / 关键词走 title 模糊"行为单测见
    ``tests/skills/test_case_matcher.py``——本 tool test 只验证装配链。
    """
    from app.modules.skills.builtin.ui_automation.matchers import case_matcher
    from app.modules.skills.builtin.ui_automation.matchers.case_matcher import (
        CaseCandidate,
        CaseMatchStrategy,
    )
    from app.modules.skills.builtin.ui_automation.tools import search_test_cases
    from app.modules.testcases.models import Testcase

    captured: dict[str, str] = {}

    async def _fake_match(db, query, project_id, *, limit):  # noqa: ANN001
        captured["query"] = query
        captured["limit"] = limit
        tc = Testcase(
            id=uuid.uuid4(), project_id=project_id, case_no=123,
            title="登录-验证账号密码", priority="high", status="active",
            created_by=uuid.uuid4(),
        )
        return [
            CaseCandidate(
                case=tc, relevance_score=1.0,
                matched_via=[CaseMatchStrategy.ID_EXACT],
            ),
        ]

    monkeypatch.setattr(case_matcher, "match_test_cases", _fake_match)
    monkeypatch.setattr(search_test_cases, "match_test_cases", _fake_match)

    db = AsyncMock()
    user = MagicMock()
    pid = uuid.uuid4()
    async with chat_platform_runtime_cm(db, user, pid, None, None):
        out = await search_test_cases.exec_search_test_cases({"query": "执行 #123"})

    assert captured["query"] == "执行 #123"
    assert out["count"] == 1
    assert out["cases"][0]["case_no"] == 123
    assert out["cases"][0]["matched_via"] == ["id_exact"]
    assert out["cases"][0]["relevance_score"] == 1.0


@pytest.mark.asyncio
async def test_list_environments_sorts_by_risk() -> None:
    """``risk_level=low`` 排前面，AI 默认选低风险环境。"""
    from app.modules.skills.builtin.ui_automation.tools import list_environments

    pid = uuid.uuid4()

    class _Env:
        def __init__(self, name: str, base_url: str) -> None:
            self.id = uuid.uuid4()
            self.name = name
            self.base_url = base_url
            self.project_id = pid
            self.updated_at = None

    rows = [
        _Env("PROD", "https://prod.x.com"),
        _Env("dev", "https://dev.x.com"),
        _Env("staging", "https://staging.x.com"),
    ]
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=exec_result)
    user = MagicMock()

    async with chat_platform_runtime_cm(db, user, pid, None, None):
        out = await list_environments.exec_list_environments({})

    levels = [e["risk_level"] for e in out["environments"]]
    assert levels == ["low", "medium", "high"]


@pytest.mark.asyncio
async def test_propose_execution_plan_returns_valid_card_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``propose_execution_plan`` 成功路径：返回 ``ExecutionPlanCard`` 各必填字段
    序列化为 JSON-friendly dict。"""
    from app.modules.skills.builtin.ui_automation import plan_builder
    from app.modules.skills.builtin.ui_automation.schemas import (
        CaseSummary,
        ConfirmationStrength,
        EnvironmentSummary,
        EnvRiskLevel,
        ExecutionPlanCard,
        LLMProviderSummary,
        TestDataPreview,
    )
    from app.modules.skills.builtin.ui_automation.tools import propose_execution_plan

    pid = uuid.uuid4()
    case_id = uuid.uuid4()
    env_id = uuid.uuid4()
    plan = ExecutionPlanCard(
        plan_id=uuid.uuid4(),
        project_id=pid,
        cases=[
            CaseSummary(id=case_id, case_no=1, title="t", priority="medium",
                        status="active"),
        ],
        environment=EnvironmentSummary(
            id=env_id, name="dev", base_url="https://dev.x.com",
            risk_level=EnvRiskLevel.LOW,
        ),
        llm_provider=LLMProviderSummary(
            id=None, name="X", provider="x", model="m",
        ),
        test_data_preview=TestDataPreview(),
        estimated_duration_seconds=90,
        confirmation_strength=ConfirmationStrength.NONE,
    )
    monkeypatch.setattr(
        plan_builder, "build_execution_plan", AsyncMock(return_value=plan),
    )
    # propose_execution_plan 模块在 import 时把 build_execution_plan 绑定到了
    # 自己的命名空间——必须同时打补丁，否则上面的 monkeypatch 不生效。
    monkeypatch.setattr(
        propose_execution_plan, "build_execution_plan", AsyncMock(return_value=plan),
    )

    db = AsyncMock()
    user = MagicMock()
    async with chat_platform_runtime_cm(db, user, pid, None, None):
        result = await propose_execution_plan.exec_propose_execution_plan({
            "case_ids": [str(case_id)],
            "environment_id": str(env_id),
        })

    assert result["confirmation_strength"] == "none"
    assert result["environment"]["risk_level"] == "low"
    assert result["plan_id"] == str(plan.plan_id)
    # JSON 可序列化（OpenAI tool result 的硬性要求）
    json.dumps(result)


@pytest.mark.asyncio
async def test_propose_execution_plan_persists_skill_card_when_session_id_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 13 / Task 13.3 — runtime 挂了 session_id 时，propose 应额外把 plan
    落成 ``kind='skill_card'`` 的 ChatMessage 并把 message_id 回灌进 plan card。"""
    from app.modules.skills.builtin.ui_automation import plan_builder
    from app.modules.skills.builtin.ui_automation.schemas import (
        CaseSummary,
        ConfirmationStrength,
        EnvironmentSummary,
        EnvRiskLevel,
        ExecutionPlanCard,
        LLMProviderSummary,
        TestDataPreview,
    )
    from app.modules.skills.builtin.ui_automation.tools import propose_execution_plan

    pid = uuid.uuid4()
    case_id = uuid.uuid4()
    env_id = uuid.uuid4()
    sid = uuid.uuid4()
    plan = ExecutionPlanCard(
        plan_id=uuid.uuid4(),
        project_id=pid,
        cases=[CaseSummary(
            id=case_id, case_no=1, title="t", priority="medium", status="active",
        )],
        environment=EnvironmentSummary(
            id=env_id, name="dev", base_url="https://dev.x.com",
            risk_level=EnvRiskLevel.LOW,
        ),
        llm_provider=LLMProviderSummary(
            id=None, name="X", provider="x", model="m",
        ),
        test_data_preview=TestDataPreview(),
        estimated_duration_seconds=60,
        confirmation_strength=ConfirmationStrength.NONE,
    )
    monkeypatch.setattr(
        plan_builder, "build_execution_plan", AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        propose_execution_plan, "build_execution_plan", AsyncMock(return_value=plan),
    )

    # 屏蔽真正的 async_session_factory：用一个空 ctx manager 避免连数据库
    class _DummySessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        propose_execution_plan,
        "async_session_factory",
        lambda: _DummySessionCtx(),
    )

    fake_msg_id = uuid.uuid4()
    publish_calls: list[dict] = []

    async def fake_publish_skill_card(_db, *, session_id, plan_id, plan_payload, **_):
        publish_calls.append({"session_id": session_id, "plan_id": plan_id})
        return MagicMock(id=fake_msg_id)

    import app.modules.llm.system_event_service as ses

    monkeypatch.setattr(ses, "publish_skill_card", fake_publish_skill_card)

    update_calls: list = []

    async def fake_update(plan_id, message_id):
        update_calls.append((plan_id, message_id))

    monkeypatch.setattr(
        propose_execution_plan, "update_cached_plan_skill_card", fake_update,
    )

    db = AsyncMock()
    user = MagicMock()
    async with chat_platform_runtime_cm(db, user, pid, None, None, session_id=sid):
        result = await propose_execution_plan.exec_propose_execution_plan({
            "case_ids": [str(case_id)],
            "environment_id": str(env_id),
        })

    assert publish_calls == [{"session_id": sid, "plan_id": plan.plan_id}]
    assert update_calls == [(plan.plan_id, fake_msg_id)]
    assert result["skill_card_message_id"] == str(fake_msg_id)


@pytest.mark.asyncio
async def test_propose_execution_plan_skips_persist_without_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没绑 session_id 的老调用方（直接调 platform tool 不走 chat 流）不应触发
    skill_card 落库——避免在没有 chat session 的场景写出孤儿消息。"""
    from app.modules.skills.builtin.ui_automation import plan_builder
    from app.modules.skills.builtin.ui_automation.schemas import (
        CaseSummary,
        ConfirmationStrength,
        EnvironmentSummary,
        EnvRiskLevel,
        ExecutionPlanCard,
        LLMProviderSummary,
        TestDataPreview,
    )
    from app.modules.skills.builtin.ui_automation.tools import propose_execution_plan

    pid = uuid.uuid4()
    case_id = uuid.uuid4()
    env_id = uuid.uuid4()
    plan = ExecutionPlanCard(
        plan_id=uuid.uuid4(),
        project_id=pid,
        cases=[CaseSummary(
            id=case_id, case_no=1, title="t", priority="medium", status="active",
        )],
        environment=EnvironmentSummary(
            id=env_id, name="dev", base_url="https://x", risk_level=EnvRiskLevel.LOW,
        ),
        llm_provider=LLMProviderSummary(id=None, name="X", provider="x", model="m"),
        test_data_preview=TestDataPreview(),
        estimated_duration_seconds=60,
        confirmation_strength=ConfirmationStrength.NONE,
    )
    monkeypatch.setattr(
        plan_builder, "build_execution_plan", AsyncMock(return_value=plan),
    )
    monkeypatch.setattr(
        propose_execution_plan, "build_execution_plan", AsyncMock(return_value=plan),
    )

    publish_calls: list = []

    async def fake_publish_skill_card(*args, **kwargs):
        publish_calls.append((args, kwargs))
        return MagicMock(id=uuid.uuid4())

    import app.modules.llm.system_event_service as ses

    monkeypatch.setattr(ses, "publish_skill_card", fake_publish_skill_card)

    db = AsyncMock()
    user = MagicMock()
    async with chat_platform_runtime_cm(db, user, pid, None, None):  # no session_id
        result = await propose_execution_plan.exec_propose_execution_plan({
            "case_ids": [str(case_id)],
            "environment_id": str(env_id),
        })

    # 没绑 session_id 时：不应触发 publish_skill_card；plan dict 里
    # skill_card_message_id 字段允许存在但值必须为 None（schema 默认）。
    assert publish_calls == []
    assert result.get("skill_card_message_id") is None
