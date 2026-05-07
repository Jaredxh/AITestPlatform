"""Task 12.4 — SkillService 核心分支（mock DB）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.exceptions import PermissionDeniedException
from app.modules.auth.models import User
from app.modules.skills.models import Skill
from app.modules.skills.schemas import SkillCreateRequest, SkillUpdateRequest
from app.modules.skills.service import SkillService


def _user(uid: uuid.UUID | None = None) -> User:
    u = User(
        email="t@example.com",
        username="tuser",
        hashed_password="x",
        display_name="T",
        is_active=True,
    )
    if uid:
        u.id = uid
    return u


@pytest.mark.asyncio
async def test_delete_built_in_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    owner_id = uuid.uuid4()

    skill = Skill(
        id=sid,
        project_id=pid,
        name="BI",
        slug="system_ui_automation",
        description="d",
        body="# B\n\n## 何时使用\nu.\n",
        source="built_in",
        created_by=owner_id,
    )

    monkeypatch.setattr(
        SkillService,
        "_get_skill_in_project",
        staticmethod(AsyncMock(return_value=skill)),
    )

    with pytest.raises(PermissionDeniedException):
        await SkillService.delete_skill(db, sid, _user(owner_id))


@pytest.mark.asyncio
async def test_toggle_allow_flip_no_explicit_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    uid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    skill = Skill(
        id=sid,
        project_id=pid,
        name="x",
        slug="custom-a",
        description="d",
        body="# B\n\n## 何时使用\nu.\n",
        semantic_version="1.0.0",
        category="custom",
        tags=[],
        triggers=[],
        tools_required=[],
        activation_mode="agent_callable",
        extra_metadata={},
        attachments=[],
        safety_scan_status="clean",
        source="custom",
        is_enabled=True,
        db_version=1,
        created_by=uid,
        created_at=now,
        updated_at=now,
    )

    monkeypatch.setattr(
        SkillService,
        "_get_skill_in_project",
        staticmethod(AsyncMock(return_value=skill)),
    )

    out = await SkillService.toggle_skill(db, sid, _user(uid), is_enabled=None)
    assert out.is_enabled is False


def test_create_request_rejects_system_slug_prefix() -> None:
    with pytest.raises(ValidationError):
        SkillCreateRequest(
            name="Bad",
            slug="system_evil",
            description="d",
            body="# B\n\n## 何时使用\nx.\n",
        )


@pytest.mark.asyncio
async def test_update_skill_description_triggers_version_bump(monkeypatch: pytest.MonkeyPatch) -> None:
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    pid = uuid.uuid4()
    sid = uuid.uuid4()
    uid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    skill = Skill(
        id=sid,
        project_id=pid,
        name="x",
        slug="custom-a",
        description="old",
        body="# B\n\n## 何时使用\nu.\n",
        semantic_version="1.0.0",
        category="custom",
        tags=[],
        triggers=[],
        tools_required=[],
        activation_mode="agent_callable",
        extra_metadata={},
        attachments=[],
        safety_scan_status="clean",
        source="custom",
        is_enabled=True,
        db_version=1,
        created_by=uid,
        created_at=now,
        updated_at=now,
    )

    monkeypatch.setattr(
        SkillService,
        "_get_skill_in_project",
        staticmethod(AsyncMock(return_value=skill)),
    )

    scanner_inst = MagicMock()
    scanner_inst.scan.return_value = MagicMock(status="clean", findings=[])
    scanner_cls = MagicMock(return_value=scanner_inst)
    scanner_cls.VERSION = "1.0"
    monkeypatch.setattr("app.modules.skills.service.SafetyScanner", scanner_cls)

    data = SkillUpdateRequest(description="new description")
    out = await SkillService.update_skill(db, sid, data, _user(uid))
    assert out.description == "new description"
    assert skill.db_version == 2
