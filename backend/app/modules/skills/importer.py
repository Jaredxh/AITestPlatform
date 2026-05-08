"""Skill ZIP / URL 导入 — Phase 12 Task 12.3。"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import AppException, NotFoundException, PermissionDeniedException
from app.modules.auth.models import User
from app.modules.projects.models import Project
from app.modules.skills.models import Skill, SkillSafetyScan, SkillVersion
from app.modules.skills.parser import ParsedSkill, SkillParseError, parse_skill_md
from app.modules.skills.safety_scanner import SafetyScanner
from app.modules.skills.schemas import SkillImportPreview

logger = logging.getLogger(__name__)

ZIP_MAX_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENTS = 50
MAX_ATTACH_FILE_BYTES = 1024 * 1024

# ── ZIP 内自动忽略的"开发噪音"目录 / 文件 ──
#
# 用户经常把整个 git 工作树（含 ``.git/`` 全套对象）或 Python ``__pycache__/``
# 直接 zip 上来，光这两个目录就轻易超过 ``MAX_ATTACHMENTS=50``，导致原本能用
# 的 SKILL.md + 几个真正附件被拒。这些路径**不可能是技能本身的内容**，统一在
# 解压前过滤掉，比让用户每次手工 ``zip -x '*/.git/*'`` 友好得多。
#
# 命中规则：路径里**任意一段**等于下列名字（不区分大小写匹配 macOS / win 习惯）。
# 之所以匹配"段"而不是 startswith：嵌套路径如 ``foo/.git/HEAD`` 也得过滤掉。
_IGNORED_PATH_SEGMENTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".idea",
        ".vscode",
        ".DS_Store",  # 也是文件名匹配
        "Thumbs.db",
        "__MACOSX",
    },
)
_IGNORED_FILENAME_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo")


def _is_ignored_zip_entry(rel: str) -> bool:
    """判断 ZIP 内某条相对路径是否应当忽略（VCS / 缓存 / IDE / OS 元数据）。"""
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return True
    for seg in parts:
        if seg in _IGNORED_PATH_SEGMENTS:
            return True
        if seg.startswith("._"):  # macOS resource fork
            return True
    last = parts[-1]
    if last.endswith(_IGNORED_FILENAME_SUFFIXES):
        return True
    return False


def skill_version_directory(project_id: uuid.UUID, skill_id: uuid.UUID, db_version: int) -> Path:
    base = Path(settings.UPLOAD_DIR) / "skills" / str(project_id) / str(skill_id) / str(db_version)
    return base


def _norm_zip_name(name: str) -> str:
    return name.replace("\\", "/").strip("/")


def unpack_skill_zip(raw: bytes) -> tuple[ParsedSkill, list[tuple[str, bytes]]]:
    """解压 ZIP：解析 SKILL.md + 附件 `(relative_path, data)`（不含 SKILL.md）。"""
    if len(raw) > ZIP_MAX_BYTES:
        raise SkillParseError(f"ZIP exceeds limit ({ZIP_MAX_BYTES} bytes)")

    attachments: list[tuple[str, bytes]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise SkillParseError(f"invalid ZIP: {e}") from e

    with zf:
        names = [_norm_zip_name(n) for n in zf.namelist() if not str(n).endswith("/")]
        skill_candidates = sorted(
            (n for n in names if n.upper().endswith("/SKILL.MD") or n.upper() == "SKILL.MD"),
            key=len,
        )
        if not skill_candidates:
            raise SkillParseError("ZIP must contain SKILL.md")

        skill_member = skill_candidates[0]
        prefix = ""
        if "/" in skill_member:
            prefix = skill_member.rsplit("/", 1)[0]

        try:
            md_bytes = zf.read(skill_member)
        except KeyError as e:
            raise SkillParseError(f"cannot read {skill_member}") from e

        text = md_bytes.decode("utf-8")
        parsed = parse_skill_md(text)

        attach_count = 0
        ignored_count = 0
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _norm_zip_name(info.filename)
            if not name or name == skill_member:
                continue
            if prefix:
                if not name.startswith(prefix + "/"):
                    continue
                rel = name[len(prefix) + 1 :]
            else:
                if name.upper() == "SKILL.MD":
                    continue
                rel = name

            if _is_ignored_zip_entry(rel):
                ignored_count += 1
                continue

            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise SkillParseError(f"unsafe attachment path: {rel!r}")

            raw_sz = info.file_size
            if raw_sz > MAX_ATTACH_FILE_BYTES:
                raise SkillParseError(
                    f"attachment {rel!r} exceeds {MAX_ATTACH_FILE_BYTES} bytes",
                )

            data = zf.read(info.filename)
            attachments.append((rel.replace("\\", "/"), data))
            attach_count += 1
            if attach_count > MAX_ATTACHMENTS:
                raise SkillParseError(
                    f"附件数量超过上限 {MAX_ATTACHMENTS} 个；请在打包前剔除非技能内容"
                    "（.git/、__pycache__/、node_modules/、.DS_Store 等本平台已自动忽略，"
                    "若仍超限说明真实附件过多，建议精简或拆成多个技能）",
                )

        if ignored_count:
            logger.info("skill import: ignored %d dev/cache files in ZIP", ignored_count)

    return parsed, attachments


def _preview_from_parsed(
    parsed: ParsedSkill,
    attachments_meta: list[dict],
    scan_status: str,
    findings: list[dict],
    skill_id: uuid.UUID | None,
) -> SkillImportPreview:
    unknown_keys = sorted(
        k
        for k in parsed.metadata.keys()
        if k
        not in {
            "name",
            "description",
            "slug",
            "version",
            "semantic_version",
            "category",
            "tags",
            "triggers",
            "tools_required",
            "activation_mode",
        }
    )
    body_preview = parsed.body[:800]
    return SkillImportPreview(
        name=parsed.name,
        slug=parsed.slug,
        description=parsed.description,
        semantic_version=parsed.semantic_version,
        category=parsed.category,
        activation_mode=parsed.activation_mode,
        triggers=parsed.triggers,
        tools_required=parsed.tools_required,
        body_preview=body_preview,
        body_size_bytes=len(parsed.body.encode("utf-8")),
        attachments=attachments_meta,
        safety_status=scan_status,
        safety_findings=findings,
        metadata_extra_keys=unknown_keys,
        skill_id=skill_id,
    )


def _scan_notes(findings: list) -> str | None:
    if not findings:
        return None
    first = findings[0]
    if isinstance(first, dict):
        return str(first.get("snippet", ""))[:500]
    return None


async def _persist_skill_bundle(
    db: AsyncSession,
    project_id: uuid.UUID,
    user: User,
    parsed: ParsedSkill,
    attachment_files: list[tuple[str, bytes]],
    scan_status: str,
    scan_findings: list[dict],
    *,
    source_url: str | None,
) -> Skill:
    if parsed.slug.startswith("system_"):
        raise PermissionDeniedException(
            "skill slug prefix system_ is reserved for built-in skills",
        )

    is_enabled = scan_status == "clean"

    proj = await db.get(Project, project_id)
    if proj is None:
        raise NotFoundException("project not found")

    skill = Skill(
        project_id=project_id,
        name=parsed.name[:200],
        slug=parsed.slug[:100],
        description=parsed.description,
        semantic_version=parsed.semantic_version[:20],
        category=parsed.category[:50],
        tags=list(parsed.tags),
        triggers=list(parsed.triggers),
        tools_required=list(parsed.tools_required),
        activation_mode=parsed.activation_mode,
        body=parsed.body,
        extra_metadata=dict(parsed.metadata),
        attachments=[],
        source="imported",
        source_url=source_url[:500] if source_url else None,
        is_enabled=is_enabled,
        safety_scan_status=scan_status,
        safety_scan_notes=_scan_notes(scan_findings),
        db_version=1,
        created_by=user.id,
    )

    db.add(skill)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise AppException(
            "skill slug already exists in this project",
            code="SKILL_SLUG_DUPLICATE",
        ) from e

    root = skill_version_directory(project_id, skill.id, skill.db_version)
    root.mkdir(parents=True, exist_ok=True)

    attach_records: list[dict] = []
    for rel, data in attachment_files:
        safe_rel = Path(rel)
        if safe_rel.is_absolute() or ".." in safe_rel.parts:
            raise SkillParseError(f"unsafe attachment path: {rel!r}")
        dest = root / safe_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        attach_records.append(
            {
                "path": rel.replace("\\", "/"),
                "size": len(data),
            },
        )

    skill.attachments = attach_records

    sv = SkillVersion(
        skill_id=skill.id,
        db_version=skill.db_version,
        body=skill.body,
        extra_metadata=dict(skill.extra_metadata),
        change_note="import",
        created_by=user.id,
    )
    db.add(sv)

    scan_row = SkillSafetyScan(
        skill_id=skill.id,
        skill_db_version=skill.db_version,
        status=scan_status,
        findings=scan_findings,
        scanner_version=SafetyScanner.VERSION,
    )
    db.add(scan_row)

    await db.flush()
    await db.refresh(skill)
    return skill


async def import_zip(
    db: AsyncSession,
    project_id: uuid.UUID,
    file: UploadFile,
    user: User,
) -> SkillImportPreview:
    raw = await file.read()
    try:
        parsed, attachment_files = unpack_skill_zip(raw)
    except SkillParseError as e:
        raise AppException(str(e), code="SKILL_PARSE_ERROR") from e

    scanner = SafetyScanner()
    scan = scanner.scan(parsed.body, parsed.metadata)

    findings = [f.as_dict() for f in scan.findings]

    if scan.status == "blocked":
        return _preview_from_parsed(
            parsed,
            [{"path": r, "size": len(b)} for r, b in attachment_files],
            "blocked",
            findings,
            skill_id=None,
        )

    skill = await _persist_skill_bundle(
        db,
        project_id,
        user,
        parsed,
        attachment_files,
        scan.status,
        findings,
        source_url=None,
    )

    return _preview_from_parsed(
        parsed,
        skill.attachments or [],
        scan.status,
        findings,
        skill_id=skill.id,
    )


async def import_url(
    db: AsyncSession,
    project_id: uuid.UUID,
    url: str,
    user: User,
    *,
    ref: str | None = None,
) -> Skill:
    try:
        raw_bundle = await _fetch_skill_bundle_bytes(url, ref=ref)
    except SkillParseError as e:
        raise AppException(str(e), code="SKILL_PARSE_ERROR") from e

    if raw_bundle is None:
        raise AppException("unsupported import URL", code="SKILL_IMPORT_URL_UNSUPPORTED")

    parsed: ParsedSkill
    attachment_files: list[tuple[str, bytes]]
    try:
        if raw_bundle[0] == "zip":
            parsed, attachment_files = unpack_skill_zip(raw_bundle[1])
            source_url = url[:500]
        else:
            parsed = parse_skill_md(raw_bundle[1].decode("utf-8", errors="replace"))
            attachment_files = []
            source_url = url[:500]
    except SkillParseError as e:
        raise AppException(str(e), code="SKILL_PARSE_ERROR") from e

    scanner = SafetyScanner()
    scan = scanner.scan(parsed.body, parsed.metadata)
    findings = [f.as_dict() for f in scan.findings]

    if scan.status == "blocked":
        raise AppException(
            "import blocked by safety scanner",
            code="SKILL_IMPORT_BLOCKED",
        )

    return await _persist_skill_bundle(
        db,
        project_id,
        user,
        parsed,
        attachment_files,
        scan.status,
        findings,
        source_url=source_url,
    )


async def _fetch_skill_bundle_bytes(url: str, *, ref: str | None) -> tuple[str, bytes] | None:
    u = url.strip()
    if u.startswith("git+"):
        u = u[4:]

    if _looks_like_git_clone_target(u):
        content = await asyncio.to_thread(_git_clone_and_read_skill_md, u, ref)
        return ("md", content)

    try:
        data = await asyncio.to_thread(_http_get_bytes, u)
    except Exception as e:
        logger.warning("skill URL fetch failed: %s", e)
        return None

    if len(data) > ZIP_MAX_BYTES:
        raise SkillParseError(f"download exceeds {ZIP_MAX_BYTES} bytes")

    if len(data) >= 2 and data[:2] == b"PK":
        return ("zip", data)

    text_head = data[:200].lstrip()
    if text_head.startswith(b"---"):
        return ("md", data)

    raise SkillParseError(
        "URL did not return a ZIP archive or UTF-8 SKILL.md text starting with ---",
    )


def _looks_like_git_clone_target(u: str) -> bool:
    ul = u.lower()
    if ul.endswith(".zip"):
        return False
    return ul.startswith("git@") or ul.endswith(".git") or ("github.com" in ul and "/archive/" not in ul)


def _http_get_bytes(url: str, *, timeout: int = 120) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AITestPlatform-SkillImport/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _git_clone_and_read_skill_md(repo: str, ref: str | None) -> bytes:
    tmp = Path(tempfile.mkdtemp(prefix="skill_git_"))
    try:
        cmd = ["git", "clone", "--depth", "1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += [repo, str(tmp / "repo")]
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=180,
        )
        repo_root = tmp / "repo"
        md_path = _find_skill_md_path(repo_root)
        return md_path.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _find_skill_md_path(root: Path) -> Path:
    direct = root / "SKILL.md"
    if direct.is_file():
        return direct
    for p in sorted(root.rglob("SKILL.md"), key=lambda x: len(str(x))):
        if p.is_file():
            return p
    raise SkillParseError(f"no SKILL.md found under {root}")
