"""Skill 附件目录的只读文件工具：``read_skill_file`` / ``list_skill_files``。

为什么需要：

很多 OpenClaw 形态技能包都会把"参考资料"放到 ``references/*.md``、把
"提示词模板"放到 ``prompts/*.md``、把"配置示例"放到 ``configs/*.yaml`` 等
非脚本目录里，SKILL.md 正文里只是**引用**它们（"详见 references/api_doc.md"）。
之前 LLM 没工具能读这些文件——它只能读到 SKILL.md 本体，剩下都是黑盒。

本模块提供两把"只读"工具：

- ``read_skill_file``：读取该技能附件目录里某个文本文件（256 KB 截断 + 二进
  制自动拒绝）。
- ``list_skill_files``：列出某个 skill 的附件目录树（深度 ≤5、entries ≤500）。

安全闸门复用 :class:`script_tools.SkillRoot` ContextVar——本轮可见的 skill 才
能读 / 列；slug 不在本轮就拒绝。路径反穿与脚本工具同一防线。

凭据敏感文件（``.env``、私钥、token JSON 等）默认走名称黑名单——admin 自己
导入的 skill 通常不会塞这种文件，但万一塞了，read 工具不会成为泄露渠道。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.modules.skills.script_tools import (
    SkillRoot,
    _normalize_relpath,
    _root_for_slug,
    get_active_skill_roots,
)

logger = logging.getLogger(__name__)


READ_FILE_TOOL_NAME = "read_skill_file"
LIST_FILES_TOOL_NAME = "list_skill_files"

MAX_READ_BYTES = 256 * 1024  # 与 stdout / http body 截断一致
LIST_MAX_ENTRIES = 500
LIST_MAX_DEPTH = 5
BINARY_PROBE_BYTES = 8192  # 前 8KB 看是否含 \0 → 视为二进制

# 文件名硬黑名单：再开放也不该让 LLM 直接 cat 出来。
#
# 原则："这种文件读出来 99% 概率是密钥 / 凭据 / token，跟 LLM 当前任务无关"。
# 命中即拒绝（不论是否真为敏感内容）。技能作者不该把这种东西塞进 ZIP，但万
# 一塞了，这层就是兜底防线，免得 admin 哪天后悔。
_BLOCKED_FILENAMES = frozenset(
    {
        ".env",
        ".envrc",
        "id_rsa",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
        "credentials.json",
        "secrets.json",
        "token.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
    },
)
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
_BLOCKED_NAME_PREFIXES = (".env.",)  # .env.local / .env.production 等


def _is_blocked_path(rel: str) -> bool:
    """是否命中"密钥 / 凭据"文件名黑名单。"""
    parts = rel.split("/")
    last = parts[-1] if parts else rel
    if last in _BLOCKED_FILENAMES:
        return True
    low = last.lower()
    if any(low.endswith(suf) for suf in _BLOCKED_SUFFIXES):
        return True
    if any(low.startswith(pre) for pre in _BLOCKED_NAME_PREFIXES):
        return True
    return False


def _looks_binary(probe: bytes) -> bool:
    """启发式：前 8KB 含 ``\\0`` 即视为二进制。

    UTF-16/32 文本理论上也含 ``\\0`` 但平台主要面向 utf-8 文档，几乎不出现。
    BOM (``\\ufeff``) 已在 utf-8 里是多字节序列，不会引入 ``\\0``，安全。
    """
    return b"\x00" in probe


def _resolve_under_root(
    *,
    skill_slug: str,
    relpath_raw: str,
    must_be: str,  # "file" / "dir"
) -> tuple[Path | None, Path | None, str | None]:
    """共享路径解析：返回 ``(abs_dir, abs_target, error_message)``。"""
    roots = get_active_skill_roots()
    if not roots:
        return None, None, (
            "skill filesystem tools are only available when at least one skill "
            "is activated this turn (always / manual / triggered / agent_callable)."
        )
    root: SkillRoot | None = _root_for_slug(skill_slug.strip(), roots)
    if root is None:
        avail = sorted({r.skill_slug for r in roots})
        return None, None, (
            f"skill {skill_slug!r} is not active this turn. "
            f"Available skills: {avail or '(none)'}."
        )
    abs_dir = Path(root.abs_dir).resolve()
    if not abs_dir.is_dir():
        return None, None, f"skill attachment dir not found on disk: {abs_dir}"

    rel = _normalize_relpath(relpath_raw) if relpath_raw else ""
    if relpath_raw and rel is None:
        return None, None, f"path {relpath_raw!r} rejected (absolute / contains '..' / empty)"
    target = (abs_dir / rel).resolve() if rel else abs_dir

    try:
        target.relative_to(abs_dir)
    except ValueError:
        return None, None, f"resolved path escapes skill attachment dir: {relpath_raw!r}"

    if must_be == "file":
        if not target.is_file():
            return None, None, f"file not found: {rel!r} (skill={skill_slug})"
        if _is_blocked_path(rel or ""):
            return None, None, (
                f"path {rel!r} is on the credentials blocklist "
                "(.env / *.pem / *.key / id_rsa / .netrc / .npmrc 等)；"
                "如确需让 AI 读取请改名到非敏感路径"
            )
    elif must_be == "dir" and not target.is_dir():
        return None, None, f"directory not found: {rel!r} (skill={skill_slug})"

    return abs_dir, target, None


# ── read_skill_file ────────────────────────────────────────────────


async def run_read_skill_file(args: dict[str, Any]) -> dict[str, Any]:
    skill_slug = args.get("skill_slug")
    path = args.get("path")
    if not isinstance(skill_slug, str) or not skill_slug.strip():
        return {"ok": False, "error": "missing skill_slug"}
    if not isinstance(path, str) or not path.strip():
        return {"ok": False, "error": "missing path"}

    _abs_dir, target, err = _resolve_under_root(
        skill_slug=skill_slug,
        relpath_raw=path,
        must_be="file",
    )
    if err is not None or target is None:
        return {"ok": False, "error": err or "unknown error"}

    try:
        size = target.stat().st_size
    except OSError as e:
        return {"ok": False, "error": f"stat failed: {e!s}"}

    truncated = False
    try:
        with target.open("rb") as fh:
            probe = fh.read(BINARY_PROBE_BYTES)
            if _looks_binary(probe):
                return {
                    "ok": False,
                    "error": (
                        f"file appears to be binary ({size} bytes); only UTF-8 text "
                        "files are supported by read_skill_file"
                    ),
                    "size_bytes": size,
                }
            data = probe + fh.read(MAX_READ_BYTES - len(probe) + 1)
    except OSError as e:
        return {"ok": False, "error": f"read failed: {e!s}"}

    if len(data) > MAX_READ_BYTES:
        data = data[:MAX_READ_BYTES]
        truncated = True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # 启发式 fallback：utf-8 + replace；不再尝试 latin-1，因为 utf-8
        # decode 失败大概率是真二进制（前 8KB 没看到 \0 但中段才有）
        text = data.decode("utf-8", errors="replace")

    logger.info(
        "read_skill_file: slug=%s path=%s bytes=%d truncated=%s",
        skill_slug, path, size, truncated,
    )
    return {
        "ok": True,
        "path": path,
        "content": text,
        "size_bytes": size,
        "truncated": truncated,
    }


def read_skill_file_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": READ_FILE_TOOL_NAME,
            "description": (
                "读取已激活技能附件目录里的某个 UTF-8 文本文件（如 references/api.md、"
                "prompts/template.md、configs/example.yaml 等）。\n\n"
                "**何时使用**：SKILL.md 正文里写了「详见 xxx 文件」"
                "「参考 references/foo.md」时，应当先读取该文件再行动；"
                "或者你需要查看某个脚本的源码 / 帮助说明再决定怎么调它的命令行参数。\n\n"
                "**约束**：仅 UTF-8 文本（二进制自动拒绝）；单文件 ≤256 KB（超出截断 + truncated=true）；"
                "路径必须在该 skill 附件目录之内（``..`` 与软链穿越拦截）；"
                "文件名 / 后缀命中凭据黑名单（.env / *.pem / *.key 等）的会被拒。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_slug": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "相对附件根目录的路径，如 references/api.md",
                    },
                },
                "required": ["skill_slug", "path"],
            },
        },
    }


# ── list_skill_files ───────────────────────────────────────────────


async def run_list_skill_files(args: dict[str, Any]) -> dict[str, Any]:
    skill_slug = args.get("skill_slug")
    subdir_raw = args.get("subdir") or ""
    if not isinstance(skill_slug, str) or not skill_slug.strip():
        return {"ok": False, "error": "missing skill_slug"}
    if subdir_raw is not None and not isinstance(subdir_raw, str):
        return {"ok": False, "error": "subdir must be a string"}

    abs_dir, target, err = _resolve_under_root(
        skill_slug=skill_slug,
        relpath_raw=subdir_raw,
        must_be="dir",
    )
    if err is not None or target is None or abs_dir is None:
        return {"ok": False, "error": err or "unknown error"}

    entries: list[dict[str, Any]] = []
    truncated = False
    base_depth = len(target.parts)
    try:
        for p in target.rglob("*"):
            depth = len(p.parts) - base_depth
            if depth > LIST_MAX_DEPTH:
                continue
            try:
                rel = str(p.relative_to(abs_dir)).replace("\\", "/")
            except ValueError:
                continue
            is_dir = p.is_dir()
            try:
                size = 0 if is_dir else p.stat().st_size
            except OSError:
                size = -1
            blocked = _is_blocked_path(rel) and not is_dir
            entries.append(
                {
                    "path": rel,
                    "size": size,
                    "is_dir": is_dir,
                    "blocked": blocked,
                },
            )
            if len(entries) >= LIST_MAX_ENTRIES:
                truncated = True
                break
    except OSError as e:
        return {"ok": False, "error": f"list failed: {e!s}"}

    entries.sort(key=lambda x: (not x["is_dir"], x["path"]))
    logger.info(
        "list_skill_files: slug=%s subdir=%s entries=%d truncated=%s",
        skill_slug, subdir_raw, len(entries), truncated,
    )
    return {
        "ok": True,
        "skill_slug": skill_slug,
        "subdir": subdir_raw or ".",
        "entries": entries,
        "truncated": truncated,
        "limits": {
            "max_entries": LIST_MAX_ENTRIES,
            "max_depth": LIST_MAX_DEPTH,
        },
    }


def list_skill_files_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": LIST_FILES_TOOL_NAME,
            "description": (
                "列出已激活技能附件目录下的文件树（含子目录），帮助你"
                "**发现可读 / 可执行的资源**。返回每个 entry 的 path、size、is_dir，"
                "以及是否命中凭据黑名单（blocked）。\n\n"
                "**何时使用**：你不知道某个 skill 还附带了哪些文件、scripts/ 下究竟有几个 .py、"
                "references/ 下文档结构如何时；这是探索 skill 包内部组成的入口。\n\n"
                "**约束**：最多 500 entries、最大递归深度 5；超过会带 truncated=true。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_slug": {"type": "string"},
                    "subdir": {
                        "type": "string",
                        "description": "可选，限定列出某个子目录（如 scripts）；默认列根",
                    },
                },
                "required": ["skill_slug"],
            },
        },
    }


# ── safe_invoke 派发桥 ─────────────────────────────────────────────


_FS_TOOL_RUNNERS = {
    READ_FILE_TOOL_NAME: run_read_skill_file,
    LIST_FILES_TOOL_NAME: run_list_skill_files,
}


def is_skill_fs_tool(name: str) -> bool:
    return name in _FS_TOOL_RUNNERS


async def run_skill_fs_tool(name: str, args_json: str) -> str:
    runner = _FS_TOOL_RUNNERS.get(name)
    if runner is None:
        return json.dumps({"ok": False, "error": f"unknown fs tool: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(args_json) if args_json else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    payload = await runner(args)
    return json.dumps(payload, ensure_ascii=False)


def skill_fs_tool_schemas() -> list[dict[str, Any]]:
    return [read_skill_file_schema(), list_skill_files_schema()]


__all__ = [
    "LIST_FILES_TOOL_NAME",
    "LIST_MAX_DEPTH",
    "LIST_MAX_ENTRIES",
    "MAX_READ_BYTES",
    "READ_FILE_TOOL_NAME",
    "is_skill_fs_tool",
    "list_skill_files_schema",
    "read_skill_file_schema",
    "run_list_skill_files",
    "run_read_skill_file",
    "run_skill_fs_tool",
    "skill_fs_tool_schemas",
]
