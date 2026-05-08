"""Phase 13 / Task 13.1 — ConfirmationCard / ExecutionPlanCard 协议契约。"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.modules.skills.builtin.ui_automation.schemas import (
    CaseSummary,
    ConfirmationStrength,
    EnvironmentSummary,
    EnvRiskLevel,
    ExecutionPlanCard,
    LLMProviderSummary,
    TestDataPreview,
)


def test_confirmation_strength_values() -> None:
    """前端按字符串值 switch 渲染卡片，禁止动这 3 个值。"""
    assert ConfirmationStrength.NONE.value == "none"
    assert ConfirmationStrength.SOFT.value == "soft"
    assert ConfirmationStrength.STRICT.value == "strict"


def test_env_risk_level_values() -> None:
    assert EnvRiskLevel.LOW.value == "low"
    assert EnvRiskLevel.MEDIUM.value == "medium"
    assert EnvRiskLevel.HIGH.value == "high"


def test_execution_plan_card_required_fields() -> None:
    """``ExecutionPlanCard`` 缺任意必填字段都应 ValidationError；前端 TS 类型生成
    依赖此契约。"""
    payload = {
        "plan_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "cases": [
            CaseSummary(
                id=uuid.uuid4(), case_no=1, title="登录-验证账号密码",
                priority="high", status="active",
            ),
        ],
        "environment": EnvironmentSummary(
            id=uuid.uuid4(), name="dev", base_url="https://dev.foo.com",
            risk_level=EnvRiskLevel.LOW,
        ),
        "llm_provider": LLMProviderSummary(
            id=uuid.uuid4(), name="DS", provider="deepseek", model="deepseek-chat",
        ),
        "test_data_preview": TestDataPreview(),
        "estimated_duration_seconds": 90,
        "confirmation_strength": ConfirmationStrength.SOFT,
    }
    plan = ExecutionPlanCard(**payload)

    assert plan.confirmation_strength is ConfirmationStrength.SOFT
    assert plan.runtime_data_flow is None
    assert plan.confirmation_payload == {}

    # 缺 environment → ValidationError
    bad = dict(payload)
    bad.pop("environment")
    with pytest.raises(ValidationError):
        ExecutionPlanCard(**bad)


def test_execution_plan_card_serializes_to_json_friendly_dict() -> None:
    """``model_dump(mode='json')`` 必须把 UUID / Enum 序列化为字符串，给 LLM
    的 tool result（JSON）才能直接吞下。"""
    plan = ExecutionPlanCard(
        plan_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        cases=[
            CaseSummary(
                id=uuid.uuid4(), case_no=42, title="t", priority="medium",
                status="active",
            ),
        ],
        environment=EnvironmentSummary(
            id=uuid.uuid4(), name="prod", base_url="https://prod.x.com",
            risk_level=EnvRiskLevel.HIGH, risk_reason="contains 'prod'",
        ),
        llm_provider=LLMProviderSummary(
            id=None, name="X", provider="qwen", model="qwen-long",
        ),
        test_data_preview=TestDataPreview(),
        estimated_duration_seconds=120,
        confirmation_strength=ConfirmationStrength.STRICT,
        confirmation_payload={"challenge": "YES PROD"},
    )

    dumped = plan.model_dump(mode="json")
    assert isinstance(dumped["plan_id"], str)
    assert isinstance(dumped["project_id"], str)
    assert dumped["confirmation_strength"] == "strict"
    assert dumped["environment"]["risk_level"] == "high"
    assert dumped["confirmation_payload"]["challenge"] == "YES PROD"
