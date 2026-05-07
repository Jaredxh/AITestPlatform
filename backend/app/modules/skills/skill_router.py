"""SkillRouter — chat 消息侧三层激活与 lazy skill_invoke（Phase 12 / Task 12.2）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.models import ChatSession
from app.modules.skills.http_tools import (
    extract_allowed_hosts_from_body,
    http_tool_schemas,
)
from app.modules.skills.models import Skill, SkillUsageLog
from app.modules.skills.platform_tools import platform_chat_openai_schemas
from app.modules.skills.safety import extract_when_to_use, wrap_with_safety
from app.modules.skills.service import (
    list_agent_callable_skills,
)
from app.modules.skills.service import (
    list_always_skills as _fetch_always_skills,
)
from app.modules.skills.triggers import match_triggers


def _invoke_tool_name(slug: str) -> str:
    """OpenAI function.name：仅字母数字下划线短横线；其余替换为 ``_``。"""
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (slug or "").strip())
    return f"skill_{safe}__invoke"


_PLATFORM_SCHEMA_CACHE: dict[str, dict[str, Any]] | None = None


def _platform_specs() -> dict[str, dict[str, Any]]:
    global _PLATFORM_SCHEMA_CACHE
    if _PLATFORM_SCHEMA_CACHE is None:
        _PLATFORM_SCHEMA_CACHE = platform_chat_openai_schemas()
    return _PLATFORM_SCHEMA_CACHE


def _parse_manual_skill_ids(session: ChatSession) -> list[uuid.UUID]:
    raw = getattr(session, "chat_context", None) or {}
    if not isinstance(raw, dict):
        return []
    ids = raw.get("manual_skill_ids") or []
    if not isinstance(ids, list):
        return []
    out: list[uuid.UUID] = []
    for x in ids:
        try:
            out.append(uuid.UUID(str(x)))
        except (ValueError, TypeError):
            continue
    return out


def _dedupe_skills(skills: list[Skill], max_total: int) -> list[Skill]:
    seen: set[uuid.UUID] = set()
    out: list[Skill] = []
    for s in skills:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append(s)
        if len(out) >= max_total:
            break
    return out


def _collect_platform_names(skill: Skill) -> set[str]:
    names: set[str] = set()
    for t in skill.tools_required or []:
        if isinstance(t, str) and t.startswith("platform_"):
            names.add(t)
    return names


def _append_platform_candidate_tools(
    skill: Skill,
    cand_tools: list[dict[str, Any]],
    cand_tool_names: set[str],
) -> None:
    """第一道闸：仅 ``system_*`` slug 暴露 platform_* OpenAI specs。"""
    if not skill.slug.startswith("system_"):
        return
    specs = _platform_specs()
    for tname in skill.tools_required or []:
        if not isinstance(tname, str) or not tname.startswith("platform_"):
            continue
        spec = specs.get(tname)
        if spec is None:
            continue
        fname = spec["function"]["name"]
        if fname in cand_tool_names:
            continue
        cand_tools.append(spec)
        cand_tool_names.add(fname)


def _build_skill_invoke_tool(skill: Skill) -> dict[str, Any]:
    """skill → OpenAI function spec（lazy load：不含正文）。"""
    when_to_use = extract_when_to_use(skill.body)
    return {
        "type": "function",
        "function": {
            "name": _invoke_tool_name(skill.slug),
            "description": (
                f"{skill.description}\n\n"
                f"何时使用：{when_to_use}\n"
                "调用此工具会加载完整技能指令并按其执行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "context": {
                        "type": "string",
                        "description": (
                            "调用此技能时希望补充的上下文（如指定文档/用例/环境名）"
                        ),
                    },
                },
            },
        },
    }


async def execute_skill_invoke(
    db: AsyncSession,
    skill_id: uuid.UUID,
    args_json: str,
    *,
    session_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    assistant_message_id: uuid.UUID | None = None,
) -> str:
    """模型调用 ``skill_*__invoke`` 时加载 SKILL.md 全文（lazy load）。

    若 ``assistant_message_id`` 已知（chat 后台任务模式下都会传），把 SkillUsageLog.id
    回写到该 ChatMessage.skill_invocation_id，前端 ``SkillUsageBadge`` 即可在该消息
    上显示"AI 用了哪个 skill"。
    """
    from app.modules.llm.models import ChatMessage  # 避免循环依赖

    stmt = select(Skill).where(Skill.id == skill_id, Skill.is_enabled.is_(True))
    if project_id is not None:
        stmt = stmt.where(
            or_(Skill.project_id == project_id, Skill.project_id.is_(None)),
        )
    result = await db.execute(stmt)
    skill = result.scalar_one_or_none()
    if skill is None:
        return json.dumps({"error": "skill not found or disabled"}, ensure_ascii=False)

    ctx_note = ""
    try:
        args = json.loads(args_json) if args_json else {}
        if isinstance(args, dict):
            raw_ctx = args.get("context")
            if isinstance(raw_ctx, str):
                ctx_note = raw_ctx.strip()
    except json.JSONDecodeError:
        pass

    wrapped = wrap_with_safety(skill.body)
    if ctx_note:
        wrapped += f"\n\n【本轮附加上下文】\n{ctx_note}"

    log = SkillUsageLog(
        skill_id=skill.id,
        skill_db_version=skill.db_version,
        session_id=session_id,
        message_id=assistant_message_id,
        activation_reason="agent_callable",
        outcome="success",
    )
    db.add(log)
    await db.flush()

    # 回写 ChatMessage.skill_invocation_id —— 一条 assistant message 只记最后一次
    # 调用的 skill（多 skill 调用场景实际很少，且前端徽章 UI 也只显示一个）。
    # 同时把 skill_id / skill_name / activation_reason 塞进 meta_data：
    # 这是前端 SkillUsageBadge 拉详情和着色的依据；不放在独立列里是因为这层
    # 信息纯属"展示辅助"，列化收益不大。
    if assistant_message_id is not None:
        msg = await db.get(ChatMessage, assistant_message_id)
        if msg is not None:
            msg.skill_invocation_id = log.id
            existing = dict(msg.meta_data or {})
            existing["skill_id"] = str(skill.id)
            existing["skill_name"] = skill.name
            existing["skill_slug"] = skill.slug
            existing["skill_activation_reason"] = "agent_callable"
            msg.meta_data = existing
            await db.flush()

    # 关键的"短事务"修复（行锁卡死）：必须在返回前 commit。
    #
    # 历史故障：上面 ``flush`` 已经发出 ``UPDATE chat_messages SET meta_data=...``，
    # 拿到这条 chat message 的行锁。但 chat 主流程（``_run_chat_task``）的主 db
    # 在整段 LLM 流式 + 工具循环期间一直 ``async with``，事务从此挂着不 commit。
    # 与此同时后台 ``persist()`` 用独立 session 想 UPDATE 同一条 chat_message 的
    # content/meta，直接撞 ``Lock/transactionid``，PostgreSQL 看到的就是
    # "5+ 分钟 idle in transaction"——所有刷新/重发都会跟着卡死。
    #
    # 这里 commit 的范围 **只是本次** SkillUsageLog 插入 + ChatMessage 元数据
    # 回写，与其他 ORM 状态无关，是天然的"短事务"边界；commit 后行锁立即释放，
    # 后台 persist 不再被阻塞，且本次"AI 用了哪个 skill"也立刻持久化（之前
    # 不 commit，浏览器中途 cancel 会丢失徽章数据）。
    await db.commit()

    return json.dumps(
        {
            "skill_slug": skill.slug,
            "skill_name": skill.name,
            "instructions_markdown": wrapped,
        },
        ensure_ascii=False,
    )


@dataclass
class ActivatedSkillInfo:
    """SkillContext 中"已激活"骨架信息，供 chat_service 推 SSE skill_activated 事件用。

    ``activation_reason`` 必须是 SkillUsageLog.activation_reason CHECK 约束允许值之一：
    ``manual / trigger_match / agent_callable / always / auto_apply``
    """

    skill_id: uuid.UUID
    slug: str
    name: str
    activation_reason: str
    matched_trigger: str | None = None


@dataclass
class SkillContext:
    """SkillRouter 输出；空对象须保持三期前 chat 字节级等价。"""

    system_messages: list[dict[str, Any]] = field(default_factory=list)
    candidate_tools: list[dict[str, Any]] = field(default_factory=list)
    active_system_skill_slugs: set[str] = field(default_factory=set)
    skill_id_by_tool_name: dict[str, uuid.UUID] = field(default_factory=dict)
    allowed_platform_tools: frozenset[str] = field(default_factory=frozenset)
    #: Layer 1+2+3 命中条目（不含 agent_callable 候选池——那些只是"可被模型调"，
    #: 真正激活信号由 ``skill_invocation_id`` 在消息里体现）。
    activated_skills: list[ActivatedSkillInfo] = field(default_factory=list)
    #: 本轮内 http_get_json / http_post_json 工具的允许主机清单（来自所有
    #: candidate skill 的 SKILL.md 正文中明文出现的 ``http(s)://host[:port]``）。
    #: 给 ``run_http_*`` 在执行前做 SSRF 闸门。
    allowed_http_hosts: frozenset[str] = field(default_factory=frozenset)


async def _fetch_skills_by_ids(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    ids: list[uuid.UUID],
) -> list[Skill]:
    if not ids:
        return []
    stmt = select(Skill).where(Skill.id.in_(ids), Skill.is_enabled.is_(True))
    if project_id is not None:
        stmt = stmt.where(Skill.project_id == project_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _list_always_skills(db: AsyncSession, project_id: uuid.UUID) -> list[Skill]:
    return await _fetch_always_skills(db, project_id, limit=2)


async def _list_agent_callable(db: AsyncSession, project_id: uuid.UUID) -> list[Skill]:
    return await list_agent_callable_skills(db, project_id, limit=5)


async def compose(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    session: ChatSession,
    user_message: str,
) -> SkillContext:
    """三层激活：always → manual → 触发词 + agent_callable 候选工具。"""
    if project_id is None:
        return SkillContext()

    sys_msgs: list[dict[str, Any]] = []
    cand_tools: list[dict[str, Any]] = []
    cand_tool_names: set[str] = set()
    active_slugs: set[str] = set()
    tool_to_skill: dict[str, uuid.UUID] = {}
    allowed_names: set[str] = set()
    allowed_hosts: set[str] = set()
    activated: list[ActivatedSkillInfo] = []

    # ── Layer 1：always ────────────────────────────────────────────
    always_skills = await _list_always_skills(db, project_id)
    if always_skills:
        body = "\n\n---\n\n".join(s.body for s in always_skills)
        sys_msgs.append({"role": "system", "content": wrap_with_safety(body)})
        for s in always_skills:
            activated.append(
                ActivatedSkillInfo(
                    skill_id=s.id,
                    slug=s.slug,
                    name=s.name,
                    activation_reason="always",
                ),
            )
            allowed_hosts |= extract_allowed_hosts_from_body(s.body or "")
            if s.slug.startswith("system_"):
                active_slugs.add(s.slug)
                allowed_names |= _collect_platform_names(s)
                _append_platform_candidate_tools(s, cand_tools, cand_tool_names)

    # ── Layer 2：manual ─────────────────────────────────────────────
    manual = await _fetch_skills_by_ids(db, project_id, _parse_manual_skill_ids(session))
    for s in manual:
        sys_msgs.append({
            "role": "system",
            "content": (
                f"## 已激活技能：{s.name}\n\n{wrap_with_safety(s.body)}"
            ),
        })
        activated.append(
            ActivatedSkillInfo(
                skill_id=s.id,
                slug=s.slug,
                name=s.name,
                activation_reason="manual",
            ),
        )
        allowed_hosts |= extract_allowed_hosts_from_body(s.body or "")
        if s.slug.startswith("system_"):
            active_slugs.add(s.slug)
            allowed_names |= _collect_platform_names(s)
            _append_platform_candidate_tools(s, cand_tools, cand_tool_names)

    # ── Layer 3：触发词 + agent_callable ───────────────────────────
    triggered = await match_triggers(db, project_id, user_message, max_matches=3)
    triggered_ids: set[uuid.UUID] = {s.id for s in triggered}
    agent_pool = await _list_agent_callable(db, project_id)
    candidates = _dedupe_skills(triggered + agent_pool, max_total=5)

    for s in candidates:
        spec = _build_skill_invoke_tool(s)
        fname = spec["function"]["name"]
        cand_tools.append(spec)
        cand_tool_names.add(fname)
        tool_to_skill[fname] = s.id
        allowed_hosts |= extract_allowed_hosts_from_body(s.body or "")
        if s.slug.startswith("system_"):
            active_slugs.add(s.slug)
            allowed_names |= _collect_platform_names(s)
            _append_platform_candidate_tools(s, cand_tools, cand_tool_names)
        # 仅 trigger 命中视为本轮"已激活"——agent_callable 池里光等候是被动的，
        # 不应让前端 banner 显示"已自动激活：xxx"误导用户。
        if s.id in triggered_ids:
            matched_str = _first_trigger_hit(s, user_message)
            activated.append(
                ActivatedSkillInfo(
                    skill_id=s.id,
                    slug=s.slug,
                    name=s.name,
                    activation_reason="trigger_match",
                    matched_trigger=matched_str,
                ),
            )

    # ── http_get_json / http_post_json 自动暴露 ──
    # 只要本轮存在任何 candidate skill 含有 http(s) URL，就把通用 http 工具放
    # 进 cand_tools；否则保持空、与三期前 chat 字节级等价（不引入冗余 schema）。
    if allowed_hosts:
        for spec in http_tool_schemas():
            fname = spec["function"]["name"]
            if fname in cand_tool_names:
                continue
            cand_tools.append(spec)
            cand_tool_names.add(fname)

    allowed_frozen = frozenset(allowed_names)
    allowed_hosts_frozen = frozenset(allowed_hosts)

    # 轻量系统提示：减少「只说在查却不调 http_*」与工具轮次浪费（不替代 SKILL 正文）
    if allowed_hosts_frozen:
        sys_msgs.append({
            "role": "system",
            "content": (
                "【技能包 HTTP 协议】本轮已启用 http_get_json / http_post_json，"
                "URL 的 host:port 仅允许为各技能 SKILL.md 正文中出现过的地址。\n"
                "执行顺序：① 调用与意图匹配的 skill_*__invoke 加载完整指令；"
                "② 按文档用 http_* 拉取真实响应（JSON 或文本）；"
                "③ 用中文 Markdown 整理结果。\n"
                "需要页面渲染或浏览器端 JS 时，请通过技能文档中的 HTTP API 获取"
                "服务端数据；对话内不提供任意 JS 沙箱执行。\n"
                "禁止只回复「正在查询默认结果」等空话而不调用工具；"
                "禁止编造接口数据。"
            ),
        })

    return SkillContext(
        system_messages=sys_msgs,
        candidate_tools=cand_tools,
        active_system_skill_slugs=active_slugs,
        skill_id_by_tool_name=tool_to_skill,
        allowed_platform_tools=allowed_frozen,
        activated_skills=activated,
        allowed_http_hosts=allowed_hosts_frozen,
    )


def _first_trigger_hit(skill: Skill, user_message: str) -> str | None:
    msg_lower = user_message.lower()
    for raw in skill.triggers or []:
        if not isinstance(raw, str):
            continue
        needle = raw.strip().lower()
        if needle and needle in msg_lower:
            return raw.strip()
    return None
