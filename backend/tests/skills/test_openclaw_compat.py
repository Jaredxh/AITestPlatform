"""Task 12.3 — OpenClaw 风格样本解析 + 导出 round-trip。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.modules.skills.exporter import build_skill_md, export_skill_as_zip
from app.modules.skills.importer import skill_version_directory, unpack_skill_zip
from app.modules.skills.models import Skill
from app.modules.skills.parser import parse_skill_md, strip_exported_use_when_suffix

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "openclaw_samples"


def test_openclaw_fixture_dirs_parse() -> None:
    dirs = sorted(d for d in FIXTURE_ROOT.iterdir() if d.is_dir())
    assert len(dirs) >= 5
    for d in dirs[:5]:
        md_path = d / "SKILL.md"
        assert md_path.is_file(), f"missing SKILL.md in {d}"
        parse_skill_md(md_path.read_text(encoding="utf-8"))


def test_export_then_unpack_equivalence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "app.config.settings.UPLOAD_DIR",
        str(tmp_path),
    )

    pid = uuid.uuid4()
    sid = uuid.uuid4()
    uid = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)

    skill = Skill(
        id=sid,
        project_id=pid,
        name="Roundtrip Skill",
        slug="roundtrip_skill",
        description="Original description without suffix.",
        semantic_version="1.4.0",
        category="custom",
        tags=["t1"],
        triggers=["alpha case", "beta case"],
        tools_required=["platform_search_testcases"],
        activation_mode="agent_callable",
        body="# Title\n\nSome **markdown**.\n",
        extra_metadata={
            "note": "keep-me",
            "slug": "roundtrip_skill",
            "version": "ignored-by-export-overlay",
        },
        attachments=[],
        source="custom",
        source_url=None,
        is_enabled=True,
        safety_scan_status="clean",
        safety_scan_notes=None,
        db_version=1,
        created_by=uid,
    )
    skill.created_at = now
    skill.updated_at = now

    root = skill_version_directory(pid, sid, 1)
    root.mkdir(parents=True)
    (root / "extra.txt").write_bytes(b"attachment-bytes")
    skill.attachments = [{"path": "extra.txt", "size": len(b"attachment-bytes")}]

    zbytes = export_skill_as_zip(skill)
    parsed, files = unpack_skill_zip(zbytes)

    assert parsed.slug == skill.slug
    assert parsed.name == skill.name
    assert strip_exported_use_when_suffix(parsed.description) == skill.description
    assert parsed.semantic_version == skill.semantic_version
    assert parsed.triggers == skill.triggers
    assert parsed.tools_required == skill.tools_required
    assert parsed.body.strip() == skill.body.strip()
    assert any(rel == "extra.txt" and data == b"attachment-bytes" for rel, data in files)


def test_build_skill_md_roundtrip_frontmatter() -> None:
    uid = uuid.uuid4()
    now = datetime.now(tz=timezone.utc)
    skill = Skill(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="FM",
        slug="fm_skill",
        description="Desc",
        semantic_version="1.0.0",
        category="c",
        tags=[],
        triggers=["z"],
        tools_required=[],
        activation_mode="agent_callable",
        body="Body",
        extra_metadata={"k_extra": 1},
        attachments=[],
        source="custom",
        source_url=None,
        is_enabled=True,
        safety_scan_status="clean",
        safety_scan_notes=None,
        db_version=1,
        created_by=uid,
    )
    skill.created_at = now
    skill.updated_at = now

    md = build_skill_md(skill)
    again = parse_skill_md(md)
    assert again.slug == skill.slug
    assert strip_exported_use_when_suffix(again.description) == skill.description
    assert again.metadata.get("k_extra") == 1
