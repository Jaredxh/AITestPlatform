"""Phase 13 / Task 13.2 — env_priority 5 层优先级解析单测。

DoD：用户上次跑了 staging → 下次同 query 不指定环境 → 默认填 staging；
项目级 default_environment_id 配置后，新用户首次跑也能命中默认；覆盖
3 种 fallback 路径（任务里写 3 种，本 test 实际覆盖 6 种命中 layer）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.skills.builtin.ui_automation.matchers.env_priority import (
    SESSION_CTX_ENV_KEY,
    EnvPriorityLayer,
    _extract_explicit_env_hint,
    resolve_environment,
)


@dataclass
class _StubEnv:
    name: str
    base_url: str = "https://x.com"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def _envs(*names: str) -> list[_StubEnv]:
    return [_StubEnv(name=n) for n in names]


# ───────────────── extract hint 单测 ─────────────────


def test_extract_hint_yong_xxx_pattern() -> None:
    """``用 staging 跑下登录`` → staging。"""
    assert _extract_explicit_env_hint("用 staging 跑下登录") == "staging"


def test_extract_hint_zai_xxx_shang_pattern() -> None:
    """``在 PROD 上跑下`` → PROD。"""
    assert _extract_explicit_env_hint("在 PROD 上跑下") == "PROD"


def test_extract_hint_xxx_huanjing() -> None:
    """``dev 环境再跑一遍`` → dev。"""
    assert _extract_explicit_env_hint("dev 环境再跑一遍") == "dev"


def test_extract_hint_no_match_returns_none() -> None:
    assert _extract_explicit_env_hint("跑下登录用例") is None


# ───────────────── 5 层优先级集成测试 ─────────────────


@pytest.mark.asyncio
async def test_layer1_user_explicit_wins_over_session_bound() -> None:
    """用户消息明说"用 staging 跑" → 即使 session 绑了 prod 也走 user_explicit。"""
    pid = uuid.uuid4()
    dev, staging, prod = _envs("dev", "staging", "prod")

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)  # project default 不存在
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[dev, staging, prod],
        user_message="用 staging 跑",
        session_chat_context={SESSION_CTX_ENV_KEY: str(prod.id)},
    )

    assert resolution.layer is EnvPriorityLayer.USER_EXPLICIT
    assert resolution.environment is staging
    assert resolution.missing is False


@pytest.mark.asyncio
async def test_layer2_session_bound_when_no_user_explicit() -> None:
    pid = uuid.uuid4()
    dev, staging = _envs("dev", "staging")

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[dev, staging],
        user_message="跑下登录用例",
        session_chat_context={SESSION_CTX_ENV_KEY: str(staging.id)},
    )

    assert resolution.layer is EnvPriorityLayer.SESSION_BOUND
    assert resolution.environment is staging


@pytest.mark.asyncio
async def test_layer3_project_default_when_present() -> None:
    """``Project.default_environment_id`` 配置后命中（M2 task 13.5 启用此字段）。"""
    pid = uuid.uuid4()
    dev, staging = _envs("dev", "staging")

    fake_project = MagicMock()
    fake_project.default_environment_id = staging.id
    db = AsyncMock()
    db.get = AsyncMock(return_value=fake_project)
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[dev, staging],
        user_message="跑下登录",
        session_chat_context=None,
    )

    assert resolution.layer is EnvPriorityLayer.PROJECT_DEFAULT
    assert resolution.environment is staging


@pytest.mark.asyncio
async def test_layer4_user_history_when_no_other_signal() -> None:
    """用户上次在该项目用了 staging → 下次同 query 默认填 staging。"""
    pid = uuid.uuid4()
    user_id = uuid.uuid4()
    dev, staging = _envs("dev", "staging")

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    res_proxy = MagicMock()
    # 模拟 ``select(UIExecution.environment_id) ... .first()`` 命中 staging.id
    res_proxy.first = MagicMock(return_value=(staging.id,))
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=user_id,
        environments=[dev, staging],
        user_message="跑下登录",
        session_chat_context=None,
    )

    assert resolution.layer is EnvPriorityLayer.USER_HISTORY
    assert resolution.environment is staging


@pytest.mark.asyncio
async def test_layer5_fallback_low_risk_when_no_signals() -> None:
    """全部信号缺失 → 取传入 environments 的首条（上游已按 risk 升序排好）。"""
    pid = uuid.uuid4()
    dev = _StubEnv(name="dev", base_url="https://dev.x.com")
    prod = _StubEnv(name="prod", base_url="https://prod.x.com")

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[dev, prod],  # 上游已按 low/medium/high 升序排
        user_message=None,
        session_chat_context=None,
    )

    assert resolution.layer is EnvPriorityLayer.FALLBACK_LOW_RISK
    assert resolution.environment is dev


@pytest.mark.asyncio
async def test_layer_none_when_no_environments_at_all() -> None:
    pid = uuid.uuid4()
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[],
        user_message=None,
        session_chat_context=None,
    )

    assert resolution.layer is EnvPriorityLayer.NONE
    assert resolution.environment is None
    assert resolution.missing is True


@pytest.mark.asyncio
async def test_invalid_session_ctx_uuid_falls_through() -> None:
    """session_chat_context 中是非 UUID 字符串 → 静默跳过 Layer 2 进入下一层。"""
    pid = uuid.uuid4()
    dev = _envs("dev")[0]

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    res_proxy = MagicMock()
    res_proxy.first = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=res_proxy)

    resolution = await resolve_environment(
        db,
        project_id=pid,
        user_id=uuid.uuid4(),
        environments=[dev],
        user_message=None,
        session_chat_context={SESSION_CTX_ENV_KEY: "not-a-uuid"},
    )

    # 应跳到 fallback_low_risk（低风险首条）；不应抛错
    assert resolution.layer is EnvPriorityLayer.FALLBACK_LOW_RISK
