"""ExecutionPlanCard 装配（Phase 13 / Task 13.1）。

设计依据：``docs/PHASE3_DESIGN.md §10.3 / §10.7 / §10.8``。本模块把 4 个查询
tool 的结果聚合成一张 ``ExecutionPlanCard``，并把 ``plan_id`` 缓存到进程内
（TTL 10 分钟），等用户在前端 ConfirmationCard 上点"确认执行"时，
``POST /api/ui-executions { plan_id }`` 走 task 13.3 接通的 service 用 plan_id
反查这张 plan，**真正派发执行**——LLM 永远拿不到也不能直接调 ``run_ui_test``，
这是设计文档 §10.3.3 的最后一道安全闸门。

M1 阶段简化：
- ``risk_level`` 由 ``_infer_risk_level()`` 启发式从环境名推断（M2 task 13.5
  接入 ``ui_environments.risk_level`` 真实字段后只需替换该函数）
- ``confirmation_strength`` 走"high → STRICT, medium → SOFT, low + 单用例 →
  NONE, low + 多用例 → SOFT"决策树
- ``estimated_duration_seconds`` 用 ``cases × 60s`` baseline + 物料/环境固定
  开销；M2 task 13.6 接通 adhoc_steps 后改为按步骤数估算
- ``test_data_preview`` 取项目级 ``is_default=True`` 物料集首批，secret 项
  返回 ``<masked>``；M2 task 13.5 接入 ``test_data_items.semantic`` 后按用例
  ``required_test_data`` 精准匹配

不动二期 ``ExecutionEngine`` 任何代码——本模块所有逻辑都是只读查询封装。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import User
from app.modules.llm.models import LLMConfig
from app.modules.skills.builtin.ui_automation.schemas import (
    CaseSummary,
    ConfirmationStrength,
    EnvironmentSummary,
    EnvRiskLevel,
    ExecutionPlanCard,
    LLMProviderSummary,
    TestDataPreview,
    TestDataPreviewItem,
)
from app.modules.test_data.models import TestDataItem, TestDataSet
from app.modules.testcases.models import Testcase
from app.modules.ui_automation.models import TestEnvironment

logger = logging.getLogger(__name__)


# ─────────────────── 风险分级（M1 启发式） ────────────────────────


_HIGH_RISK_KEYWORDS = ("production", "prod", "线上", "正式")
_MEDIUM_RISK_KEYWORDS = (
    "preprod", "pre-prod", "pre_prod",  # 必须优先于 high 的 "prod" 子串匹配
    "staging", "stage", "uat", "预发", "灰度", "pre",
)


def _infer_risk_level(env_name: str, base_url: str) -> tuple[EnvRiskLevel, str]:
    """根据环境名 + base_url 启发式推断 risk_level。

    M2 task 13.5 接入 ``ui_environments.risk_level`` 字段后替换此函数即可。
    返回 ``(level, reason)``，``reason`` 透传到前端 tooltip 帮助用户理解
    "为什么这条被判为 high"。

    实现细节：medium 关键词优先匹配（``preprod``/``staging``/``uat`` 等环境
    名常含 "prod" 子串，必须提前消费掉避免误判 high）；其次再做 high 匹配。
    """
    name_lower = (env_name or "").lower()
    url_lower = (base_url or "").lower()

    for kw in _MEDIUM_RISK_KEYWORDS:
        if kw in name_lower or kw in url_lower:
            return EnvRiskLevel.MEDIUM, f"name/url contains keyword {kw!r}"
    for kw in _HIGH_RISK_KEYWORDS:
        if kw in name_lower or kw in url_lower:
            return EnvRiskLevel.HIGH, f"name/url contains keyword {kw!r}"
    return EnvRiskLevel.LOW, "no high/medium-risk keyword in name/url"


def _decide_confirmation(
    risk: EnvRiskLevel, case_count: int,
) -> tuple[ConfirmationStrength, dict]:
    """风险等级 → confirmation_strength 决策表。

    设计 §10.3.2：
    - HIGH → STRICT（必须输入挑战短语 "YES PROD"）
    - MEDIUM → SOFT（单按钮确认）
    - LOW + 单用例 → NONE（自动开始）
    - LOW + 多用例 → SOFT（显式让用户过一眼批量执行内容）
    """
    if risk is EnvRiskLevel.HIGH:
        return (
            ConfirmationStrength.STRICT,
            {
                "message": (
                    "你即将在高风险环境执行 UI 自动化用例，可能影响真实用户数据。"
                ),
                "challenge": "请输入 'YES PROD' 确认（区分大小写）",
                "challenge_value": "YES PROD",
                "ack_label": "我已知晓在高风险环境执行的影响",
            },
        )
    if risk is EnvRiskLevel.MEDIUM:
        return (
            ConfirmationStrength.SOFT,
            {
                "message": (
                    "本次执行将作用于预发/UAT 环境；请确认用例与物料正确后开始。"
                ),
            },
        )
    if case_count > 1:
        return (
            ConfirmationStrength.SOFT,
            {"message": f"本次将批量执行 {case_count} 条用例。"},
        )
    return ConfirmationStrength.NONE, {}


# ─────────────────── 估时（粗略） ─────────────────────────────────


_BASE_PER_CASE_SECONDS = 60
_FIXED_OVERHEAD_SECONDS = 30


def _estimate_duration_seconds(case_count: int) -> int:
    return max(case_count, 1) * _BASE_PER_CASE_SECONDS + _FIXED_OVERHEAD_SECONDS


# ─────────────────── plan_id 进程内缓存（TTL 10 分钟） ────────────


_PLAN_TTL_SECONDS = 10 * 60


@dataclass(slots=True)
class CachedPlan:
    """缓存条目：把"前端可见的 plan card"和"派发执行需要的原始入参"绑在一起。

    Phase 13 / Task 13.3：``POST /api/ui-executions { plan_id }`` 反查这条记录，
    用 ``case_ids / environment_id / llm_config_id`` 还原成
    ``ExecutionCreateRequest`` 给二期 ``start_execution`` 派发，**不让前端篡改**
    用例 / 环境 —— 前端如要换用例只能让 LLM 重新 propose 拿新 plan_id。
    """

    plan: ExecutionPlanCard
    case_ids: list[uuid.UUID]
    environment_id: uuid.UUID
    llm_config_id: uuid.UUID | None
    project_id: uuid.UUID


_plan_cache: dict[uuid.UUID, tuple[float, CachedPlan]] = {}
_plan_cache_lock = asyncio.Lock()


async def _put_plan(entry: CachedPlan) -> None:
    deadline = time.monotonic() + _PLAN_TTL_SECONDS
    async with _plan_cache_lock:
        _plan_cache[entry.plan.plan_id] = (deadline, entry)
        if len(_plan_cache) > 256:
            now = time.monotonic()
            stale = [pid for pid, (d, _p) in _plan_cache.items() if d < now]
            for pid in stale:
                _plan_cache.pop(pid, None)


async def get_cached_plan(plan_id: uuid.UUID) -> CachedPlan | None:
    """task 13.3 的 ``POST /api/ui-executions`` 在 ``plan_id → plan`` 反查时调。"""
    now = time.monotonic()
    async with _plan_cache_lock:
        entry = _plan_cache.get(plan_id)
        if entry is None:
            return None
        deadline, cached = entry
        if deadline < now:
            _plan_cache.pop(plan_id, None)
            return None
        return cached


async def update_cached_plan_skill_card(
    plan_id: uuid.UUID, skill_card_message_id: uuid.UUID,
) -> None:
    """落 kind=skill_card 消息后回写 message_id 到 plan，便于前端"原地变身"。"""
    async with _plan_cache_lock:
        entry = _plan_cache.get(plan_id)
        if entry is None:
            return
        deadline, cached = entry
        cached.plan = cached.plan.model_copy(
            update={"skill_card_message_id": skill_card_message_id},
        )
        _plan_cache[plan_id] = (deadline, cached)


async def _clear_plan_cache_for_test() -> None:
    """仅供测试调用，避免跨用例污染。"""
    async with _plan_cache_lock:
        _plan_cache.clear()


# ─────────────────── 装配 ExecutionPlanCard ─────────────────────


SECRET_KEY_HINTS = ("password", "pwd", "passwd", "secret", "token", "api_key")


def _is_secret_key(key: str) -> bool:
    k = (key or "").lower()
    return any(hint in k for hint in SECRET_KEY_HINTS)


def _mask_value(raw: str | None, *, is_secret: bool) -> str:
    if raw is None:
        return ""
    if is_secret:
        return "<masked>"
    s = str(raw)
    return s if len(s) <= 64 else s[:60] + "..."


async def _load_cases(
    db: AsyncSession, project_id: uuid.UUID, case_ids: list[uuid.UUID],
) -> list[Testcase]:
    if not case_ids:
        return []
    stmt = (
        select(Testcase)
        .where(Testcase.project_id == project_id, Testcase.id.in_(case_ids))
        .limit(20)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    # 按入参 case_ids 顺序返回（让 LLM 给出的执行顺序保持稳定）
    by_id = {tc.id: tc for tc in rows}
    return [by_id[cid] for cid in case_ids if cid in by_id]


async def _load_environment(
    db: AsyncSession, environment_id: uuid.UUID,
) -> TestEnvironment | None:
    stmt = select(TestEnvironment).where(TestEnvironment.id == environment_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_default_data_sets(
    db: AsyncSession, project_id: uuid.UUID, environment_id: uuid.UUID | None,
) -> list[TestDataSet]:
    """M1：取项目级 + 环境级 ``is_default=True`` 物料集，按 scope 优先级排序。

    M2 task 13.5 接通 ``case.required_test_data`` 后会改为按 semantic 精准匹配。
    """
    stmt = select(TestDataSet).options(selectinload(TestDataSet.items)).where(
        TestDataSet.project_id == project_id,
        TestDataSet.is_default.is_(True),
    )
    rows = list((await db.execute(stmt)).scalars().all())

    def _rank(s: TestDataSet) -> int:
        if s.scope == "environment" and s.environment_id == environment_id:
            return 0
        if s.scope == "project":
            return 1
        return 2

    return sorted(rows, key=_rank)


def _build_test_data_preview(
    sets: Iterable[TestDataSet], *, max_items: int = 8,
) -> TestDataPreview:
    items: list[TestDataPreviewItem] = []
    summaries: list[dict] = []
    seen_keys: set[str] = set()
    for s in sets:
        item_count = len(s.items or [])
        summaries.append(
            {
                "id": str(s.id),
                "name": s.name,
                "scope": s.scope,
                "item_count": item_count,
            },
        )
        for it in s.items or []:
            if len(items) >= max_items:
                break
            key = it.key
            if key in seen_keys:
                continue
            seen_keys.add(key)
            is_secret = _is_secret_key(key) or (it.value_type or "") == "secret"
            items.append(
                TestDataPreviewItem(
                    semantic=None,
                    key=key,
                    value_preview=_mask_value(
                        _value_preview_text(it), is_secret=is_secret,
                    ),
                    source=f"{s.name}（{s.scope}）",
                    source_set_id=s.id,
                    is_secret=is_secret,
                ),
            )
        if len(items) >= max_items:
            break
    return TestDataPreview(
        items=items,
        missing_semantics=[],
        set_summaries=summaries,
    )


def _value_preview_text(item: TestDataItem) -> str | None:
    """按 ``value_type`` 选最合适的可预览字段；secret / 加密内容由调用方 mask。

    M1 仅做"字符串展示"——前端 ConfirmationCard 物料区只是给用户瞄一眼"用了
    哪些字段"；secret 始终走 mask 分支，加密密文不会出现在 chat 上下文里。
    """
    vt = (item.value_type or "").lower()
    if vt in ("string", "multiline", "random"):
        return item.value_text
    if vt == "dataset":
        if item.value_json is None:
            return None
        try:
            import json as _json
            return _json.dumps(item.value_json, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(item.value_json)
    if vt == "file":
        return item.file_path
    # secret / 未知 type：交给调用方按 is_secret 走 ``<masked>``
    return None


async def _load_llm_provider_summary(
    db: AsyncSession, llm_config_id: uuid.UUID | None,
) -> LLMProviderSummary:
    if llm_config_id is not None:
        cfg = await db.get(LLMConfig, llm_config_id)
        if cfg is not None:
            return LLMProviderSummary(
                id=cfg.id,
                name=cfg.name,
                provider=cfg.provider,
                model=cfg.model,
            )
    # 兜底：查项目默认 LLMConfig
    stmt = select(LLMConfig).where(LLMConfig.is_default.is_(True)).limit(1)
    cfg = (await db.execute(stmt)).scalar_one_or_none()
    if cfg is not None:
        return LLMProviderSummary(
            id=cfg.id,
            name=cfg.name,
            provider=cfg.provider,
            model=cfg.model,
        )
    return LLMProviderSummary(
        id=None, name="(未配置默认 LLM)", provider="unknown", model="unknown",
    )


async def build_execution_plan(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    user: User | None,  # noqa: ARG001 — 留给 M2 接入用户画像
    case_ids: list[uuid.UUID],
    environment_id: uuid.UUID,
    llm_config_id: uuid.UUID | None = None,
) -> ExecutionPlanCard:
    """聚合 cases + environment + llm + test_data 装配为 ConfirmationCard payload。

    错误（用例 / 环境不存在）走异常上抛——上游 tool wrapper 会把异常转成
    ``{"error": ...}`` JSON 回给 LLM，模型可据此重试或反问用户。
    """
    if not case_ids:
        raise ValueError("case_ids must not be empty")

    cases = await _load_cases(db, project_id, case_ids)
    if not cases:
        raise ValueError("no testcases found for given ids")

    env = await _load_environment(db, environment_id)
    if env is None:
        raise ValueError(f"environment {environment_id} not found")
    if env.project_id != project_id:
        raise ValueError("environment does not belong to project")

    risk_level, risk_reason = _infer_risk_level(env.name, str(env.base_url))
    sets = await _load_default_data_sets(db, project_id, environment_id)
    test_data = _build_test_data_preview(sets)
    llm_summary = await _load_llm_provider_summary(db, llm_config_id)
    strength, confirm_payload = _decide_confirmation(risk_level, len(cases))

    plan = ExecutionPlanCard(
        plan_id=uuid.uuid4(),
        project_id=project_id,
        cases=[
            CaseSummary(
                id=tc.id,
                case_no=tc.case_no,
                title=tc.title,
                priority=tc.priority,
                status=tc.status,
                relevance_score=None,
            )
            for tc in cases
        ],
        environment=EnvironmentSummary(
            id=env.id,
            name=env.name,
            base_url=str(env.base_url),
            risk_level=risk_level,
            risk_reason=risk_reason,
        ),
        llm_provider=llm_summary,
        test_data_preview=test_data,
        estimated_duration_seconds=_estimate_duration_seconds(len(cases)),
        confirmation_strength=strength,
        confirmation_payload=confirm_payload,
        runtime_data_flow=None,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=_PLAN_TTL_SECONDS)
        ).isoformat(),
    )
    await _put_plan(
        CachedPlan(
            plan=plan,
            case_ids=[tc.id for tc in cases],
            environment_id=env.id,
            llm_config_id=(llm_summary.id if llm_summary.id else None),
            project_id=project_id,
        ),
    )
    return plan


__all__ = [
    "_PLAN_TTL_SECONDS",
    "_clear_plan_cache_for_test",
    "_decide_confirmation",
    "_estimate_duration_seconds",
    "_infer_risk_level",
    "build_execution_plan",
    "CachedPlan",
    "get_cached_plan",
    "update_cached_plan_skill_card",
]
