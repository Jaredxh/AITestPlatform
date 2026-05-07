"""Task 12.3 — SKILL.md 解析。"""

from __future__ import annotations

import pytest

from app.modules.skills.parser import (
    ParsedSkill,
    SkillParseError,
    parse_skill_md,
    slugify,
)


def test_parse_standard_skill_md() -> None:
    md = """---
name: Demo
description: Does something useful.
slug: demo-skill
version: 1.2.3
category: cat
tags: [a, b]
triggers: ["x"]
tools_required: ["platform_search_testcases"]
activation_mode: agent_callable
unknown_field: 42
---

# Hello

Body here.
"""
    p = parse_skill_md(md)
    assert isinstance(p, ParsedSkill)
    assert p.name == "Demo"
    assert p.slug == "demo-skill"
    assert p.semantic_version == "1.2.3"
    assert p.category == "cat"
    assert p.tags == ["a", "b"]
    assert p.triggers == ["x"]
    assert p.tools_required == ["platform_search_testcases"]
    assert p.activation_mode == "agent_callable"
    assert p.body.startswith("# Hello")
    assert p.metadata.get("unknown_field") == 42


def test_missing_name_raises() -> None:
    with pytest.raises(SkillParseError, match="name"):
        parse_skill_md("---\ndescription: x\n---\n")


def test_missing_description_raises() -> None:
    with pytest.raises(SkillParseError, match="description"):
        parse_skill_md("---\nname: x\n---\n")


def test_slug_auto_from_name() -> None:
    p = parse_skill_md(
        "---\nname: My Cool Skill\ndescription: d\n---\n",
    )
    assert p.slug == "my-cool-skill"


def test_slugify_ascii() -> None:
    assert slugify("Hello  World!!") == "hello-world"
