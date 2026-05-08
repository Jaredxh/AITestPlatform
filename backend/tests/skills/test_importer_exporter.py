"""Task 12.3 — ZIP unpack / 体积约束。"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.modules.skills.importer import (
    MAX_ATTACH_FILE_BYTES,
    MAX_ATTACHMENTS,
    ZIP_MAX_BYTES,
    unpack_skill_zip,
)
from app.modules.skills.parser import SkillParseError


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_unpack_nested_skill_md_and_attachment() -> None:
    md = b"""---
name: Zip Skill
description: From zip
slug: zip-skill
version: 1.0.0
category: custom
tags: []
triggers: []
tools_required: []
activation_mode: agent_callable
---

# Z

Hello.
"""
    raw = _zip_bytes(
        {
            "my-pack/SKILL.md": md,
            "my-pack/sub/note.txt": b"note",
        },
    )
    parsed, attachments = unpack_skill_zip(raw)
    assert parsed.slug == "zip-skill"
    assert len(attachments) == 1
    assert attachments[0][0].replace("\\", "/") == "sub/note.txt"


def test_rejects_oversized_zip() -> None:
    huge = b"x" * (ZIP_MAX_BYTES + 1)
    with pytest.raises(SkillParseError, match="exceeds"):
        unpack_skill_zip(huge)


def test_rejects_large_attachment() -> None:
    md = b"""---
name: Big
description: d
slug: big-skill
version: 1.0.0
category: custom
tags: []
triggers: []
tools_required: []
activation_mode: agent_callable
---

# B
"""
    big = _zip_bytes(
        {
            "SKILL.md": md,
            "big.bin": b"x" * (MAX_ATTACH_FILE_BYTES + 1),
        },
    )
    with pytest.raises(SkillParseError, match="exceeds"):
        unpack_skill_zip(big)


def test_rejects_too_many_attachments() -> None:
    md = b"""---
name: Many
description: d
slug: many-skill
version: 1.0.0
category: custom
tags: []
triggers: []
tools_required: []
activation_mode: agent_callable
---

# M
"""
    entries = {"SKILL.md": md}
    for i in range(MAX_ATTACHMENTS + 1):
        entries[f"f{i}.txt"] = b"a"
    raw = _zip_bytes(entries)
    with pytest.raises(SkillParseError, match="附件数量超过上限"):
        unpack_skill_zip(raw)


def test_ignores_dev_noise_files() -> None:
    """``.git/`` / ``__pycache__/`` / ``.DS_Store`` 等不算附件，不该撑爆上限。

    背景：用户常把整个 git 工作树打包上来，光 ``.git/`` 一个目录就 100+ 文件，
    历史实现会把它们都当真附件，瞬间触发 ``MAX_ATTACHMENTS`` 拒绝。修复后这些
    路径应当被静默过滤，仅 ``real.txt`` 这一个真正的附件被保留。
    """
    md = b"""---
name: Noise Skill
description: d
slug: noise-skill
version: 1.0.0
category: custom
tags: []
triggers: []
tools_required: []
activation_mode: agent_callable
---

# N
"""
    entries: dict[str, bytes] = {"pkg/SKILL.md": md, "pkg/real.txt": b"real"}
    for i in range(80):
        entries[f"pkg/.git/objects/aa/{i:02d}"] = b"obj"
    entries["pkg/scripts/__pycache__/x.cpython-310.pyc"] = b"x"
    entries["pkg/.DS_Store"] = b"ds"
    entries["pkg/node_modules/foo/index.js"] = b"js"
    entries["pkg/.vscode/settings.json"] = b"{}"
    entries["pkg/__MACOSX/._real.txt"] = b"resfork"

    raw = _zip_bytes(entries)
    parsed, attachments = unpack_skill_zip(raw)
    assert parsed.slug == "noise-skill"
    assert [rel for rel, _ in attachments] == ["real.txt"]
