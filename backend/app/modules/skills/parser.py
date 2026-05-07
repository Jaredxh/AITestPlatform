"""SKILL.md 解析（python-frontmatter，OpenClaw 兼容）— Phase 12 Task 12.3。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import frontmatter

from app.modules.skills.schemas import ACTIVATION_MODES

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")


class SkillParseError(ValueError):
    """SKILL.md 缺失必填字段或格式非法。"""


def slugify(name: str) -> str:
    """由展示名生成 URL/slug 安全串（与 ``SkillCreateRequest`` pattern 对齐）。"""
    ascii_name = (
        unicodedata.normalize("NFKD", name or "")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    s = ascii_name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "skill"


def _ensure_slug(raw: str | None, name: str) -> str:
    slug = (raw or "").strip() if raw else slugify(name)
    if not _SLUG_RE.fullmatch(slug):
        slug = slugify(name)
    if not _SLUG_RE.fullmatch(slug):
        raise SkillParseError(f"invalid slug after normalization: {slug!r}")
    return slug


def _coerce_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    return []


def _coerce_str(val: Any, default: str | None = None) -> str | None:
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


@dataclass
class ParsedSkill:
    """解析结果：已知字段 + 完整 YAML dict（写入 ``Skill.extra_metadata``）。"""

    name: str
    description: str
    slug: str
    semantic_version: str
    category: str
    tags: list[str]
    triggers: list[str]
    tools_required: list[str]
    activation_mode: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_skill_md(content: str) -> ParsedSkill:
    """解析标准 OpenClaw SKILL.md（YAML 前言 + Markdown 正文）。"""
    post = frontmatter.loads(content or "")
    fm_raw = post.metadata
    if fm_raw is None:
        fm: dict[str, Any] = {}
    elif isinstance(fm_raw, dict):
        fm = dict(fm_raw)
    else:
        raise SkillParseError("SKILL.md: YAML front matter must be a mapping")

    body = post.content if isinstance(post.content, str) else ""

    name = _coerce_str(fm.get("name"))
    if not name:
        raise SkillParseError("SKILL.md: missing required field 'name'")

    description = _coerce_str(fm.get("description"))
    if not description:
        raise SkillParseError("SKILL.md: missing required field 'description'")

    slug = _ensure_slug(_coerce_str(fm.get("slug")), name)

    semantic_version = (
        _coerce_str(fm.get("version"))
        or _coerce_str(fm.get("semantic_version"))
        or "1.0.0"
    )

    category = _coerce_str(fm.get("category")) or "custom"

    tags = _coerce_str_list(fm.get("tags"))
    triggers = _coerce_str_list(fm.get("triggers"))
    tools_required = _coerce_str_list(fm.get("tools_required"))

    activation_mode = _coerce_str(fm.get("activation_mode")) or "agent_callable"
    if activation_mode not in ACTIVATION_MODES:
        raise SkillParseError(
            f"SKILL.md: invalid activation_mode {activation_mode!r}; "
            f"must be one of {ACTIVATION_MODES}",
        )

    metadata = {str(k): _jsonify_yaml_value(v) for k, v in fm.items()}

    return ParsedSkill(
        name=name,
        description=description,
        slug=slug,
        semantic_version=semantic_version,
        category=category,
        tags=tags,
        triggers=triggers,
        tools_required=tools_required,
        activation_mode=activation_mode,
        body=body.lstrip("\n"),
        metadata=metadata,
    )


def _jsonify_yaml_value(val: Any) -> Any:
    """尽量得到 JSON 兼容结构（metadata 落 PG JSONB）。"""
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, dict):
        return {str(k): _jsonify_yaml_value(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_jsonify_yaml_value(v) for v in val]
    return str(val)


def strip_exported_use_when_suffix(description: str) -> str:
    """导出时在 description 末尾追加的 ``Use when:`` 便于 round-trip 对比。"""
    marker = "\n\nUse when:"
    idx = description.rfind(marker)
    if idx == -1:
        return description
    return description[:idx].rstrip()
