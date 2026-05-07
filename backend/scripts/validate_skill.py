#!/usr/bin/env python3
"""Validate a skill ZIP locally (parse + safety scan). Phase 12 Task 12.3.

Usage (from repo root):

    ./run.sh validate-skill /path/to/package.zip

Exit codes: 0 OK (clean or warning), 1 blocked / parse error, 2 usage error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: validate_skill.py <path-to.zip>", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from app.modules.skills.importer import unpack_skill_zip
    from app.modules.skills.parser import SkillParseError
    from app.modules.skills.safety_scanner import SafetyScanner

    path = Path(argv[1]).expanduser()
    if not path.is_file():
        print(f"FAIL: not a file: {path}", file=sys.stderr)
        return 1

    raw = path.read_bytes()
    try:
        parsed, attachments = unpack_skill_zip(raw)
    except SkillParseError as e:
        print(f"FAIL: parse: {e}")
        return 1

    scan = SafetyScanner().scan(parsed.body, parsed.metadata)
    report = {
        "slug": parsed.slug,
        "name": parsed.name,
        "attachments": len(attachments),
        "scan_status": scan.status,
        "findings": [f.as_dict() for f in scan.findings],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if scan.status == "blocked":
        print("FAIL: blocked by safety scanner")
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
