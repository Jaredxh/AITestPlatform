"""Phase 13 — ``run_skill_script`` 单元测试（重构后版本）。

覆盖：
1. SKILL.md 正文解析 → ``AllowedScript`` 集合（仅作为 system prompt hint，
   不再是运行时闸门）。
2. 路径反穿与未在本轮可见 skill 拒绝。
3. 真实子进程执行（python3 / bash）成功路径。
4. stdout 截断（>256 KB）。
5. wall-clock 超时杀进程（用极小超时 + ``time.sleep`` 保持单测 1s 内结束）。
6. ``run_skill_script_tool`` 入参校验：缺字段 / 非法 interpreter / args 类型。
7. **新模型**：未在 SKILL.md 列出但物理存在于 skill 附件目录里的脚本应当**放行**
   （OpenClaw 信任模型）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.skills import script_tools as st


def test_extract_python_node_bash_scripts(tmp_path: Path) -> None:
    body = """
# 用户文档

请执行 `python scripts/dklive_diagnose.py -c <mid>`。
另外可以跑 `python3 scripts/check_user.py 1234`。

Node 入口：```bash
node scripts/get-token.js --system
```

也可以执行 ``bash setup.sh`` 做初始化。

不该匹配：``node setup.py``（扩展名不一致）、``ipython foo.py``（词前界）。
绝对路径也不该匹配：``python /etc/passwd.py``。
"""
    scripts = st.extract_allowed_scripts_from_body(
        body,
        skill_slug="cq-app-dklive",
        abs_dir=tmp_path,
    )
    rels = sorted({(s.interpreter, s.relpath) for s in scripts})
    assert rels == [
        ("bash", "setup.sh"),
        ("node", "scripts/get-token.js"),
        ("python3", "scripts/check_user.py"),
        ("python3", "scripts/dklive_diagnose.py"),
    ]
    for s in scripts:
        assert s.skill_slug == "cq-app-dklive"
        assert s.abs_dir == str(tmp_path)


def test_extract_rejects_traversal_and_absolute(tmp_path: Path) -> None:
    body = """
``python ../escape.py`` 不应被接受；
``python /etc/passwd.py`` 也不应；
``python ~/foo.py`` 也不应；
合法的：``python scripts/ok.py``。
"""
    scripts = st.extract_allowed_scripts_from_body(
        body, skill_slug="x", abs_dir=tmp_path,
    )
    assert {s.relpath for s in scripts} == {"scripts/ok.py"}


def test_normalize_relpath_edge_cases() -> None:
    assert st._normalize_relpath("scripts/a.py") == "scripts/a.py"
    assert st._normalize_relpath("./scripts//a.py") == "scripts/a.py"
    assert st._normalize_relpath("/etc/x.py") is None
    assert st._normalize_relpath("../x.py") is None
    assert st._normalize_relpath("a/../b.py") is None
    assert st._normalize_relpath("~/x.py") is None
    assert st._normalize_relpath("") is None
    assert st._normalize_relpath("C:/x.py") is None


def _root(tmp_path: Path, slug: str = "demo") -> frozenset[st.SkillRoot]:
    return frozenset({st.SkillRoot(skill_slug=slug, abs_dir=str(tmp_path))})


@pytest.mark.asyncio
async def test_run_skill_script_python_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实派发 python3 hello.py，断言 stdout / exit_code."""
    script = tmp_path / "hello.py"
    script.write_text(
        "import sys\nprint('hi', sys.argv[1])\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "python3",
                "script": "hello.py",
                "args": ["world"],
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert "hi world" in payload["stdout"]
    assert payload["stdout_truncated"] is False


@pytest.mark.asyncio
async def test_run_skill_script_bash_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """bash setup.sh 也能跑（新增的解释器）。"""
    script = tmp_path / "hello.sh"
    script.write_text("#!/usr/bin/env bash\necho hi-from-bash $1\n", encoding="utf-8")
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "bash",
                "script": "hello.sh",
                "args": ["world"],
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert "hi-from-bash world" in payload["stdout"]


@pytest.mark.asyncio
async def test_run_skill_script_unlisted_but_under_root_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新模型：SKILL.md 没声明但物理存在于附件目录的脚本应当放行（OpenClaw 信任）。"""
    script = tmp_path / "scripts" / "diag.py"
    script.parent.mkdir()
    script.write_text("print('ok')", encoding="utf-8")
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "python3",
                "script": "scripts/diag.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is True
    assert payload["stdout"].strip() == "ok"


@pytest.mark.asyncio
async def test_run_skill_script_path_traversal_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``..`` 路径反穿无论是否物理存在都拒绝。"""
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "python3",
                "script": "../escape.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is False
    assert "rejected" in payload["error"] or "escape" in payload["error"]


@pytest.mark.asyncio
async def test_run_skill_script_unknown_skill_slug_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """skill_slug 不在本轮可见集合 → 拒绝。"""
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path, slug="demo"))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "other-skill",
                "interpreter": "python3",
                "script": "x.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is False
    assert "not active" in payload["error"]


@pytest.mark.asyncio
async def test_run_skill_script_no_active_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """ContextVar 为空 → 直接拒绝，不会去 spawn 进程。"""
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    out_json = await st.run_script_tool(
        st.SCRIPT_TOOL_NAME,
        json.dumps({
            "skill_slug": "demo",
            "interpreter": "python3",
            "script": "hello.py",
        }),
    )
    payload = json.loads(out_json)
    assert payload["ok"] is False
    assert "only available" in payload["error"]


@pytest.mark.asyncio
async def test_run_skill_script_stdout_truncation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stdout 大于 256 KB 时返回 truncated=True 且 len ≤ MAX_STREAM_BYTES。"""
    script = tmp_path / "spam.py"
    script.write_text(
        "import sys\nsys.stdout.write('x' * 400_000)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "python3",
                "script": "spam.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is True
    assert payload["stdout_truncated"] is True
    assert len(payload["stdout"]) <= st.MAX_STREAM_BYTES + 32


@pytest.mark.asyncio
async def test_run_skill_script_timeout_kill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 wall-clock 一定 ``proc.kill``，返回 ok=False + 提示非交互参数。"""
    script = tmp_path / "loop.py"
    script.write_text(
        "import time\nprint('start', flush=True)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    monkeypatch.setattr(st, "WALL_CLOCK_TIMEOUT_SECONDS", 1.0)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "python3",
                "script": "loop.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is False
    assert "wall-clock timeout" in payload["error"]
    assert payload["exit_code"] != 0


@pytest.mark.asyncio
async def test_run_skill_script_arg_validation() -> None:
    """非法入参 → 直接拒绝，不会进入 ContextVar 检查路径。"""
    out = json.loads(await st.run_script_tool(st.SCRIPT_TOOL_NAME, json.dumps({
        "interpreter": "python3", "script": "x.py",
    })))
    assert out["ok"] is False and "skill_slug" in out["error"]

    out = json.loads(await st.run_script_tool(st.SCRIPT_TOOL_NAME, json.dumps({
        "skill_slug": "x", "interpreter": "ruby", "script": "x.py",
    })))
    assert out["ok"] is False
    assert "ruby" in out["error"] or "only available" in out["error"]

    out = json.loads(await st.run_script_tool(st.SCRIPT_TOOL_NAME, json.dumps({
        "skill_slug": "x", "interpreter": "python3", "script": "x.py", "args": "oops",
    })))
    assert out["ok"] is False and "args" in out["error"]


@pytest.mark.asyncio
async def test_run_skill_script_extension_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``node setup.py`` 这种诡异调用应被扩展名校验拦掉。"""
    bad = tmp_path / "setup.py"
    bad.write_text("# python file", encoding="utf-8")
    monkeypatch.setattr(st, "_apply_rlimits", lambda: None)
    token = st.set_active_skill_roots(_root(tmp_path))
    try:
        out_json = await st.run_script_tool(
            st.SCRIPT_TOOL_NAME,
            json.dumps({
                "skill_slug": "demo",
                "interpreter": "node",
                "script": "setup.py",
            }),
        )
    finally:
        st.reset_active_skill_roots(token)
    payload = json.loads(out_json)
    assert payload["ok"] is False
    assert "extension" in payload["error"]


def test_script_tool_schema_shape() -> None:
    spec = st.script_tool_schema()
    assert spec["type"] == "function"
    fn = spec["function"]
    assert fn["name"] == st.SCRIPT_TOOL_NAME
    params = fn["parameters"]
    assert params["required"] == ["skill_slug", "interpreter", "script"]
    enums = params["properties"]["interpreter"]["enum"]
    for x in ("python3", "node", "bash", "sh"):
        assert x in enums
