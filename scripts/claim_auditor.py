#!/usr/bin/env python3
"""Re-run the experiment behind every numeric claim a repository publishes.

A claim is only audited when the repository declares it in a manifest. The
auditor never infers claims from prose: it checks that a declared statement is
still present verbatim in the document that carries it, re-runs the command the
repository nominates as the source of the number, and compares the measured
value against the declared bound. Failing to find a claim is a failure, so a
claim cannot quietly stop being checked.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

VERIFIED = "verified"
DRIFTED = "drifted"
FAILED = "failed"
ERRORED = "errored"

NUMBER_TRASH = str.maketrans({",": "", "×": "", "−": "-", "%": ""})
COMPARISONS = ("expected", "min", "max")
EXTRACTORS = ("pattern", "json_path")


class ManifestError(Exception):
    """The manifest is unusable, which is a failure rather than an empty run."""


def normalise(text: str) -> str:
    """Collapse whitespace so a wrapped Markdown sentence still matches."""
    return " ".join(text.split())


def to_number(raw: str) -> float:
    cleaned = raw.strip().translate(NUMBER_TRASH).strip()
    try:
        return float(cleaned)
    except ValueError as error:
        raise ManifestError(f"cannot read a number from {raw!r}") from error


def load_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"no claim manifest at {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ManifestError(f"{path} is not valid TOML: {error}") from error

    claims = document.get("claim")
    if not isinstance(claims, list) or not claims:
        raise ManifestError(f"{path} declares no [[claim]] entries")

    seen: set[str] = set()
    for index, claim in enumerate(claims):
        validate(claim, index, seen)
    return claims


def validate(claim: dict[str, Any], index: int, seen: set[str]) -> None:
    where = claim.get("id") or f"claim #{index + 1}"
    for field in ("id", "statement", "command"):
        if not isinstance(claim.get(field), str) or not claim[field].strip():
            raise ManifestError(f"{where}: '{field}' is required and must be a non-empty string")
    if claim["id"] in seen:
        raise ManifestError(f"duplicate claim id {claim['id']!r}")
    seen.add(claim["id"])

    extractors = [name for name in EXTRACTORS if name in claim]
    if len(extractors) != 1:
        raise ManifestError(f"{where}: declare exactly one of {' or '.join(EXTRACTORS)}")
    if "pattern" in claim:
        try:
            compiled = re.compile(claim["pattern"])
        except re.error as error:
            raise ManifestError(f"{where}: invalid pattern: {error}") from error
        if compiled.groups != 1:
            raise ManifestError(f"{where}: pattern needs exactly one capture group")

    bounds = [name for name in COMPARISONS if name in claim]
    if not bounds:
        raise ManifestError(f"{where}: declare 'expected', or 'min' and/or 'max'")
    if "expected" in claim and len(bounds) > 1:
        raise ManifestError(f"{where}: 'expected' cannot be combined with 'min' or 'max'")
    for field in (*COMPARISONS, "tolerance", "tolerance_pct"):
        if field in claim and not isinstance(claim[field], (int, float)):
            raise ManifestError(f"{where}: '{field}' must be a number")
    if "tolerance" in claim and "tolerance_pct" in claim:
        raise ManifestError(f"{where}: declare 'tolerance' or 'tolerance_pct', not both")
    if ("tolerance" in claim or "tolerance_pct" in claim) and "expected" not in claim:
        raise ManifestError(f"{where}: a tolerance only applies alongside 'expected'")


def statement_present(root: Path, claim: dict[str, Any]) -> str | None:
    """Return a failure reason when the declared sentence is no longer published."""
    relative = claim.get("document", "README.md")
    document = (root / relative).resolve()
    try:
        document.relative_to(root)
    except ValueError:
        return f"document {relative} escapes the repository root"
    if not document.exists():
        return f"document {relative} does not exist"
    if normalise(claim["statement"]) not in normalise(
        document.read_text(encoding="utf-8", errors="replace")
    ):
        return f"statement is no longer present in {relative}"
    return None


def dig(payload: Any, path: str) -> Any:
    cursor = payload
    for segment in path.split("."):
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(segment)]
            except (ValueError, IndexError) as error:
                raise ManifestError(f"json_path {path!r} has no element {segment!r}") from error
        elif isinstance(cursor, dict) and segment in cursor:
            cursor = cursor[segment]
        else:
            raise ManifestError(f"json_path {path!r} has no key {segment!r}")
    return cursor


def measure(output: str, claim: dict[str, Any]) -> float:
    if "pattern" in claim:
        match = re.search(claim["pattern"], output)
        if match is None:
            raise ManifestError("pattern did not match the command output")
        return to_number(match.group(1))
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise ManifestError("command output is not JSON") from error
    value = dig(payload, claim["json_path"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"json_path {claim['json_path']!r} is not a number")
    return float(value)


def within(value: float, claim: dict[str, Any]) -> tuple[bool, str]:
    if "expected" in claim:
        expected = float(claim["expected"])
        if "tolerance_pct" in claim:
            allowed = abs(expected) * float(claim["tolerance_pct"]) / 100.0
            bound = f"{expected} ±{claim['tolerance_pct']}%"
        else:
            allowed = float(claim.get("tolerance", 0.0))
            bound = f"{expected} ±{allowed}" if allowed else f"exactly {expected}"
        return abs(value - expected) <= allowed, bound

    low = float(claim["min"]) if "min" in claim else None
    high = float(claim["max"]) if "max" in claim else None
    ok = (low is None or value >= low) and (high is None or value <= high)
    if low is not None and high is not None:
        bound = f"between {low} and {high}"
    elif low is not None:
        bound = f"at least {low}"
    else:
        bound = f"at most {high}"
    return ok, bound


def run(claim: dict[str, Any], root: Path, timeout: int,
        cache: dict[str, subprocess.CompletedProcess[str]]) -> subprocess.CompletedProcess[str]:
    """Run a claim's command, reusing output when several claims share one command.

    A benchmark that prints six numbers should be run once, not six times. The
    first claim to name a command decides its timeout.
    """
    command = claim["command"]
    if command not in cache:
        cache[command] = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=claim.get("timeout", timeout),
        )
    return cache[command]


def check(claim: dict[str, Any], root: Path, timeout: int, execute: bool,
          cache: dict[str, subprocess.CompletedProcess[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {"id": claim["id"], "statement": normalise(claim["statement"])}
    drift = statement_present(root, claim)
    if drift is not None:
        return {**result, "status": DRIFTED, "detail": drift}
    if not execute:
        return {**result, "status": VERIFIED, "detail": "statement present; command not run"}

    try:
        completed = run(claim, root, timeout, cache)
    except subprocess.TimeoutExpired:
        return {**result, "status": ERRORED, "detail": "command timed out"}
    except OSError as error:
        return {**result, "status": ERRORED, "detail": f"command could not start: {error}"}
    if completed.returncode != 0:
        tail = normalise(completed.stderr or completed.stdout)[-300:]
        return {**result, "status": ERRORED, "detail": f"command exited {completed.returncode}: {tail}"}

    try:
        value = measure(completed.stdout, claim)
    except ManifestError as error:
        return {**result, "status": ERRORED, "detail": str(error)}

    ok, bound = within(value, claim)
    return {
        **result,
        "status": VERIFIED if ok else FAILED,
        "measured": value,
        "bound": bound,
        "detail": f"measured {value:g}, claim requires {bound}",
    }


def audit(root: Path, manifest: Path, timeout: int = 900, execute: bool = True,
          only: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    claims = load_manifest(manifest)
    if only is not None:
        claims = [claim for claim in claims if claim["id"] == only]
        if not claims:
            raise ManifestError(f"no claim with id {only!r}")
    cache: dict[str, subprocess.CompletedProcess[str]] = {}
    results = [check(claim, root, timeout, execute, cache) for claim in claims]
    return {
        "schema": 1,
        "root": str(root),
        "manifest": str(manifest),
        "commands_executed": execute,
        "commands_run": len(cache),
        "claims_checked": len(results),
        "claims": results,
        "failures": [item for item in results if item["status"] != VERIFIED],
        "ok": all(item["status"] == VERIFIED for item in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=Path("claim-audit.json"))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--only", default=None, help="audit a single claim id")
    parser.add_argument("--check-only", action="store_true",
                        help="verify each statement is still published without running commands")
    args = parser.parse_args(argv)

    manifest = args.manifest or args.root / "claims.toml"
    try:
        report = audit(args.root, manifest, args.timeout, not args.check_only, args.only)
    except ManifestError as error:
        print(f"claim manifest unusable: {error}", file=sys.stderr)
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    for item in report["claims"]:
        marker = "ok  " if item["status"] == VERIFIED else item["status"]
        print(f"{marker:8} {item['id']}: {item['detail']}")
    print(f"\n{report['claims_checked']} claims checked, {len(report['failures'])} failing")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
