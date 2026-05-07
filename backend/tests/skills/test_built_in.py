"""Task 12.4 — 内置 skill 规格常量 + ``sync_built_in_skills`` 行为合约。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.skills import built_in
from app.modules.skills.built_in import sync_built_in_skills


def test_builtin_expected_three_slugs() -> None:
    assert built_in._EXPECTED_SLUGS == frozenset({  # noqa: SLF001
        "system_ui_automation",
        "system_requirement_review",
        "system_testcase_generation",
    })
    assert len(built_in.BUILTIN_SPECS) == 3


def test_bundle_meta_includes_version() -> None:
    spec = built_in.BUILTIN_SPECS[0]
    meta = built_in._bundle_meta(spec)  # noqa: SLF001
    assert meta["_system_bundle_version"] == built_in.SYSTEM_SKILLS_VERSION


def _mock_db_returning(existing: list) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=existing)
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=exec_result)
    return db


@pytest.mark.asyncio
async def test_sync_creates_three_when_missing() -> None:
    db = _mock_db_returning([])
    pid = uuid.uuid4()
    uid = uuid.uuid4()

    created = await sync_built_in_skills(db, pid, created_by=uid)

    assert created == 3
    # 每条 spec 写 3 行（Skill + SkillVersion + SkillSafetyScan）→ 9 次 add
    assert db.add.call_count == 9
    # delete 走的是 db.execute（非 db.delete），仅断 created 数量与 add 次数足够


@pytest.mark.asyncio
async def test_sync_skips_when_version_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    """已有 3 条且 _system_bundle_version 一致 → no-op，返回 0。"""

    class _FakeSkill:
        def __init__(self, slug: str, version: str) -> None:
            self.slug = slug
            self.extra_metadata = {"_system_bundle_version": version}

    existing = [
        _FakeSkill(s.slug, built_in.SYSTEM_SKILLS_VERSION) for s in built_in.BUILTIN_SPECS
    ]
    db = _mock_db_returning(existing)

    created = await sync_built_in_skills(db, uuid.uuid4(), created_by=uuid.uuid4())
    assert created == 0
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_sync_rewrites_when_version_bumped() -> None:
    """version 不一致 → 重写：3 条新 skill。"""

    class _FakeSkill:
        def __init__(self, slug: str) -> None:
            self.slug = slug
            self.extra_metadata = {"_system_bundle_version": "0.0"}

    existing = [_FakeSkill(s.slug) for s in built_in.BUILTIN_SPECS]
    db = _mock_db_returning(existing)

    created = await sync_built_in_skills(db, uuid.uuid4(), created_by=uuid.uuid4())
    assert created == 3
