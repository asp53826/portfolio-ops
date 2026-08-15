#!/usr/bin/env python3
"""Check that a repository's published instructions produce its published numbers.

Claim Auditor binds a sentence in the README to a command in claims.toml. Nothing
binds either of them to the commands the README actually tells a reader to run.
That gap is real: cdcl-sat published a table measured at 300 instances and 120
variables while its "Verify it" block said `make proof-test`, which defaults to
60 instances at 90 variables. Every claim passed, every test passed, and the
documented reproduction produced different numbers than the documented table.

This closes the triangle. Every command an audited claim depends on must appear
verbatim in a fenced block the README offers as the way to reproduce it. A claim
whose command is not documented is a number a reader cannot check, which for
this portfolio is the same as a number that is not measured.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

FENCE = re.compile(r"^[ \t]*(?:```|~~~)([^\n]*)\n(.*?)^[ \t]*(?:```|~~~)[ \t]*$", re.M | re.S)
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)
PROMPT = re.compile(r"^[ \t]*[$>][ \t]+")
COMMENT = re.compile(r"\s+#\s.*$")
SHELL_LANGS = {"", "bash", "sh", "shell", "console", "zsh", "text"}

DOCUMENTED = "documented"
UNDOCUMENTED = "undocumented"
EXEMPT = "exempt"


class RehearsalError(Exception):
    """The manifest or document is unusable, which is a failure, not an empty run."""


def normalise(command: str) -> str:
    """Collapse continuations and whitespace so wrapped shell lines still match."""
    joined = command.replace("\\\n", " ")
    stripped = COMMENT.sub("", PROMPT.sub("", joined))
    return " ".join(stripped.split())


def sections(text: str) -> list[tuple[str, str]]:
    """Split a Markdown document into (heading, body) pairs."""
    marks = list(HEADING.finditer(text))
    if not marks:
        return [("", text)]
    found = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        found.append((mark.group(2).strip(), text[mark.end():end]))
    return found


def blocks(text: str, headings: list[str] | None) -> list[str]:
    """Return shell command lines from fenced blocks, optionally scoped to headings."""
    if headings:
        wanted = {name.casefold() for name in headings}
        bodies = [body for heading, body in sections(text) if heading.casefold() in wanted]
    else:
        bodies = [text]

    lines: list[str] = []
    for body in bodies:
        for match in FENCE.finditer(body):
            language = match.group(1).strip().casefold()
            if language and language not in SHELL_LANGS:
                continue
            for line in match.group(2).splitlines():
                if line.strip():
                    lines.append(line)
    return lines


def documented_commands(text: str, headings: list[str] | None) -> set[str]:
    """Normalised commands the document offers, including `a && b` split into parts."""
    found: set[str] = set()
    pending = ""
    for line in blocks(text, headings):
        pending = f"{pending} {line}" if pending else line
        if pending.rstrip().endswith("\\"):
            pending = pending.rstrip()[:-1]
            continue
        whole = normalise(pending)
        pending = ""
        if not whole:
            continue
        found.add(whole)
        for part in re.split(r"&&|\|\||;", whole):
            piece = normalise(part)
            if piece:
                found.add(piece)
    return found


def load(path: Path) -> list[dict[str, Any]]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RehearsalError(f"no claim manifest at {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise RehearsalError(f"{path} is not valid TOML: {error}") from error
    claims = document.get("claim")
    if not isinstance(claims, list) or not claims:
        raise RehearsalError(f"{path} declares no [[claim]] entries")
    for claim in claims:
        if not isinstance(claim.get("command"), str) or not claim["command"].strip():
            raise RehearsalError(f"{claim.get('id', 'a claim')} has no command")
        if "undocumented_reason" in claim and not str(claim["undocumented_reason"]).strip():
            raise RehearsalError(f"{claim['id']}: undocumented_reason must say why")
    return claims


def rehearse(root: Path, manifest: Path, document: str = "README.md",
             headings: list[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    page = (root / document).resolve()
    try:
        page.relative_to(root)
    except ValueError:
        raise RehearsalError(f"document {document} escapes the repository root")
    if not page.exists():
        raise RehearsalError(f"document {document} does not exist")

    text = page.read_text(encoding="utf-8", errors="replace")
    offered = documented_commands(text, headings)
    if not offered and headings:
        raise RehearsalError(
            f"{document} has no shell blocks under {' or '.join(headings)}; "
            "the reproduction section is missing or renamed"
        )

    results = []
    for claim in load(manifest):
        wanted = normalise(claim["command"])
        if wanted in offered:
            status, detail = DOCUMENTED, "command appears in the reproduction section"
        elif "undocumented_reason" in claim:
            status = EXEMPT
            detail = f"exempt: {claim['undocumented_reason']}"
        else:
            status = UNDOCUMENTED
            detail = f"no fenced block runs `{wanted}`"
        results.append({"id": claim["id"], "command": wanted, "status": status,
                        "detail": detail})

    return {
        "schema": 1,
        "root": str(root),
        "document": document,
        "headings": headings or [],
        "commands_offered": sorted(offered),
        "claims_checked": len(results),
        "claims": results,
        "failures": [item for item in results if item["status"] == UNDOCUMENTED],
        "ok": all(item["status"] != UNDOCUMENTED for item in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--document", default="README.md")
    parser.add_argument("--section", action="append", dest="sections",
                        help="heading to scope the search to; repeatable")
    parser.add_argument("--report", type=Path, default=Path("quickstart-rehearsal.json"))
    args = parser.parse_args(argv)

    manifest = args.manifest or args.root / "claims.toml"
    try:
        report = rehearse(args.root, manifest, args.document, args.sections)
    except RehearsalError as error:
        print(f"quickstart rehearsal unusable: {error}", file=sys.stderr)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    for item in report["claims"]:
        marker = "ok  " if item["status"] != UNDOCUMENTED else UNDOCUMENTED
        print(f"{marker:12} {item['id']}: {item['detail']}")
    print(f"\n{report['claims_checked']} claims checked, "
          f"{len(report['failures'])} not reproducible from {report['document']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
