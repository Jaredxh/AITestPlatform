"""SkillRouter — chat 消息侧三层激活与 lazy skill_invoke（Phase 12 / Task 12.2）。"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.llm.models import ChatSession
from app.modules.skills.builtin.ui_automation.intent_classifier import (
    UI_AUTOMATION_INTENT_GUARDED,
    LLMClassifierCallable,
)
from app.modules.skills.builtin.ui_automation.intent_classifier import (
    classify as classify_intent,
)
from app.modules.skills.builtin.ui_automation.tools import (
    UI_AUTOMATION_TOOL_NAMES,
    ui_automation_chat_openai_schemas,
)
from app.modules.skills.http_tools import (
    extract_allowed_hosts_from_body,
    http_tool_schemas,
)
from app.modules.skills.importer import skill_version_directory
from app.modules.skills.models import Skill, SkillUsageLog
from app.modules.skills.platform_tools import platform_chat_openai_schemas
from app.modules.skills.safety import extract_when_to_use, wrap_with_safety
from app.modules.skills.script_tools import (
    AllowedScript,
    SkillRoot,
    extract_allowed_scripts_from_body,
    script_tool_schema,
)
from app.modules.skills.skill_fs_tools import skill_fs_tool_schemas
from app.modules.skills.service import (
    list_agent_callable_skills,
)
from app.modules.skills.service import (
    list_always_skills as _fetch_always_skills,
)
from app.modules.skills.triggers import match_triggers

logger = logging.getLogger(__name__)


#: Phase 13 / Task 13.1 — LLM 安全黑名单。
#:
#: 设计依据：``docs/PHASE3_DESIGN.md §10.3.3 / §10.7``。``platform_run_ui_execution``
#: 等价于"run_ui_test"——直接派发 UI 自动化执行；**永远**不能暴露给 LLM。
#: LLM 想触发执行只能调 ``system__ui_automation__propose_execution_plan`` 生成
#: ConfirmationCard，由用户在前端"确认执行"按钮触发专门 API 派发。
#:
#: 即使内置 SKILL.md 历史 ``tools_required`` 写了它（Phase 12 老配置兼容），
#: ``_append_platform_candidate_tools`` 与 ``_collect_platform_names`` 都会把
#: 它主动剔除；``safe_invoke.safe_run_tool`` 也会在执行层做第二道闸拒绝。
LLM_FORBIDDEN_PLATFORM_TOOLS: frozenset[str] = frozenset({
    "platform_run_ui_execution",
})


#: Phase 13 / Task 13.1 — 与 ``system_ui_automation`` 内置 skill 绑定的 4 个
#: ``system__ui_automation__*`` agent tool 名集合。这些 tool **不是**
#: ``platform_*`` 命名空间，由 ``ensure_ui_automation_tools_registered`` 注册到
#: ``TOOL_REGISTRY``，安全闸门走 ``safe_invoke`` 的 system_ui_automation slug
#: 校验（见 §10.7）。
SYSTEM_UI_AUTOMATION_TOOL_NAMES: frozenset[str] = frozenset(UI_AUTOMATION_TOOL_NAMES)


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
        if not isinstance(t, str) or not t.startswith("platform_"):
            continue
        # Phase 13 / Task 13.1 — 黑名单 platform_run_ui_execution：即使 SKILL.md
        # 历史 tools_required 仍写了它，也不放进 allowed_platform_tools，
        # safe_invoke 第二道闸据此拒绝执行。
        if t in LLM_FORBIDDEN_PLATFORM_TOOLS:
            continue
        names.add(t)
    return names


def _append_platform_candidate_tools(
    skill: Skill,
    cand_tools: list[dict[str, Any]],
    cand_tool_names: set[str],
) -> None:
    """第一道闸：仅 ``system_*`` slug 暴露 platform_* OpenAI specs。

    Phase 13 / Task 13.1：``platform_run_ui_execution`` 永远不放进 LLM tool
    list（屏蔽于 ``LLM_FORBIDDEN_PLATFORM_TOOLS``），无论 SKILL.md 是否声明。
    """
    if not skill.slug.startswith("system_"):
        return
    specs = _platform_specs()
    for tname in skill.tools_required or []:
        if not isinstance(tname, str) or not tname.startswith("platform_"):
            continue
        if tname in LLM_FORBIDDEN_PLATFORM_TOOLS:
            continue
        spec = specs.get(tname)
        if spec is None:
            continue
        fname = spec["function"]["name"]
        if fname in cand_tool_names:
            continue
        cand_tools.append(spec)
        cand_tool_names.add(fname)


def _append_ui_automation_candidate_tools(
    cand_tools: list[dict[str, Any]],
    cand_tool_names: set[str],
) -> None:
    """``system_ui_automation`` 激活时把 4 个 ``system__ui_automation__*`` tool 加入
    LLM 工具集（Phase 13 / Task 13.1）。

    与 ``platform_*`` 不同，这 4 个 tool 由本期独立实现（task 13.1 stub /
    task 13.2 升级智能匹配）；幂等，重复 append 同名 spec 自动去重。
    """
    for fname, spec in ui_automation_chat_openai_schemas().items():
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
    #: 本轮内"作者推荐入口"的脚本 hint 列表（来自 SKILL.md 正文中明文写过的
    #: ``python xxx.py`` / ``node yyy.js`` / ``bash xxx.sh``）。**只用作
    #: system prompt 里 LLM 的提示**——告诉它"这个 skill 作者点名了这些命令"，
    #: 不再是运行时闸门。重构前是闸门，但 OpenClaw 信任模型下"安装即信任"，
    #: 闸门下沉到 :attr:`skill_roots`。
    allowed_skill_scripts: frozenset[AllowedScript] = field(default_factory=frozenset)
    #: 本轮内"LLM 可访问"的 skill 附件根目录集合（每个 candidate skill 一条）。
    #: ``run_skill_script`` / ``read_skill_file`` / ``list_skill_files`` 三个工
    #: 具的统一运行时闸门：``skill_slug`` 必须命中本集合，目标路径必须在该
    #: skill 的 abs_dir 之内。这是 OpenClaw 信任模型的核心载体。
    skill_roots: frozenset[SkillRoot] = field(default_factory=frozenset)


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


#: Phase 13 / Task 13.0 — ui_automation 二段式 NLU 校验阈值。低于此值的
#: ``execute_test`` 视为"不够明确"，候选剔除让 LLM 走普通问答（设计文档
#: §10.0.4：纯名词"登录用例"/conf=0.55 时让 AI 反问而不是直接执行）。
_UI_AUTOMATION_MIN_INTENT_CONFIDENCE = 0.7


async def compose(
    db: AsyncSession,
    project_id: uuid.UUID | None,
    session: ChatSession,
    user_message: str,
    *,
    intent_llm_classifier: LLMClassifierCallable | None = None,
) -> SkillContext:
    """三层激活：always → manual → 触发词 + agent_callable 候选工具。

    Phase 13 / Task 13.0：在三层组装末尾对 ``UI_AUTOMATION_INTENT_GUARDED``
    内的 skill 单独跑一次 NLU；意图非 ``execute_test`` 或置信度不够时把这些
    候选从 ``candidate_tools`` / ``activated_skills`` 剔除，避免"昨天跑用例
    失败率"被关键词召回误触发。

    ``intent_llm_classifier`` 为可选参数：调用方可注入"接收 prompt 返回 JSON
    字符串"的 awaitable 给 IntentClassifier Layer 2 兑底；不传时只跑 Layer 1
    规则——M1 task 13.0 阶段的 DoD 全部反例靠规则即可命中，故 chat_service
    主调用点目前不传，保持热路径零额外 LLM 延迟。
    """
    if project_id is None:
        return SkillContext()

    sys_msgs: list[dict[str, Any]] = []
    cand_tools: list[dict[str, Any]] = []
    cand_tool_names: set[str] = set()
    active_slugs: set[str] = set()
    tool_to_skill: dict[str, uuid.UUID] = {}
    allowed_names: set[str] = set()
    allowed_hosts: set[str] = set()
    allowed_scripts: set[AllowedScript] = set()
    skill_roots: set[SkillRoot] = set()
    activated: list[ActivatedSkillInfo] = []

    def _absorb_skill_resources(s: Skill) -> None:
        """把 SKILL.md 的 http host、附件根目录、脚本 hint 登记到本轮上下文。

        非 system 的 skill（``project_id is not None``）才会:

        - 加 :class:`SkillRoot`（``run_skill_script`` / ``read_skill_file`` /
          ``list_skill_files`` 的运行时闸门）；
        - 抽脚本 hint（仅 system prompt 用）。

        ``allowed_hosts`` 不区分 system / 非 system——内置 skill 也可能引用
        平台 API URL（兼容老逻辑）。
        """
        body = s.body or ""
        allowed_hosts.update(extract_allowed_hosts_from_body(body))
        # ``project_id is None`` 是平台自带的 system_* skill；这些 skill 的
        # SKILL.md 由内部托管，无附件脚本，不进入 SkillRoot 系统。
        if s.project_id is None:
            return
        try:
            attach_dir = skill_version_directory(s.project_id, s.id, s.db_version)
        except Exception:  # noqa: BLE001 —— 路径计算失败不该把整轮 chat 拖死
            logger.warning("skill_version_directory failed for slug=%s", s.slug, exc_info=True)
            return
        skill_roots.add(SkillRoot(skill_slug=s.slug, abs_dir=str(attach_dir)))
        scripts = extract_allowed_scripts_from_body(
            body,
            skill_slug=s.slug,
            abs_dir=attach_dir,
        )
        if scripts:
            allowed_scripts.update(scripts)

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
            _absorb_skill_resources(s)
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
        _absorb_skill_resources(s)
        if s.slug.startswith("system_"):
            active_slugs.add(s.slug)
            allowed_names |= _collect_platform_names(s)
            _append_platform_candidate_tools(s, cand_tools, cand_tool_names)

    # ── Layer 3：触发词 + agent_callable ───────────────────────────
    triggered = await match_triggers(db, project_id, user_message, max_matches=3)
    triggered_ids: set[uuid.UUID] = {s.id for s in triggered}
    agent_pool = await _list_agent_callable(db, project_id)
    candidates = _dedupe_skills(triggered + agent_pool, max_total=5)

    # Phase 13 / Task 13.0 — ui_automation 二段式 NLU 校验。
    #
    # 设计依据：``docs/PHASE3_DESIGN.md §10.0.3``。仅在候选池含
    # ``UI_AUTOMATION_INTENT_GUARDED`` 内 skill 时跑一次 classify；意图非
    # ``execute_test`` 或置信度 < 0.7 → 把这些 skill 从 candidates 剔除，避免
    # "昨天跑用例失败率"被"跑/用例"关键词召回误触发执行。
    #
    # 注意：剔除范围**只是 trigger / agent_callable 这一层候选**；always /
    # manual 主动激活的同 slug skill 不受影响（用户明确选择优先于 NLU）。
    ui_guarded_in_candidates = [
        s for s in candidates if s.slug in UI_AUTOMATION_INTENT_GUARDED
    ]
    if ui_guarded_in_candidates:
        intent = await classify_intent(
            user_message,
            session_id=getattr(session, "id", None),
            llm_classifier=intent_llm_classifier,
        )
        guarded_passes = (
            intent.action == "execute_test"
            and intent.confidence >= _UI_AUTOMATION_MIN_INTENT_CONFIDENCE
        )
        if not guarded_passes:
            dropped_slugs = [s.slug for s in ui_guarded_in_candidates]
            candidates = [
                s for s in candidates if s.slug not in UI_AUTOMATION_INTENT_GUARDED
            ]
            triggered_ids = {s.id for s in candidates if s.id in triggered_ids}
            logger.info(
                "ui_automation candidates dropped: intent=%s conf=%.2f slugs=%s",
                intent.action, intent.confidence, dropped_slugs,
            )

    for s in candidates:
        spec = _build_skill_invoke_tool(s)
        fname = spec["function"]["name"]
        cand_tools.append(spec)
        cand_tool_names.add(fname)
        tool_to_skill[fname] = s.id
        _absorb_skill_resources(s)
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

    # ── system_ui_automation 4 个 system__ui_automation__* 专属工具 ──
    #
    # Phase 13 / Task 13.1：只要本轮 always / manual / trigger / agent_callable
    # 任意层激活了 ``system_ui_automation`` slug，就把 4 个 system__ui_automation__*
    # tool 加入 LLM tool 列表。这些 tool 不走 platform_* 命名空间，是 ui_automation
    # skill 的私有工具集（设计文档 §10.7 Tool 工具集）；``run_ui_test`` 不在其中。
    if "system_ui_automation" in active_slugs:
        _append_ui_automation_candidate_tools(cand_tools, cand_tool_names)

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

    # ── 技能附件操作三件套自动暴露（OpenClaw 信任模型）──
    # 任意 candidate skill 是非 system_*（即用户安装的）→ 它就有 attach 目录，
    # 一律把 ``run_skill_script`` + ``read_skill_file`` + ``list_skill_files``
    # 加进 LLM 工具集。运行时闸门（slug 是否本轮可见 + 路径是否在 attach 目录
    # 内 + 扩展名 / 二进制 / 黑名单）由各 tool 自己持有。
    if skill_roots:
        for spec in [script_tool_schema(), *skill_fs_tool_schemas()]:
            fname = spec["function"]["name"]
            if fname in cand_tool_names:
                continue
            cand_tools.append(spec)
            cand_tool_names.add(fname)

    allowed_frozen = frozenset(allowed_names)
    allowed_hosts_frozen = frozenset(allowed_hosts)
    allowed_scripts_frozen = frozenset(allowed_scripts)
    skill_roots_frozen = frozenset(skill_roots)

    # 轻量系统提示：把工具能力 + skill 入口 hint 一并给 LLM，避免它 30k 字看完
    # SKILL.md 后又开始挣扎找 API。
    if allowed_hosts_frozen or skill_roots_frozen:
        msg_parts: list[str] = ["【技能包工具协议】本轮已启用以下平台工具："]
        if allowed_hosts_frozen:
            msg_parts.append(
                "- http_get_json / http_post_json：调 SKILL.md 正文里出现过的 http(s) 接口；"
                "URL host:port 必须在白名单里。",
            )
        if skill_roots_frozen:
            msg_parts.append(
                "- run_skill_script：执行任意已激活技能附件目录里的 .py / .js / .sh 脚本"
                "（SKILL.md 里写过的入口 + 同 skill 下其它脚本都行）。"
                "**不要依赖 stdin 交互**——优先用脚本提供的非交互参数（如 `-c`、`--check`）；"
                "wall-clock 35s 必杀。",
            )
            msg_parts.append(
                "- read_skill_file：读取已激活技能附件里的文本文件"
                "（references/*.md、prompts/*.md、configs/*.yaml 等）。"
                "用于查阅文档引用的资料、检查脚本源码确定参数、对比配置示例。",
            )
            msg_parts.append(
                "- list_skill_files：列出已激活技能附件目录树，发现可用资源。"
                "建议在你不熟悉某个 skill 结构时**先调一次 list_skill_files** 探查。",
            )

        # SKILL.md 作者点名的常用入口 → 系统提示划重点（命中率高）
        if allowed_scripts_frozen:
            by_slug: dict[str, list[str]] = {}
            for sc in allowed_scripts_frozen:
                by_slug.setdefault(sc.skill_slug, []).append(f"{sc.interpreter} {sc.relpath}")
            entry_lines: list[str] = []
            for slug in sorted(by_slug):
                cmds = sorted(set(by_slug[slug]))
                entry_lines.append(f"  · {slug}: {', '.join(cmds[:8])}")
            msg_parts.append("作者推荐的脚本入口（来自 SKILL.md 正文）：")
            msg_parts.extend(entry_lines)

        # 本轮所有可访问 skill_slug 列表——LLM 调三件套时填 ``skill_slug`` 用
        if skill_roots_frozen:
            slugs = sorted({r.skill_slug for r in skill_roots_frozen})
            msg_parts.append(f"本轮可访问 skill_slug：{slugs}")

        msg_parts.append(
            "执行顺序建议：① 不熟悉的 skill 先 list_skill_files 探查；"
            "② 调 skill_*__invoke 或 read_skill_file 加载指令 / 文档；"
            "③ 按文档调 run_skill_script（如有）/ http_*；"
            "④ 用中文 Markdown 整理结果给用户。",
        )
        msg_parts.append(
            "禁止凭脑补编造接口数据；禁止只回复「正在查询」等空话而不调工具；"
            "禁止用 http_* 自己拼装本应由脚本处理的鉴权 / 签名逻辑。",
        )
        sys_msgs.append({"role": "system", "content": "\n".join(msg_parts)})

    return SkillContext(
        system_messages=sys_msgs,
        candidate_tools=cand_tools,
        active_system_skill_slugs=active_slugs,
        skill_id_by_tool_name=tool_to_skill,
        allowed_platform_tools=allowed_frozen,
        activated_skills=activated,
        allowed_http_hosts=allowed_hosts_frozen,
        allowed_skill_scripts=allowed_scripts_frozen,
        skill_roots=skill_roots_frozen,
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
