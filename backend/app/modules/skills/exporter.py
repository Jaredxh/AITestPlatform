"""Skill 导出 ZIP（OpenClaw / Claude Code 兼容）— Phase 12 Task 12.3。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import yaml

from app.modules.skills.importer import skill_version_directory
from app.modules.skills.models import Skill


def _description_for_export(skill: Skill) -> str:
    desc = (skill.description or "").rstrip()
    triggers = [str(t).strip() for t in (skill.triggers or []) if str(t).strip()]
    if triggers:
        tail = "; ".join(triggers)
        desc = desc + "\n\nUse when: " + tail
    return desc


def build_skill_md(skill: Skill) -> str:
    """生成完整 SKILL.md 文本（YAML + body），供导出或测试。"""
    meta = export_yaml_frontmatter(skill)
    dumped = yaml.safe_dump(
        meta,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    if not dumped.endswith("\n"):
        dumped += "\n"
    body = skill.body if isinstance(skill.body, str) else ""
    body_stripped = body.lstrip("\n")
    return f"---\n{dumped}---\n\n{body_stripped}"


def export_yaml_frontmatter(skill: Skill) -> dict:
    """合并 DB 权威字段与 ``extra_metadata``（后者可被顶层覆盖）。"""
    base = dict(skill.extra_metadata or {})
    base.update(
        {
            "name": skill.name,
            "description": _description_for_export(skill),
            "version": skill.semantic_version,
            "category": skill.category,
            "tags": list(skill.tags or []),
            "triggers": list(skill.triggers or []),
            "tools_required": list(skill.tools_required or []),
            "activation_mode": skill.activation_mode,
            "slug": skill.slug,
        },
    )
    return base


def _readme(skill: Skill) -> str:
    return (
        f"# {skill.name}\n\n"
        "Exported from **AITestPlatform** (Phase 12 skill pack).\n\n"
        f"- Slug: `{skill.slug}`\n"
        f"- Version: `{skill.semantic_version}`\n"
    )


def export_skill_as_zip(skill: Skill) -> bytes:
    """导出 OpenClaw 风格 ZIP：``{{slug}}/SKILL.md`` + README + 附件。"""
    buf = io.BytesIO()
    slug = skill.slug
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}/SKILL.md", build_skill_md(skill).encode("utf-8"))
        zf.writestr(f"{slug}/README.md", _readme(skill).encode("utf-8"))

        if skill.project_id is not None:
            root = skill_version_directory(skill.project_id, skill.id, skill.db_version)
            for att in skill.attachments or []:
                if not isinstance(att, dict):
                    continue
                rel = att.get("path")
                if not rel or not isinstance(rel, str):
                    continue
                safe = Path(rel)
                if safe.is_absolute() or ".." in safe.parts:
                    continue
                disk = root / safe
                arcname = f"{slug}/{rel.replace(chr(92), '/')}"
                if disk.is_file():
                    zf.writestr(arcname, disk.read_bytes())
                else:
                    zf.writestr(arcname, b"")

    return buf.getvalue()
