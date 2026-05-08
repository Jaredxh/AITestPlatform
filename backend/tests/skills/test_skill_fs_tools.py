"""Phase 13 — ``read_skill_file`` / ``list_skill_files`` 单元测试。

覆盖：
1. 读取技能附件目录里的 Markdown / JSON / 配置文件。
2. 二进制文件被识别并拒绝。
3. 路径反穿被拒。
4. skill_slug 不在本轮可见集合 → 拒绝。
5. ContextVar 为空 → 拒绝。
6. 凭据黑名单（``.env``、``*.pem``、``id_rsa`` 等）即使物理存在也拒绝。
7. ``list_skill_files`` 列出树、entries 上限截断、blocked 标记。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.skills import script_tools as st
from app.modules.skills import skill_fs_tools as fs


def _root(tmp_path: Path, slug: str = "demo") -> frozenset[st.SkillRoot]:
    return frozenset({st.SkillRoot(skill_slug=slug, abs_dir=str(tmp_path))})


# ── read_skill_file ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_text_file_success(tmp_path: Path) -> None:
    (tmp_path / "references").mkdir()
    target = tmp_path / "references" / "api.md"
    target.write_text("# API 文档\n\n* GET /foo\n", encoding="utf-8")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "path": "references/api.md"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is True
    assert "API 文档" in out["content"]
    assert out["truncated"] is False
    assert out["size_bytes"] > 0


@pytest.mark.asyncio
async def test_read_binary_rejected(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02\x03" * 100)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "path": "blob.bin"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is False
    assert "binary" in out["error"]


@pytest.mark.asyncio
async def test_read_path_traversal_rejected(tmp_path: Path) -> None:
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "path": "../etc/passwd"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is False
    assert "rejected" in out["error"] or "escape" in out["error"]


@pytest.mark.asyncio
async def test_read_unknown_skill_rejected(tmp_path: Path) -> None:
    (tmp_path / "x.md").write_text("hi")
    token = st.set_active_skill_roots(_root(tmp_path, slug="demo"))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "ghost", "path": "x.md"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is False
    assert "not active" in out["error"]


@pytest.mark.asyncio
async def test_read_no_active_roots() -> None:
    out = json.loads(await fs.run_skill_fs_tool(
        fs.READ_FILE_TOOL_NAME,
        json.dumps({"skill_slug": "demo", "path": "x.md"}),
    ))
    assert out["ok"] is False
    assert "only available" in out["error"]


@pytest.mark.parametrize("name", [".env", ".env.production", "id_rsa", "creds.pem", "secret.key"])
@pytest.mark.asyncio
async def test_read_credentials_blocklist(tmp_path: Path, name: str) -> None:
    """凭据类文件即使物理存在也拒绝（兜底防泄露）。"""
    target = tmp_path / name
    target.write_text("looks-like-a-secret", encoding="utf-8")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "path": name}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is False
    assert "blocklist" in out["error"]


@pytest.mark.asyncio
async def test_read_truncates_large_text(tmp_path: Path) -> None:
    big = tmp_path / "big.md"
    big.write_text("a" * (fs.MAX_READ_BYTES + 100), encoding="utf-8")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.READ_FILE_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "path": "big.md"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is True
    assert out["truncated"] is True
    assert len(out["content"]) <= fs.MAX_READ_BYTES + 32


# ── list_skill_files ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_basic(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("print(1)")
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "doc.md").write_text("# d")
    (tmp_path / "SKILL.md").write_text("# s")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.LIST_FILES_TOOL_NAME,
            json.dumps({"skill_slug": "demo"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is True
    paths = {e["path"] for e in out["entries"]}
    assert "scripts" in paths
    assert "scripts/a.py" in paths
    assert "references/doc.md" in paths
    assert "SKILL.md" in paths


@pytest.mark.asyncio
async def test_list_subdir(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("a")
    (tmp_path / "scripts" / "b.py").write_text("b")
    (tmp_path / "other.py").write_text("c")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.LIST_FILES_TOOL_NAME,
            json.dumps({"skill_slug": "demo", "subdir": "scripts"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is True
    paths = {e["path"] for e in out["entries"]}
    assert "scripts/a.py" in paths
    assert "scripts/b.py" in paths
    assert "other.py" not in paths  # 限定 subdir 时不该出现


@pytest.mark.asyncio
async def test_list_marks_blocked_entries(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("S=1")
    (tmp_path / "ok.md").write_text("ok")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.LIST_FILES_TOOL_NAME,
            json.dumps({"skill_slug": "demo"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    by_path = {e["path"]: e for e in out["entries"]}
    assert by_path[".env"]["blocked"] is True
    assert by_path["ok.md"]["blocked"] is False


@pytest.mark.asyncio
async def test_list_truncates_at_max_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs, "LIST_MAX_ENTRIES", 5)
    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out = json.loads(await fs.run_skill_fs_tool(
            fs.LIST_FILES_TOOL_NAME,
            json.dumps({"skill_slug": "demo"}),
        ))
    finally:
        st.reset_active_skill_roots(token)
    assert out["ok"] is True
    assert out["truncated"] is True
    assert len(out["entries"]) == 5


def test_fs_tool_schemas_shape() -> None:
    schemas = fs.skill_fs_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {fs.READ_FILE_TOOL_NAME, fs.LIST_FILES_TOOL_NAME}
