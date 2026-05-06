"""platform_* 物料工具注册与 reasoning 脱敏。"""

from __future__ import annotations

import json
import uuid

import pytest

from app.core import crypto
from app.modules.llm.agent_tools import register_tool, run_tool, unregister_tool
from app.modules.ui_automation.data_platform_tools import (
    redact_tool_result_for_reasoning,
    register_data_tools,
    unregister_data_tools,
)
from app.modules.ui_automation.data_synthesizer import DataSynthesizer
from app.modules.ui_automation.test_data_resolver import TestDataItem, TestDataResolver


@pytest.fixture
def exec_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_platform_get_secret_flags_and_redact(exec_id: uuid.UUID) -> None:
    secret_item = TestDataItem(
        key="pwd",
        value_type="secret",
        value_encrypted=crypto.encrypt("sekrit"),
    )
    resolver = TestDataResolver.from_merge_dict({"pwd": secret_item})
    register_data_tools(exec_id, resolver)
    try:
        raw = await run_tool(f"{exec_id}__platform_get_secret", json.dumps({"key": "pwd"}))
        payload = json.loads(raw)
        assert payload["value"] == "sekrit"
        assert payload["_test_data_secret_used"] is True

        safe = redact_tool_result_for_reasoning(f"{exec_id}__platform_get_secret", payload)
        assert "value" not in safe
        assert safe.get("_test_data_secret_used") is True
    finally:
        unregister_data_tools(exec_id)


@pytest.mark.asyncio
async def test_platform_mark_data_failure_finalize(exec_id: uuid.UUID) -> None:
    resolver = TestDataResolver.from_merge_dict({"a": TestDataItem.adhoc("a", "1")})
    resolver.reset_case_state()
    register_data_tools(exec_id, resolver)
    try:
        raw = await run_tool(
            f"{exec_id}__platform_mark_data_failure",
            json.dumps({"key": "email", "reason": "bounce"}),
        )
        assert json.loads(raw)["case_will_be_marked"] == "data_failure"
        assert resolver.finalize_case()["data_confidence"] == "data_failure"
    finally:
        unregister_data_tools(exec_id)


@pytest.mark.asyncio
async def test_platform_synthesize_reuses_infer_fn(exec_id: uuid.UUID) -> None:
    async def infer_stub(key: str, hint: str, vt: str) -> str:
        return f"AUTO-{key}"

    synth = DataSynthesizer(infer_fn=infer_stub)
    resolver = TestDataResolver.from_merge_dict({})
    resolver.reset_case_state()
    register_data_tools(exec_id, resolver, synthesizer=synth)
    try:
        raw = await run_tool(
            f"{exec_id}__platform_synthesize_data",
            json.dumps({"key": "weird_xxx", "hint": "coupon field", "value_type": "string"}),
        )
        body = json.loads(raw)
        assert body["value"] == "AUTO-weird_xxx"
        assert body["source"] == "ai_inferred"
        assert resolver.finalize_case()["data_confidence"] == "synthesized"
    finally:
        unregister_data_tools(exec_id)


@pytest.mark.asyncio
async def test_unregister_only_platform_tools(exec_id: uuid.UUID) -> None:
    async def dummy_browser(args: dict):  # noqa: ARG001
        return {"browser": True}

    from app.modules.llm import agent_tools as at

    register_tool(f"{exec_id}__browser_dummy", dummy_browser)
    resolver = TestDataResolver.from_merge_dict({})
    register_data_tools(exec_id, resolver)
    unregister_data_tools(exec_id)

    assert f"{exec_id}__browser_dummy" in at.TOOL_REGISTRY
    unregister_tool(f"{exec_id}__browser_dummy")
