#!/usr/bin/env python3
"""Render a claim audit report as Markdown for a run summary or a tracked issue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HEADER = ("| claim | status | detail |", "|---|---|---|")


def escape(text: str) -> str:
    return text.replace("|", "\\|")


def table(claims: list[dict[str, Any]]) -> list[str]:
    rows = [f"| `{item['id']}` | {item['status']} | {escape(item['detail'])} |" for item in claims]
    return [*HEADER, *rows] if rows else ["No claims to report."]


def render(report: dict[str, Any], failures_only: bool, run_url: str | None) -> str:
    claims = report["failures"] if failures_only else report["claims"]
    checked = report["claims_checked"]
    failing = len(report["failures"])
    if failures_only:
        lead = "Claim Auditor could not reproduce one or more claims published by this repository."
    else:
        lead = f"{checked} claims checked, {failing} failing"
    lines = ["## Claim Auditor", "", lead, "", *table(claims)]
    if run_url:
        lines += ["", f"Evidence: {run_url}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("claim-audit.json"))
    parser.add_argument("--failures-only", action="store_true")
    parser.add_argument("--run-url", default=None)
    args = parser.parse_args(argv)

    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        print("## Claim Auditor\n\nThe manifest could not be read; no claims were audited.")
        return 0

    print(render(report, args.failures_only, args.run_url), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
