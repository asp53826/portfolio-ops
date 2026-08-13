#!/usr/bin/env python3
"""Validate repository-local links in Markdown without third-party services."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK = re.compile(r"(?:href|src)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
SKIP_PREFIXES = ("#", "http://", "https://", "mailto:", "tel:", "data:", "javascript:")
SKIP_DIRS = {".git", ".portfolio-ops", "node_modules", "vendor", ".venv", "dist", "build"}


def markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
    )


def targets(text: str) -> list[str]:
    found = MARKDOWN_LINK.findall(text) + HTML_LINK.findall(text)
    return [item.strip().split(maxsplit=1)[0].strip("<>") for item in found]


def resolve_local(root: Path, source: Path, target: str) -> Path | None:
    if not target or target.startswith(SKIP_PREFIXES) or "${{" in target:
        return None
    clean = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not clean:
        return None
    candidate = root / clean.lstrip("/") if clean.startswith("/") else source.parent / clean
    return candidate.resolve()


def audit(root: Path) -> dict[str, object]:
    root = root.resolve()
    checked = 0
    broken: list[dict[str, str]] = []
    for source in markdown_files(root):
        text = source.read_text(encoding="utf-8", errors="replace")
        for target in targets(text):
            candidate = resolve_local(root, source, target)
            if candidate is None:
                continue
            checked += 1
            try:
                candidate.relative_to(root)
            except ValueError:
                broken.append({
                    "source": str(source.relative_to(root)),
                    "target": target,
                    "reason": "escapes repository root",
                })
                continue
            if not candidate.exists():
                broken.append({
                    "source": str(source.relative_to(root)),
                    "target": target,
                    "reason": "target does not exist",
                })
    return {
        "schema": 1,
        "root": str(root),
        "markdown_files": len(markdown_files(root)),
        "local_links_checked": checked,
        "broken": broken,
        "ok": not broken,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--report", type=Path, default=Path("docs-health.json"))
    args = parser.parse_args(argv)

    report = audit(args.root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"checked {report['local_links_checked']} local links across "
        f"{report['markdown_files']} Markdown files"
    )
    for failure in report["broken"]:
        print(f"broken: {failure['source']} -> {failure['target']} ({failure['reason']})")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
