#!/usr/bin/env python3
"""Refresh a profile README from verified GitHub repository facts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

START = "<!-- portfolio-status:start -->"
END = "<!-- portfolio-status:end -->"
HOUSEKEEPING_WORKFLOW_TERMS = ("codeql", "pages build", "configured graph")


def request_json(url: str, token: str) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "portfolio-ops-profile-curator/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def optional_json(url: str, token: str) -> Any | None:
    try:
        return request_json(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def is_dependabot_run(run: dict[str, Any]) -> bool:
    actor = run.get("actor") or {}
    return (
        run.get("event") == "dynamic"
        or str(run.get("path", "")).startswith("dynamic/dependabot/")
        or actor.get("login") == "dependabot[bot]"
    )


def select_workflow(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    engineering_runs = [run for run in runs if not is_dependabot_run(run)]
    return next(
        (
            run
            for run in engineering_runs
            if not any(
                term in str(run.get("name", "")).lower()
                for term in HOUSEKEEPING_WORKFLOW_TERMS
            )
        ),
        engineering_runs[0] if engineering_runs else None,
    )


def repo_record(owner: str, name: str, token: str) -> dict[str, Any] | None:
    slug = f"{owner}/{name}"
    base = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
    repository = optional_json(base, token)
    if repository is None:
        return None

    release = optional_json(f"{base}/releases/latest", token)
    runs = request_json(
        f"{base}/actions/runs?branch={urllib.parse.quote(repository['default_branch'])}"
        "&status=completed&per_page=20",
        token,
    ).get("workflow_runs", [])
    workflow = select_workflow(runs)
    return {
        "slug": slug,
        "url": repository["html_url"],
        "language": repository.get("language") or "Mixed",
        "archived": bool(repository.get("archived")),
        "release": (
            {"tag": release["tag_name"], "url": release["html_url"]} if release else None
        ),
        "workflow": (
            {
                "name": workflow.get("name") or "workflow",
                "conclusion": workflow.get("conclusion") or "unknown",
                "url": workflow["html_url"],
            }
            if workflow
            else None
        ),
    }


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render(records: list[dict[str, Any]]) -> str:
    lines = [
        START,
        "| Project | Primary language | Latest release | Latest completed workflow |",
        "|---|---|---|---|",
    ]
    for record in records:
        project = f"[{cell(record['slug'].split('/', 1)[1])}]({record['url']})"
        if record["archived"]:
            project += " (archived)"
        release = record["release"]
        release_cell = (
            f"[{cell(release['tag'])}]({release['url']})" if release else "No published release"
        )
        workflow = record["workflow"]
        workflow_cell = (
            f"[{cell(workflow['name'])}: {cell(workflow['conclusion'])}]({workflow['url']})"
            if workflow
            else "No completed workflow"
        )
        lines.append(
            f"| {project} | {cell(record['language'])} | {release_cell} | {workflow_cell} |"
        )
    lines.extend(
        [
            "",
            "This block is regenerated only when GitHub's repository, release, or workflow data changes.",
            END,
        ]
    )
    return "\n".join(lines)


def replace_block(readme: Path, block: str) -> bool:
    original = readme.read_text()
    if START not in original or END not in original:
        raise ValueError(f"{readme} is missing the Profile Curator markers")
    before, remainder = original.split(START, 1)
    _, after = remainder.split(END, 1)
    updated = before + block + after
    if updated == original:
        return False
    readme.write_text(updated)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repos", required=True, help="comma or newline-separated repository names")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    args = parser.parse_args(argv)

    names = [name.strip() for name in args.repos.replace(",", "\n").splitlines() if name.strip()]
    token = os.environ.get("GITHUB_TOKEN", "")
    records = []
    for name in names:
        record = repo_record(args.owner, name, token)
        if record is None:
            print(f"profile curator: excluding missing repository {args.owner}/{name}", file=sys.stderr)
            continue
        records.append(record)
    if not records:
        raise SystemExit("profile curator received no valid repository records; README left untouched")
    changed = replace_block(args.readme, render(records))
    print(f"profile curator verified {len(records)} repositories; changed={str(changed).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
