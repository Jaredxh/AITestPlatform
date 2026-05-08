"""Skill 附件脚本执行工具（``run_skill_script``）—— Phase 13 / Task 13.x。

OpenClaw 形态的技能包很常见这样写：

    使用 ``python scripts/dklive_diagnose.py -c <mid>`` 检查用户数据。

之前缺一座桥：LLM 看完 SKILL.md 知道"应该跑这条命令"但没工具能跑，只能尝试
凭脑补改 ``http_get_json``——脚本里通常还有 token / 签名 / 内部 host 等 LLM 复
刻不出来的细节，于是就出现"深度思考三万字然后一直在找 API"的卡壳现象。

本模块补上这把"安全的 subprocess 桥"，安全模型 = "import + enable = 信任"
（与 OpenClaw 一致）：

1. **本轮可见性闸门（SkillRoot）**：``skill_slug`` 必须在本轮 candidate skill
   集合里——既包含 always / manual / triggered 已激活的，也包含
   agent_callable 候选池中的（毕竟 LLM 调 ``skill_*__invoke`` 之前要先看到
   工具入口）。**不在本轮的 skill 永远拒绝。**
2. **路径闸门**：脚本物理路径 ``Path.resolve()`` 后必须仍在该 skill 的附件
   目录之下（``relative_to`` 兜底防 ``..`` / 软链穿越）。
3. **扩展名 ↔ 解释器**：``.py`` ↔ ``python3``、``.js/.mjs/.cjs`` ↔ ``node``、
   ``.sh`` ↔ ``bash`` / ``sh``。错配直接拒（防"node setup.py"这种诡异调用）。
4. **资源闸**：CPU 30s、地址空间 ≤512 MB、输出文件 ≤50 MB；wall-clock ≤35s
   兜底 ``asyncio.wait_for`` + ``proc.kill``；stdout/stderr 各按 256 KB 截断。
5. **环境隔离**：``env`` 仅放行 PATH/HOME/USER/LANG/LC_ALL/TZ + UTF-8 / unbuffered
   /etc.；不暴露其它任意进程环境变量。
6. **输出结构化**：返回 ``{ok, exit_code, stdout, stderr, duration_ms,
   stdout_truncated, stderr_truncated}`` JSON，便于 LLM 直接消费。

**与上一版的关键差异**：

之前要求"脚本相对路径必须在 SKILL.md 正文里以 ``python <path>`` 形式明文出现
过"，这是 prompt-injection 防御。但用户反馈："我安装并启用了的 skill 应该
直接可用，不需要再要求文档照着固定格式写"——这是 OpenClaw 的核心信任模型。
所以现在：

- ``extract_allowed_scripts_from_body`` 仍然存在，但其结果**只是 system prompt
  里给 LLM 看的"建议入口"hint**（让它知道哪些是技能作者点名的常用入口）；
- 真正的运行时闸门换成 :class:`SkillRoot`，只校验"slug 是不是本轮可见 + 文件
  在不在该 skill 的附件目录内 + 扩展名是否合法"。

这样：

- 已导入并启用的 skill 下任意 ``scripts/*.py`` 都能直接被 agent 调用，
  不需要"作者必须在 SKILL.md 列出每一条命令"；
- 同时**未导入 / 已禁用 / 未在本轮激活**的 skill 仍然完全拒绝——这是平台的
  最低防线，不会因为追求易用而打开。
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


SCRIPT_TOOL_NAME = "run_skill_script"

# 资源闸：默认值在 ``app.config.Settings``，env 可覆盖。
#
# 这些值放成"模块级变量 + 单测可 monkeypatch"以保留过去测试形态，并通过
# ``_load_resource_limits`` 在运行时回读 settings——这样运维改 .env 之后
# ``./run.sh redeploy backend`` 立即生效，无需改代码。
#
# 历史：上一版 RLIMIT_AS=512MB / NODE old-space=384MB / wall-clock=35s 在
# ``cq-app-dklive`` 触发 ``npx clawhub install`` 时 V8 反复 OOM。新默认能撑过
# ``npm/npx install`` 这种 OpenClaw 风格的"自启动安装"流程。
WALL_CLOCK_TIMEOUT_SECONDS: float = float(settings.SKILL_SCRIPT_TIMEOUT_S)
RLIMIT_CPU_SECONDS: int = settings.SKILL_SCRIPT_RLIMIT_CPU_S
RLIMIT_AS_BYTES: int = settings.SKILL_SCRIPT_RLIMIT_AS_MB * 1024 * 1024
RLIMIT_FSIZE_BYTES: int = settings.SKILL_SCRIPT_RLIMIT_FSIZE_MB * 1024 * 1024
NODE_MAX_OLD_SPACE_MB: int = settings.SKILL_SCRIPT_NODE_MAX_OLD_SPACE_MB
MAX_STREAM_BYTES = 256 * 1024  # stdout/stderr 各 256 KB 截断（与 http body 对齐）

# 解释器白名单：interpreter_arg → 允许的脚本扩展名集合。
#
# - ``python3`` 只跑 ``.py``——避免 ``python data.txt`` 这种把数据文件当脚本跑
# - ``node`` 跑 ``.js/.mjs/.cjs``——ES module / CommonJS 都覆盖
# - ``bash`` / ``sh`` 跑 ``.sh``——很多 OpenClaw 包里 ``bash setup.sh`` /
#   ``bash rotate-token.sh`` 这种"非交互运维入口"
#
# 不放行 ``ruby`` / ``perl`` / ``php`` 等不常见解释器——技能生态目前以 Python
# Node 为主，剩下两个引入的运行时占用比覆盖率不划算；后续真有需求再加。
_ALLOWED_INTERPRETERS: dict[str, frozenset[str]] = {
    "python3": frozenset({".py"}),
    "node": frozenset({".js", ".mjs", ".cjs"}),
    "bash": frozenset({".sh", ".bash"}),
    "sh": frozenset({".sh"}),
}

# SKILL.md 抽取脚本调用的正则——结果**仅作为 system prompt 提示**，不再用作
# 运行时闸门。
#
# 思路：与 http_tools 抽 URL 一致的"宁松不严，事后归一化"。匹配
#   - python xxx.py / python3 xxx.py
#   - node xxx.js / node xxx.mjs / node xxx.cjs
#   - bash xxx.sh / sh xxx.sh
# 允许的路径字符：字母/数字 + ``._/-``；这覆盖绝大多数 ``scripts/foo.py`` 类
# 写法，又不会贪心吞掉后续句子里的标点。引号包裹（"path/x.py"）是常见 Markdown
# 写法，也接受。
_SCRIPT_CALL_RE = re.compile(
    r"""
    (?<![A-Za-z0-9_-])         # 词前界——避免 ipython / subnode 这类被误抓
    (?P<interpreter>python3?|node|bash|sh)
    \s+
    (?:["']?)                  # 路径可能被引号包裹
    (?P<path>[A-Za-z0-9_./-]+\.(?:py|js|mjs|cjs|sh|bash))
    (?:["']?)
    """,
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class SkillRoot:
    """本轮 LLM 可访问的某个 skill 的"附件根目录"声明。

    用 frozen dataclass 既保 hashable（可塞 frozenset），又保字段不可变——
    避免 chat 流水线某一处误改后污染其他 turn。

    ``abs_dir`` 是该 skill 当前 ``db_version`` 的附件根目录（绝对路径字符串）。

    一个 skill 在本轮上下文里只会出现一条 ``SkillRoot``——同一 slug 跨 layer
    重复激活时，``frozenset`` 自动去重。
    """

    skill_slug: str
    abs_dir: str  # 已 ``str(Path)``


@dataclass(frozen=True, slots=True)
class AllowedScript:
    """**仅 system prompt 提示用**：SKILL.md 正文里点名的"常用脚本入口"。

    历史上这个字段是运行时闸门。重构后改为"hint"——只用来在 system 提示里
    告诉 LLM"这个 skill 的作者推荐你优先调这些入口"，并不限制 LLM 调其它
    同 skill 下的脚本（运行时闸门是 :class:`SkillRoot`）。
    """

    skill_slug: str
    interpreter: str  # "python3" / "node" / "bash" / "sh"
    relpath: str
    abs_dir: str


def _normalize_relpath(raw: str) -> str | None:
    """把抓到的相对路径归一化；非法路径返回 None。

    规则：
    - 拒绝绝对路径（``/x``、Windows ``C:\\``）
    - 拒绝 ``~``（home 跳转）
    - 折叠 ``./`` 与多余 ``/``
    - 拆段后任意一段是 ``..`` 或空字符串 → 拒绝
    - 结果至少含一段
    """
    if not raw:
        return None
    s = raw.strip().replace("\\", "/")
    if s.startswith(("/", "~")) or (len(s) >= 2 and s[1] == ":"):
        return None
    parts: list[str] = []
    for seg in s.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            return None
        parts.append(seg)
    if not parts:
        return None
    return "/".join(parts)


def _interpreter_for_extension(ext: str) -> str | None:
    """根据扩展名反推默认解释器；未知返回 None。"""
    ext = ext.lower()
    for interp, exts in _ALLOWED_INTERPRETERS.items():
        if ext in exts:
            return interp
    return None


def extract_allowed_scripts_from_body(
    body: str,
    *,
    skill_slug: str,
    abs_dir: Path,
) -> set[AllowedScript]:
    """从 SKILL.md 正文抽取"作者推荐入口"列表（用于 system prompt hint）。

    ``abs_dir`` 是该 skill 当前 db_version 附件目录的绝对路径，会被原样塞入
    ``AllowedScript.abs_dir``。

    返回 set 而非 list：去重 + 与 frozenset 适配。
    """
    if not body:
        return set()
    out: set[AllowedScript] = set()
    abs_dir_str = str(abs_dir)
    for m in _SCRIPT_CALL_RE.finditer(body):
        interp = m.group("interpreter")
        path_raw = m.group("path")
        # ``python`` / ``python3`` 都归一化到 ``python3`` 子进程入口（python2 早 EOL）
        if interp.startswith("python"):
            interp_norm = "python3"
        else:
            interp_norm = interp  # node / bash / sh 原样
        rel = _normalize_relpath(path_raw)
        if rel is None:
            continue
        ext = "." + rel.rsplit(".", 1)[-1].lower() if "." in rel else ""
        # 二次校验：扩展名必须与解释器一致；不一致大概率是文档里随手举例
        # （如 ``node setup.py``），不能放进 hint 列表。
        if ext not in _ALLOWED_INTERPRETERS.get(interp_norm, frozenset()):
            continue
        out.add(
            AllowedScript(
                skill_slug=skill_slug,
                interpreter=interp_norm,
                relpath=rel,
                abs_dir=abs_dir_str,
            ),
        )
    return out


# ── ContextVar：当前 turn 内 LLM 可访问的 skill 附件根目录集合 ──────
#
# 取代旧版"细粒度脚本路径白名单"。"OpenClaw 信任模型"：本轮可见的 skill →
# 它的整个 attach 目录里所有"可执行扩展名"文件都被允许；不在 skill 目录内 /
# 不在本轮可见集合里 → 永远拒绝。

_active_skill_roots: contextvars.ContextVar[frozenset[SkillRoot]] = contextvars.ContextVar(
    "skill_roots",
    default=frozenset(),
)


def set_active_skill_roots(roots: frozenset[SkillRoot]) -> contextvars.Token:
    return _active_skill_roots.set(roots)


def reset_active_skill_roots(token: contextvars.Token) -> None:
    _active_skill_roots.reset(token)


def get_active_skill_roots() -> frozenset[SkillRoot]:
    return _active_skill_roots.get()


def _root_for_slug(slug: str, roots: frozenset[SkillRoot]) -> SkillRoot | None:
    for r in roots:
        if r.skill_slug == slug:
            return r
    return None


# ── 安全检查：参数 → 解析为可执行路径 ─────────────────────────────


@dataclass
class _ResolveResult:
    ok: bool
    error: str | None = None
    interpreter: str | None = None
    abs_script: Path | None = None
    abs_dir: Path | None = None


def _resolve_script_call(
    *,
    skill_slug: str,
    interpreter: str,
    relpath_raw: str,
    roots: frozenset[SkillRoot],
) -> _ResolveResult:
    """把 LLM 入参解析成"可派发的子进程参数"，不通过任何一道闸都返回 error。"""
    if interpreter not in _ALLOWED_INTERPRETERS:
        return _ResolveResult(
            False,
            f"interpreter {interpreter!r} not allowed; choose one of "
            f"{sorted(_ALLOWED_INTERPRETERS)}",
        )
    rel = _normalize_relpath(relpath_raw)
    if rel is None:
        return _ResolveResult(
            False,
            f"script path {relpath_raw!r} rejected (absolute / contains '..' / empty)",
        )
    root = _root_for_slug(skill_slug.strip(), roots)
    if root is None:
        avail = sorted({r.skill_slug for r in roots})
        return _ResolveResult(
            False,
            (
                f"skill {skill_slug!r} is not active this turn. "
                f"Available skills: {avail or '(none)'}."
            ),
        )
    abs_dir = Path(root.abs_dir).resolve()
    candidate = (abs_dir / rel).resolve()
    # 关键防线：``resolve()`` 后再确认仍在 abs_dir 之内（即使 _normalize_relpath
    # 通过了归一化，软链穿透仍可能让 candidate 跑到 abs_dir 之外）。
    try:
        candidate.relative_to(abs_dir)
    except ValueError:
        return _ResolveResult(
            False,
            f"resolved script path escapes skill attachment dir: {rel!r}",
        )
    ext = candidate.suffix.lower()
    if ext not in _ALLOWED_INTERPRETERS[interpreter]:
        return _ResolveResult(
            False,
            (
                f"extension {ext!r} does not match interpreter {interpreter!r} "
                f"(expected one of {sorted(_ALLOWED_INTERPRETERS[interpreter])})"
            ),
        )
    if not candidate.is_file():
        return _ResolveResult(
            False,
            f"script not found in skill attachment dir: {rel!r} (skill={skill_slug})",
        )
    return _ResolveResult(
        True,
        None,
        interpreter=interpreter,
        abs_script=candidate,
        abs_dir=abs_dir,
    )


# ── 子进程执行 ─────────────────────────────────────────────────────


def _build_subprocess_env() -> dict[str, str]:
    """白名单 env，避免向脚本暴露 fastapi 进程的全部环境变量。

    保留：
    - ``PATH`` 用于 ``shutil.which`` 找解释器（已经预查过，但脚本内部可能还有 ``subprocess``）
    - ``HOME`` 让脚本能读 ``~/.cqclaw/tokens/...`` 这类 dklive 风格的本地凭据
      （admin 已经在容器里布好该文件——本平台不替它存）
    - ``LANG`` / ``LC_ALL`` / ``TZ`` 防止中文 print 乱码、时间区不一致
    强制：
    - ``PYTHONIOENCODING=utf-8`` —— Python 默认 utf-8 但兜底
    - ``PYTHONUNBUFFERED=1`` —— stdout 立即可见，超时也能拿到部分输出
    - ``NODE_OPTIONS=--max-old-space-size=<settings>`` —— Node V8 堆上限

    注意：``NODE_OPTIONS`` 故意不再压到 384 MB——上一版那个太紧的值导致
    ``npx clawhub install`` 等"OpenClaw 自动安装"路径反复 V8 OOM
    （``Fatal process out of memory: Zone``）。当前默认 1024 MB 与
    ``RLIMIT_AS=2 GB`` 配合使用，留出 1 GB 给 native heap / mmap 等。
    """
    keep = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "LANGUAGE", "TZ"}
    env = {k: v for k, v in os.environ.items() if k in keep and v is not None}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["NODE_OPTIONS"] = f"--max-old-space-size={NODE_MAX_OLD_SPACE_MB}"
    return env


def _apply_rlimits() -> None:  # pragma: no cover - 子进程内执行，主进程测不到
    """子进程 ``preexec_fn``：CPU/内存/写文件硬限。

    单测里我们 monkeypatch 掉它，避免 rlimit 在 macOS Python rosetta 等环境
    意外失败而干扰测试断言。容器内（Linux glibc）这些都正常生效。
    """
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (RLIMIT_CPU_SECONDS, RLIMIT_CPU_SECONDS))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))
    except (ValueError, OSError):
        pass


def _truncate(b: bytes) -> tuple[str, bool]:
    """二进制 → utf-8 字符串，按 ``MAX_STREAM_BYTES`` 截断。"""
    truncated = False
    if len(b) > MAX_STREAM_BYTES:
        b = b[:MAX_STREAM_BYTES]
        truncated = True
    try:
        text = b.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = b.decode("latin-1", errors="replace")
    return text, truncated


async def _run_subprocess(
    *,
    interpreter: str,
    abs_script: Path,
    abs_dir: Path,
    args: list[str],
    stdin_text: str | None,
) -> dict[str, Any]:
    """实际派发子进程。``abs_script`` 与 ``abs_dir`` 都已校验完毕。"""
    interp_path = shutil.which(interpreter)
    if not interp_path:
        return {"ok": False, "error": f"interpreter {interpreter!r} not found in container PATH"}

    env = _build_subprocess_env()
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            interp_path,
            str(abs_script),
            *args,
            cwd=str(abs_dir),
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=_apply_rlimits,
        )
    except OSError as e:
        return {"ok": False, "error": f"failed to spawn subprocess: {e!s}"}

    timed_out = False
    stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=WALL_CLOCK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        # 把已经累积的 stdout/stderr 拿回来，方便 LLM 看进度
        try:
            stdout, stderr = await proc.communicate()
        except Exception:  # noqa: BLE001
            stdout, stderr = b"", b""

    duration_ms = int((time.monotonic() - started) * 1000)
    out_text, out_trunc = _truncate(stdout or b"")
    err_text, err_trunc = _truncate(stderr or b"")
    exit_code = proc.returncode if proc.returncode is not None else -1

    payload: dict[str, Any] = {
        "ok": (not timed_out) and exit_code == 0,
        "exit_code": exit_code,
        "stdout": out_text,
        "stderr": err_text,
        "duration_ms": duration_ms,
        "stdout_truncated": out_trunc,
        "stderr_truncated": err_trunc,
    }
    if timed_out:
        payload["error"] = (
            f"script killed after {WALL_CLOCK_TIMEOUT_SECONDS:.0f}s wall-clock timeout. "
            "可能原因：① 脚本依赖 stdin 交互 → 改用非交互参数（-c / --check）；"
            "② 脚本里嵌套调 ``npx install`` / ``pip install`` 等长操作 → "
            "应当让管理员在平台**预先安装**依赖技能，而不是让脚本运行时联网安装；"
            "③ 真的需要更长执行时间 → 调大 ``SKILL_SCRIPT_TIMEOUT_S`` 环境变量。"
        )
    return payload


# ── safe_invoke 派发入口 ────────────────────────────────────────────


async def run_skill_script_tool(args: dict[str, Any]) -> dict[str, Any]:
    """``safe_invoke`` 派发入口；失败统一返回 ``{ok: false, error: ...}``。"""
    skill_slug = args.get("skill_slug")
    interpreter = args.get("interpreter")
    script = args.get("script")
    if not isinstance(skill_slug, str) or not skill_slug.strip():
        return {"ok": False, "error": "missing skill_slug"}
    if not isinstance(interpreter, str):
        return {"ok": False, "error": "missing interpreter (python3 / node / bash / sh)"}
    if not isinstance(script, str) or not script.strip():
        return {"ok": False, "error": "missing script (relative path)"}

    raw_args = args.get("args", [])
    if raw_args is None:
        raw_args = []
    if not isinstance(raw_args, list) or not all(isinstance(x, (str, int, float)) for x in raw_args):
        return {"ok": False, "error": "args must be a list of strings"}
    cli_args = [str(x) for x in raw_args]

    stdin_text = args.get("stdin")
    if stdin_text is not None and not isinstance(stdin_text, str):
        return {"ok": False, "error": "stdin must be a string when provided"}

    roots = get_active_skill_roots()
    if not roots:
        return {
            "ok": False,
            "error": (
                "run_skill_script is only available when at least one skill is "
                "activated this turn (always / manual / triggered / agent_callable)."
            ),
        }

    resolved = _resolve_script_call(
        skill_slug=skill_slug.strip(),
        interpreter=interpreter.strip(),
        relpath_raw=script.strip(),
        roots=roots,
    )
    if (
        not resolved.ok
        or resolved.abs_script is None
        or resolved.abs_dir is None
        or resolved.interpreter is None
    ):
        return {"ok": False, "error": resolved.error or "script not allowed"}

    logger.info(
        "run_skill_script: slug=%s interp=%s script=%s args=%d stdin=%s",
        skill_slug,
        resolved.interpreter,
        resolved.abs_script.relative_to(resolved.abs_dir),
        len(cli_args),
        "yes" if stdin_text is not None else "no",
    )
    return await _run_subprocess(
        interpreter=resolved.interpreter,
        abs_script=resolved.abs_script,
        abs_dir=resolved.abs_dir,
        args=cli_args,
        stdin_text=stdin_text,
    )


def is_script_tool(name: str) -> bool:
    return name == SCRIPT_TOOL_NAME


async def run_script_tool(name: str, args_json: str) -> str:
    """JSON 入参 → JSON 字符串出参（与 ``run_http_tool`` 形状对齐）。"""
    if name != SCRIPT_TOOL_NAME:
        return json.dumps({"ok": False, "error": f"unknown script tool: {name}"}, ensure_ascii=False)
    try:
        args = json.loads(args_json) if args_json else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}
    payload = await run_skill_script_tool(args)
    return json.dumps(payload, ensure_ascii=False)


# ── OpenAI tool schema ─────────────────────────────────────────────


def script_tool_schema() -> dict[str, Any]:
    """暴露给 LLM 的 OpenAI function spec。

    描述里**显式告诉 LLM** 优先用脚本提供的"非交互入口"——大多数 OpenClaw 风
    格的诊断脚本都同时提供 ``-c <id>`` / ``--check <id>`` 这种"一次性参数"模
    式，依赖 stdin 交互在 agent 场景里铁定超时。
    """
    return {
        "type": "function",
        "function": {
            "name": SCRIPT_TOOL_NAME,
            "description": (
                "执行已激活技能（SKILL.md）附件目录中的可执行脚本"
                "（python3 / node / bash / sh）并返回 stdout / stderr / exit_code。\n\n"
                "**何时使用**：当 SKILL.md 描述的诊断 / 数据提取 / 运维流程是"
                "「执行某个 .py / .js / .sh」而不是直接调 HTTP 接口时。优先用此工具，"
                "不要凭脑补改去调 http_get_json / http_post_json。\n\n"
                "**调用约定**：\n"
                "1. ``skill_slug``：脚本所属技能的 slug（与 SKILL.md frontmatter 一致）。\n"
                "2. ``interpreter``：python3 / node / bash / sh 之一。\n"
                "3. ``script``：脚本相对路径（如 scripts/dklive_diagnose.py），"
                "可以是 SKILL.md 里写过的，也可以是 SKILL.md 没写但同 skill 附件下的脚本"
                "——只要文件存在且扩展名匹配解释器即放行。\n"
                "4. ``args``：命令行参数列表（字符串），非交互模式优先（如 [\"-c\", \"<mid>\"]）。\n"
                "5. ``stdin``：可选 stdin 文本；不要依赖交互式 input()，"
                "wall-clock 35s 必杀。\n\n"
                "**安全约束**：\n"
                "- skill_slug 必须是本轮已激活 / 候选的 skill。\n"
                "- 路径不能逃出该技能附件目录（``..`` / 软链穿越被拦）。\n"
                "- CPU 30s / 内存 512 MB / 输出文件 50 MB / wall-clock 35s。\n"
                "- stdout 与 stderr 各 256 KB 截断，超过会带 *_truncated=true 标记。\n"
                "- env 已被白名单收窄；HOME 仍是容器原 HOME，admin 在容器里布置的"
                "本地 token 文件依然能被脚本读取。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_slug": {
                        "type": "string",
                        "description": "脚本所属技能的 slug",
                    },
                    "interpreter": {
                        "type": "string",
                        "enum": ["python3", "node", "bash", "sh"],
                    },
                    "script": {
                        "type": "string",
                        "description": "脚本相对路径，如 scripts/dklive_diagnose.py",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "命令行参数列表",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "可选 stdin 文本（不推荐；优先用 args）",
                    },
                },
                "required": ["skill_slug", "interpreter", "script"],
            },
        },
    }


__all__ = [
    "AllowedScript",
    "MAX_STREAM_BYTES",
    "SCRIPT_TOOL_NAME",
    "SkillRoot",
    "WALL_CLOCK_TIMEOUT_SECONDS",
    "extract_allowed_scripts_from_body",
    "get_active_skill_roots",
    "is_script_tool",
    "reset_active_skill_roots",
    "run_script_tool",
    "run_skill_script_tool",
    "script_tool_schema",
    "set_active_skill_roots",
]
