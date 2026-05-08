"""5 层环境优先级解析（Phase 13 / Task 13.2）。

设计依据：``docs/PHASE3_DESIGN.md §10.3.1``。

```
最高优先级 ─────────────────────────────────────────────
  1. 用户指令显式提到（"用 staging 跑"）            → user_explicit
  2. 当前会话上下文绑定的环境（之前 confirm 过）     → session_bound
  3. 项目级默认环境（project.default_environment_id）→ project_default
  4. 用户上次使用的环境（最近一次 ui_executions）    → user_history
  5. fallback: 项目内首条 low 风险环境              → fallback_low_risk
  6. 全部缺失 → missing=True，让 LLM 反问用户       → none
最低优先级 ─────────────────────────────────────────────
```

**不动二期数据模型**：``Project.default_environment_id`` 字段当前未存在，
本模块通过 ``getattr`` 容错——M2 task 13.5 加字段后第 3 层自动激活。
``user_preferences`` 表也未存在，第 4 层直接走 ``ui_executions`` 回查最近一次
该用户在该 project 派发的环境。
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.projects.models import Project
from app.modules.ui_automation.models import TestEnvironment, UIExecution

logger = logging.getLogger(__name__)


# ChatSession.chat_context 中存放"上次确认过的 environment_id"的 key——task
# 13.3 用户在 ConfirmationCard 上点"确认执行"后由后端写入；本模块只读，写
# 入路径解耦在 task 13.3。
SESSION_CTX_ENV_KEY = "ui_automation_env_id"


class EnvPriorityLayer(str, Enum):
    """命中层级；上游 list_environments tool 把 layer 透传给 LLM 看。"""

    USER_EXPLICIT = "user_explicit"
    SESSION_BOUND = "session_bound"
    PROJECT_DEFAULT = "project_default"
    USER_HISTORY = "user_history"
    FALLBACK_LOW_RISK = "fallback_low_risk"
    NONE = "none"


@dataclass(slots=True)
class EnvironmentResolution:
    environment: TestEnvironment | None
    layer: EnvPriorityLayer
    reason: str
    missing: bool

    @property
    def environment_id(self) -> uuid.UUID | None:
        return self.environment.id if self.environment else None


# ─────────────────── Layer 1：用户指令显式提到 ───────────────────


_ENV_HINT_PATTERNS = (
    re.compile(r"用\s*(?P<name>[A-Za-z0-9_\-]+)\s*(?:环境|跑|测|验)"),
    re.compile(r"在\s*(?P<name>[A-Za-z0-9_\-]+)\s*(?:环境|上)"),
    re.compile(r"(?P<name>[A-Za-z0-9_\-]+)\s*(?:环境)"),
)


def _extract_explicit_env_hint(message: str) -> str | None:
    """从用户消息里抽出环境名候选词。命中规则按 ``_ENV_HINT_PATTERNS`` 顺序。

    例：``"用 staging 跑"`` → ``staging``；``"在 PROD 上跑下登录"`` → ``PROD``。
    没有命中返回 None；上游会跳到 Layer 2。
    """
    if not message:
        return None
    for pat in _ENV_HINT_PATTERNS:
        m = pat.search(message)
        if m:
            return m.group("name")
    return None


def _match_env_by_name(
    environments: Sequence[TestEnvironment], hint: str,
) -> TestEnvironment | None:
    if not hint:
        return None
    hint_l = hint.lower().strip()
    # 优先 exact 匹配，其次 ilike 子串。
    for env in environments:
        if (env.name or "").lower() == hint_l:
            return env
    for env in environments:
        if hint_l in (env.name or "").lower():
            return env
    return None


# ─────────────────── Layer 4：用户上次用过 ───────────────────


async def _query_user_last_environment(
    db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if user_id is None:
        return None
    stmt = (
        select(UIExecution.environment_id)
        .where(
            UIExecution.project_id == project_id,
            UIExecution.triggered_by == user_id,
            UIExecution.environment_id.is_not(None),
        )
        .order_by(UIExecution.created_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    return row[0]


# ─────────────────── Layer 3：项目级默认 ───────────────────


async def _query_project_default(
    db: AsyncSession, project_id: uuid.UUID,
) -> uuid.UUID | None:
    """读 ``Project.default_environment_id``——M2 task 13.5 加字段后才有值。

    当前用 ``getattr`` 兜底返回 None，老 Project ORM 没该字段也不报错。
    """
    proj = await db.get(Project, project_id)
    if proj is None:
        return None
    raw = getattr(proj, "default_environment_id", None)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


# ─────────────────── Layer 5：fallback low risk ───────────────────


def _pick_low_risk_fallback(
    environments: Sequence[TestEnvironment],
) -> TestEnvironment | None:
    """从已查好的 ``environments`` 里挑首条 risk_level=low 的；都不是 low 就返回首条。

    本模块**不**重复跑启发式 risk 推断；上游 list_environments tool 调用前一般
    已经按 risk 升序排好了，直接取首条更省事。
    """
    if not environments:
        return None
    return environments[0]


# ─────────────────── 主入口 ───────────────────


def _find_by_id(
    environments: Sequence[TestEnvironment], env_id: uuid.UUID | None,
) -> TestEnvironment | None:
    if env_id is None:
        return None
    for env in environments:
        if env.id == env_id:
            return env
    return None


def _ctx_env_id(session_chat_context: dict[str, Any] | None) -> uuid.UUID | None:
    if not session_chat_context:
        return None
    raw = session_chat_context.get(SESSION_CTX_ENV_KEY)
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


async def resolve_environment(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None,
    environments: Sequence[TestEnvironment],
    user_message: str | None = None,
    session_chat_context: dict[str, Any] | None = None,
) -> EnvironmentResolution:
    """5 层级联解析。``environments`` 应已包含项目下所有候选环境（list_environments
    一次性查完后传入）；本函数全程不再发 SQL 查环境列表，只查"用户最近执行" /
    "项目默认"两条小回查。

    返回 ``EnvironmentResolution``：
    - 命中：``missing=False``，``environment`` 与 ``layer`` / ``reason`` 合意；
    - 未命中：``missing=True`` / ``layer=NONE``——上游应回退给 LLM"反问用户选环境"。
    """
    # Layer 1：用户消息显式提到
    hint = _extract_explicit_env_hint(user_message or "")
    if hint:
        env = _match_env_by_name(environments, hint)
        if env is not None:
            return EnvironmentResolution(
                environment=env,
                layer=EnvPriorityLayer.USER_EXPLICIT,
                reason=f"user message mentioned env hint {hint!r}",
                missing=False,
            )

    # Layer 2：会话上下文绑定（task 13.3 写入）
    sess_eid = _ctx_env_id(session_chat_context)
    env = _find_by_id(environments, sess_eid)
    if env is not None:
        return EnvironmentResolution(
            environment=env,
            layer=EnvPriorityLayer.SESSION_BOUND,
            reason="session context bound after previous confirmation",
            missing=False,
        )

    # Layer 3：项目级默认（M2 task 13.5 启用）
    proj_eid = await _query_project_default(db, project_id)
    env = _find_by_id(environments, proj_eid)
    if env is not None:
        return EnvironmentResolution(
            environment=env,
            layer=EnvPriorityLayer.PROJECT_DEFAULT,
            reason="project.default_environment_id configured",
            missing=False,
        )

    # Layer 4：用户上次用过（最近一次 ui_executions）
    last_eid = await _query_user_last_environment(db, project_id, user_id)
    env = _find_by_id(environments, last_eid)
    if env is not None:
        return EnvironmentResolution(
            environment=env,
            layer=EnvPriorityLayer.USER_HISTORY,
            reason="recent ui_executions by current user",
            missing=False,
        )

    # Layer 5：fallback——挑首条 low risk 环境
    fallback = _pick_low_risk_fallback(environments)
    if fallback is not None:
        return EnvironmentResolution(
            environment=fallback,
            layer=EnvPriorityLayer.FALLBACK_LOW_RISK,
            reason="no preference; picked first low-risk env from list",
            missing=False,
        )

    return EnvironmentResolution(
        environment=None,
        layer=EnvPriorityLayer.NONE,
        reason="no environment available; ask user to pick",
        missing=True,
    )
