"""Skill 安全扫描（只读打标）— Phase 12 Task 12.3。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.modules.skills.platform_tools import platform_chat_openai_schemas

# 二期物料工具（SKILL.md 可能声明）；chat 通路另有三个全局 platform_*
_DATA_PLATFORM_TOOLS: frozenset[str] = frozenset(
    {
        "platform_get_test_data",
        "platform_get_secret",
        "platform_get_file",
        "platform_iter_dataset",
        "platform_synthesize_data",
        "platform_mark_data_failure",
        "platform_solve_captcha",
    },
)


def registered_platform_tool_names() -> frozenset[str]:
    return frozenset(platform_chat_openai_schemas().keys()) | _DATA_PLATFORM_TOOLS


BODY_WARNING_BYTES = 50 * 1024

SCANNER_VERSION = "1.0"

_HIGH_INJECTION_RES = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.I),
    re.compile(r"ignore\s+the\s+above", re.I),
    re.compile(r"disregard\s+(all\s+)?(prior\s+)?instructions?", re.I),
    re.compile(r"override\s+(the\s+)?system\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+DAN\b", re.I),
    re.compile(r"<\s*system\s*>", re.I),
    re.compile(r"\bjailbreak\b", re.I),
]

_HIGH_SENSITIVE_SUBSTR = [
    "/etc/passwd",
    "/etc/shadow",
    "postgres://",
    "mysql://",
    "mongodb://",
    "-----BEGIN PRIVATE KEY-----",
    "api_key=",
    "apikey=",
    "secret_key=",
]


@dataclass
class Finding:
    kind: str
    severity: str
    snippet: str
    line: int | None = None

    def as_dict(self) -> dict:
        return {
            "type": self.kind,
            "severity": self.severity,
            "snippet": self.snippet,
            "line": self.line,
        }


@dataclass
class ScanResult:
    status: str  # clean | warning | blocked
    findings: list[Finding] = field(default_factory=list)


class SafetyScanner:
    """内置规则集；不改 SKILL 正文，仅产出 findings / status。"""

    VERSION = SCANNER_VERSION

    def scan(self, body: str, metadata: dict | None) -> ScanResult:
        findings: list[Finding] = []
        text = body or ""
        meta = metadata or {}

        joined_for_scan = text + "\n" + _meta_strings(meta)

        for rx in _HIGH_INJECTION_RES:
            m = rx.search(joined_for_scan)
            if m:
                findings.append(
                    Finding(
                        kind="prompt_injection",
                        severity="high",
                        snippet=m.group(0)[:200],
                        line=_line_of(text, m.start())
                        if m.start() < len(text)
                        else None,
                    ),
                )
                break

        for needle in _HIGH_SENSITIVE_SUBSTR:
            idx = joined_for_scan.lower().find(needle.lower())
            if idx != -1:
                findings.append(
                    Finding(
                        kind="sensitive_string",
                        severity="high",
                        snippet=needle,
                        line=_line_of(text, idx) if idx < len(text) else None,
                    ),
                )

        tools_required = meta.get("tools_required") or []
        if isinstance(tools_required, list):
            allowed = registered_platform_tool_names()
            for t in tools_required:
                ts = str(t).strip()
                if ts.startswith("platform_") and ts not in allowed:
                    findings.append(
                        Finding(
                            kind="unregistered_platform_tool",
                            severity="medium",
                            snippet=ts,
                            line=None,
                        ),
                    )

        body_bytes = len(text.encode("utf-8"))
        if body_bytes > BODY_WARNING_BYTES:
            findings.append(
                Finding(
                    kind="large_body",
                    severity="medium",
                    snippet=f"body is {body_bytes} bytes (> {BODY_WARNING_BYTES})",
                    line=None,
                ),
            )

        zwj = text.count("\u200b") + text.count("\ufeff")
        if zwj > 20:
            findings.append(
                Finding(
                    kind="suspicious_unicode",
                    severity="low",
                    snippet=f"zero-width / BOM chars count={zwj}",
                    line=None,
                ),
            )

        status = "clean"
        severities = {f.severity for f in findings}
        if "high" in severities:
            status = "blocked"
        elif "medium" in severities:
            status = "warning"

        return ScanResult(status=status, findings=findings)


def _meta_strings(meta: dict) -> str:
    parts: list[str] = []
    for k, v in meta.items():
        if isinstance(v, (dict, list)):
            parts.append(str(v))
        elif v is not None:
            parts.append(str(v))
    return "\n".join(parts)


def _line_of(text: str, pos: int) -> int | None:
    if pos < 0 or pos > len(text):
        return None
    return text.count("\n", 0, pos) + 1
