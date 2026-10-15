#!/usr/bin/env python3
"""Grade every public repository an owner publishes, from public evidence only.

Fleet Scoreboard shallow-clones each public, non-fork, non-archived repository,
runs string-only checks against the clone, and reads default-branch workflow
results from the GitHub API. It never executes code from a scored repository:
claim statements are compared as text, README commands are matched as text and
documentation links are resolved on disk.

Every run records a check-in (time and run link) next to the grades, so the
scoreboard history shows each time the fleet was graded, including runs where
nothing changed. Private repositories are never listed, cloned or named, and a
commit author's address is reported as a yes/no, never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claim_auditor
import docs_scribe
import quickstart_rehearsal

API = "https://api.github.com"
FORMAT = "portfolio-ops-fleet-scoreboard/v1"
RUBRIC = "v1"

MAX = {
    "workflows": 25,
    "lab": 20,
    "evidence": 20,
    "docs": 10,
    "supply_chain": 20,
    "privacy": 5,
}
LABELS = {
    "workflows": "Workflows",
    "lab": "Lab",
    "evidence": "Evidence",
    "docs": "Docs",
    "supply_chain": "Supply chain",
    "privacy": "Privacy",
}
RUBRIC_TEXT = {
    "workflows": "Share of default-branch workflows whose latest push, schedule or manual run "
    "succeeded. A scheduled workflow that last ran over 45 days ago counts half, since GitHub "
    "disables schedules in public repositories after 60 days without activity.",
    "lab": "Share of jobs that passed in the latest scheduled Autonomous Engineering Lab run. "
    "Not enrolled or never run scores zero; a run older than 45 days counts half.",
    "evidence": "5 for a claims.toml, 5 for claim statements still present in the README, "
    "10 for claims whose command appears in a README code block (Quickstart Rehearsal).",
    "docs": "10 when every repository-local Markdown link resolves (Documentation Scribe).",
    "supply_chain": "8 for action uses pinned to a commit SHA (GitHub-owned actions included, "
    "as OpenSSF Scorecard counts them; your own actions exempt), 6 for top-level permissions "
    "blocks, 3 for Dependabot, 3 for portfolio-ops refs at the latest release.",
    "privacy": "5 when the latest commit author uses a GitHub noreply address.",
}

STALE_DAYS = 45
IGNORED_CONCLUSIONS = {None, "cancelled", "skipped", "neutral", "stale"}
WORKFLOW_EVENTS = {"push", "schedule", "workflow_dispatch"}
LAB_EVENTS = {"schedule", "workflow_dispatch"}
LAB_FILE = "autonomous-lab.yml"
SHA = re.compile(r"[0-9a-f]{40}")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
TOP_PERMISSIONS = re.compile(r"^permissions\s*:", re.MULTILINE)
RELEASE_TAG = re.compile(r"v\d+\.\d+\.\d+")
NOREPLY_SUFFIX = "@users.noreply.github.com"

Fetch = Callable[[str, str], Any]


class ScoreboardError(RuntimeError):
    pass


# --------------------------------------------------------------------------- GitHub


def request_json(url: str, token: str, attempts: int = 3) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "portfolio-ops-fleet-scoreboard/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            transient = error.code >= 500 or error.code == 429
            if not transient or attempt == attempts:
                raise ScoreboardError(f"GitHub API {error.code} for {url}") from error
        except urllib.error.URLError as error:
            if attempt == attempts:
                raise ScoreboardError(f"GitHub API unreachable for {url}: {error}") from error
        time.sleep(2 ** attempt)
    raise ScoreboardError(f"GitHub API gave up on {url}")


def list_public_repos(owner: str, token: str, fetch: Fetch = request_json) -> list[dict[str, str]]:
    repos: list[dict[str, str]] = []
    page = 1
    while True:
        batch = fetch(
            f"{API}/users/{urllib.parse.quote(owner)}/repos"
            f"?type=owner&sort=full_name&per_page=100&page={page}",
            token,
        )
        for repo in batch:
            if repo.get("private") or repo.get("fork") or repo.get("archived"):
                continue
            if repo.get("visibility", "public") != "public":
                continue
            repos.append({
                "name": repo["name"],
                "default_branch": repo.get("default_branch") or "main",
                "url": repo["html_url"],
            })
        if len(batch) < 100:
            break
        page += 1
    return sorted(repos, key=lambda repo: repo["name"].lower())


def default_branch_runs(owner: str, name: str, branch: str, token: str,
                        fetch: Fetch = request_json) -> list[dict[str, Any]]:
    url = (
        f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/actions/runs"
        f"?branch={urllib.parse.quote(branch)}&status=completed&per_page=100"
        "&exclude_pull_requests=true"
    )
    return fetch(url, token).get("workflow_runs", [])


def run_jobs(owner: str, name: str, run_id: int, token: str,
             fetch: Fetch = request_json) -> list[dict[str, Any]]:
    url = (
        f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"
        f"/actions/runs/{run_id}/jobs?per_page=100"
    )
    return fetch(url, token).get("jobs", [])


def parse_ls_remote(text: str) -> tuple[str, str] | None:
    """Return the highest vX.Y.Z tag and the commit it points at."""
    tags: dict[str, str] = {}
    for line in text.splitlines():
        sha, _, ref = line.partition("\t")
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        peeled = name.endswith("^{}")
        name = name.removesuffix("^{}")
        if not RELEASE_TAG.fullmatch(name):
            continue
        if peeled or name not in tags:
            tags[name] = sha
    if not tags:
        return None
    latest = max(tags, key=lambda tag: tuple(int(part) for part in tag[1:].split(".")))
    return latest, tags[latest]


def latest_ops_release(owner: str) -> tuple[str, str] | None:
    try:
        completed = subprocess.run(
            ["git", "ls-remote", "--tags", f"https://github.com/{owner}/portfolio-ops.git"],
            capture_output=True, text=True, timeout=60, check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return parse_ls_remote(completed.stdout)


def clone(owner: str, name: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", "--no-tags",
             f"https://github.com/{owner}/{name}.git", str(dest)],
            capture_output=True, text=True, timeout=300, check=True, env=env,
        )
    except subprocess.CalledProcessError as error:
        raise ScoreboardError(f"could not clone {owner}/{name}: {error.stderr.strip()}") from error


# --------------------------------------------------------------------------- checks


def check(points: float, maximum: int, details: list[str]) -> dict[str, Any]:
    points = max(0.0, min(float(maximum), points))
    return {"points": round(points, 1), "max": maximum, "details": details}


def not_checked(maximum: int, reason: str) -> dict[str, Any]:
    return {"points": None, "max": maximum, "details": [reason]}


def workflow_texts(root: Path) -> dict[str, str]:
    folder = root / ".github" / "workflows"
    if not folder.is_dir():
        return {}
    return {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix in (".yml", ".yaml")
    }


def classify_ref(ref: str, owner: str) -> str:
    if ref.startswith("./"):
        return "local"
    if ref.startswith("docker://"):
        return "pinned" if "@sha256:" in ref else "unpinned"
    slug, _, version = ref.partition("@")
    if slug.split("/", 1)[0].lower() == owner.lower():
        return "owned"
    return "pinned" if SHA.fullmatch(version) else "unpinned"


def supply_chain_check(root: Path, owner: str,
                       latest: tuple[str, str] | None) -> dict[str, Any]:
    texts = workflow_texts(root)
    third_party: list[str] = []
    unpinned: set[str] = set()
    ops_versions: set[str] = set()
    for text in texts.values():
        for ref in USES.findall(text):
            kind = classify_ref(ref, owner)
            if kind in ("pinned", "unpinned"):
                third_party.append(ref)
                if kind == "unpinned":
                    unpinned.add(ref)
            elif kind == "owned" and "/portfolio-ops/" in ref:
                ops_versions.add(ref.partition("@")[2])

    details: list[str] = []
    pinned_count = sum(1 for ref in third_party if ref not in unpinned)
    pin_ratio = pinned_count / len(third_party) if third_party else 1.0
    if unpinned:
        sample = ", ".join(sorted(unpinned)[:3])
        more = f" and {len(unpinned) - 3} more" if len(unpinned) > 3 else ""
        details.append(
            f"{len(third_party) - pinned_count}/{len(third_party)} action uses not pinned to a "
            f"commit SHA: {sample}{more}"
        )

    with_permissions = [name for name, text in texts.items() if TOP_PERMISSIONS.search(text)]
    permission_ratio = len(with_permissions) / len(texts) if texts else 1.0
    missing = sorted(set(texts) - set(with_permissions))
    if missing:
        details.append(f"no top-level permissions block in {', '.join(missing)}")

    dependabot = any((root / ".github" / name).is_file()
                     for name in ("dependabot.yml", "dependabot.yaml"))
    if not dependabot:
        details.append("no Dependabot configuration")

    ops_current = True
    if ops_versions and latest is not None:
        stale = sorted(
            version[:12] if SHA.fullmatch(version) else version
            for version in ops_versions if version not in latest
        )
        if stale:
            ops_current = False
            details.append(
                f"portfolio-ops pinned at {', '.join(stale)}; latest release is {latest[0]}"
            )

    points = 8 * pin_ratio + 6 * permission_ratio + 3 * dependabot + 3 * ops_current
    return check(points, MAX["supply_chain"], details)


def evidence_check(root: Path) -> dict[str, Any]:
    manifest = root / "claims.toml"
    if not manifest.is_file():
        return check(0, MAX["evidence"], ["no claims.toml, so published numbers are not audited"])
    try:
        audit = claim_auditor.audit(root, manifest, execute=False)
    except claim_auditor.ManifestError as error:
        return check(0, MAX["evidence"], [f"claims.toml is invalid: {error}"])

    total = audit["claims_checked"]
    present = total - len(audit["failures"])
    details: list[str] = []
    if present < total:
        details.append(f"{total - present}/{total} claim statements no longer match the README")

    try:
        rehearsal = quickstart_rehearsal.rehearse(root, manifest)
        reproducible = sum(
            1 for claim in rehearsal["claims"]
            if claim["status"] != quickstart_rehearsal.UNDOCUMENTED
        )
    except quickstart_rehearsal.RehearsalError as error:
        reproducible = 0
        details.append(f"Quickstart Rehearsal could not run: {error}")
    if reproducible < total:
        details.append(
            f"{total - reproducible}/{total} claims have no README command a reader can run"
        )

    points = 5 + 5 * present / total + 10 * reproducible / total
    return check(points, MAX["evidence"], details)


def docs_check(root: Path) -> dict[str, Any]:
    report = docs_scribe.audit(root)
    if report["ok"]:
        return check(MAX["docs"], MAX["docs"], [])
    broken = len(report["broken"])
    return check(0, MAX["docs"], [f"{broken} broken repository-local Markdown link(s)"])


def privacy_check(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%ae"],
        capture_output=True, text=True, check=False,
    )
    email = completed.stdout.strip().lower()
    if completed.returncode != 0 or not email or email.endswith(NOREPLY_SUFFIX):
        return check(MAX["privacy"], MAX["privacy"], [])
    return check(0, MAX["privacy"],
                 ["latest commit author uses a public email instead of a GitHub noreply address"])


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def is_lab(path: str) -> bool:
    return Path(path.split("@", 1)[0]).name == LAB_FILE


def counted(run: dict[str, Any], events: set[str]) -> bool:
    return (
        str(run.get("path", "")).startswith(".github/workflows/")
        and run.get("event") in events
        and run.get("conclusion") not in IGNORED_CONCLUSIONS
    )


def latest_by_workflow(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    chosen: dict[str, dict[str, Any]] = {}
    for run in runs:  # the API lists newest first
        path = str(run.get("path", "")).split("@", 1)[0]
        if is_lab(path) or not counted(run, WORKFLOW_EVENTS):
            continue
        chosen.setdefault(path, run)
    return chosen


def workflows_check(runs: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    latest = latest_by_workflow(runs)
    if not latest:
        return check(0, MAX["workflows"], ["no completed workflow run on the default branch"])
    earned = 0.0
    details: list[str] = []
    for path, run in sorted(latest.items()):
        name = run.get("name") or Path(path).name
        if run.get("conclusion") != "success":
            details.append(f"{name}: latest run {run.get('conclusion')}")
            continue
        stale = (now - parse_time(run["updated_at"])).days > STALE_DAYS
        if run.get("event") == "schedule" and stale:
            earned += 0.5
            details.append(
                f"{name}: last scheduled run is older than {STALE_DAYS} days; "
                "its schedule may have been disabled"
            )
            continue
        earned += 1
    return check(MAX["workflows"] * earned / len(latest), MAX["workflows"], details)


def lab_check(texts: dict[str, str], runs: list[dict[str, Any]], now: datetime,
              jobs_for: Callable[[int], list[dict[str, Any]]]) -> dict[str, Any]:
    if LAB_FILE not in texts:
        return check(0, MAX["lab"], ["not enrolled in the Autonomous Engineering Lab"])
    lab_runs = [run for run in runs if is_lab(str(run.get("path", ""))) and counted(run, LAB_EVENTS)]
    if not lab_runs:
        return check(0, MAX["lab"], ["the lab has no completed scheduled run on the default branch"])
    run = lab_runs[0]
    outcomes: dict[str, bool] = {}
    for job in jobs_for(run["id"]):
        conclusion = job.get("conclusion")
        if conclusion in IGNORED_CONCLUSIONS:
            continue
        label = str(job.get("name", "job")).split(" / ", 1)[0]
        outcomes[label] = outcomes.get(label, True) and conclusion == "success"
    if not outcomes:
        return check(0, MAX["lab"], ["the latest lab run executed no jobs"])
    failing = sorted(label for label, passed in outcomes.items() if not passed)
    details = [f"{label} failed in the latest scheduled lab run" for label in failing]
    ratio = (len(outcomes) - len(failing)) / len(outcomes)
    if (now - parse_time(run["updated_at"])).days > STALE_DAYS:
        ratio *= 0.5
        details.append(
            f"the lab has not run in over {STALE_DAYS} days; its schedule may have been disabled"
        )
    return check(MAX["lab"] * ratio, MAX["lab"], details)


# --------------------------------------------------------------------------- scoring


def grade(score: int) -> str:
    for floor, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= floor:
            return letter
    return "F"


def total(checks: dict[str, dict[str, Any]]) -> int:
    scored = [item for item in checks.values() if item["points"] is not None]
    possible = sum(item["max"] for item in scored)
    if not possible:
        return 0
    return round(100 * sum(item["points"] for item in scored) / possible)


def score_repository(owner: str, repo: dict[str, str], root: Path,
                     runs: list[dict[str, Any]] | None,
                     latest: tuple[str, str] | None, now: datetime,
                     jobs_for: Callable[[int], list[dict[str, Any]]] | None) -> dict[str, Any]:
    texts = workflow_texts(root)
    if runs is None or jobs_for is None:
        workflows = not_checked(MAX["workflows"], "not checked: offline preview")
        lab = not_checked(MAX["lab"], "not checked: offline preview")
    else:
        workflows = workflows_check(runs, now)
        lab = lab_check(texts, runs, now, jobs_for)
    checks = {
        "workflows": workflows,
        "lab": lab,
        "evidence": evidence_check(root),
        "docs": docs_check(root),
        "supply_chain": supply_chain_check(root, owner, latest),
        "privacy": privacy_check(root),
    }
    score = total(checks)
    return {
        "name": repo["name"],
        "url": repo["url"],
        "score": score,
        "grade": grade(score),
        "checks": checks,
    }


# --------------------------------------------------------------------------- output


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    grades = {letter: 0 for letter in "ABCDF"}
    for record in records:
        grades[record["grade"]] += 1
    average = round(sum(record["score"] for record in records) / len(records)) if records else 0
    return {"repositories": len(records), "average": average, "grades": grades}


def cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def points_cell(item: dict[str, Any]) -> str:
    if item["points"] is None:
        return "–"
    return f"{round(item['points'])}/{item['max']}"


def render_markdown(owner: str, latest: tuple[str, str] | None,
                    records: list[dict[str, Any]], checkin: dict[str, str]) -> str:
    stats = summary(records)
    grades = " · ".join(f"{letter}: {count}" for letter, count in stats["grades"].items())
    lines = [
        "# Fleet Scoreboard",
        "",
        (
            f"Every public, non-fork, non-archived `{owner}` repository, graded from public "
            "evidence by [Fleet Scoreboard](../.github/workflows/fleet-scoreboard.yml). The bot "
            "checks in every three hours; the commit message says whether any grade changed."
        ),
        "",
        f"**Last checked:** {checkin['checked_at']} · [run]({checkin['run_url']})"
        if checkin.get("run_url") else f"**Last checked:** {checkin['checked_at']}",
        "",
        f"**{stats['repositories']} repositories · fleet average {stats['average']} · {grades}**",
        "",
        "| Repository | Grade | Score | " + " | ".join(LABELS.values()) + " |",
        "|---|---|---|" + "---|" * len(LABELS),
    ]
    ranked = sorted(records, key=lambda record: (-record["score"], record["name"].lower()))
    for record in ranked:
        cells = [points_cell(record["checks"][key]) for key in LABELS]
        lines.append(
            f"| [{cell(record['name'])}]({record['url']}) | {record['grade']} | "
            f"{record['score']} | " + " | ".join(cells) + " |"
        )

    lines += ["", "## Where points were lost", ""]
    losing = [
        record for record in ranked
        if any(item["details"] for item in record["checks"].values())
    ]
    if not losing:
        lines.append("Nothing: every repository earned every point.")
    for record in losing:
        lines.append(f"### {record['name']} · {record['grade']} · {record['score']}")
        lines.append("")
        for key, label in LABELS.items():
            for detail in record["checks"][key]["details"]:
                lines.append(f"- **{label}:** {cell(detail)}")
        lines.append("")

    lines += [
        f"## Rubric {RUBRIC}",
        "",
        "| Check | Max | How it is scored |",
        "|---|---|---|",
    ]
    for key, label in LABELS.items():
        lines.append(f"| {label} | {MAX[key]} | {RUBRIC_TEXT[key]} |")
    release = f" Latest portfolio-ops release: {latest[0]}." if latest else ""
    lines += [
        "",
        (
            "Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, otherwise F. Scores are rounded "
            f"percentages of the points that were checked.{release}"
        ),
        "",
        (
            "Nothing here is self-reported. Workflow and lab results come from the GitHub "
            "Actions API; every other check reads a shallow clone as text and never executes "
            "the repository's code. Private repositories are never listed or cloned, and "
            "commit author addresses are reported as pass or fail, never printed."
        ),
        "",
    ]
    return "\n".join(lines)


def grade_changes(previous: dict[str, Any] | None, records: list[dict[str, Any]]) -> list[str]:
    before = {
        item["name"]: (item["score"], item["grade"])
        for item in (previous or {}).get("repositories", [])
    }
    changes = []
    for record in records:
        old = before.get(record["name"])
        new = (record["score"], record["grade"])
        if old is None:
            changes.append(f"{record['name']} added at {record['grade']} {record['score']}")
        elif old != new:
            changes.append(f"{record['name']} {old[1]} {old[0]} -> {new[1]} {new[0]}")
    for name in sorted(set(before) - {record["name"] for record in records}):
        changes.append(f"{name} removed")
    return changes


def write_outputs(out: Path, owner: str, latest: tuple[str, str] | None,
                  records: list[dict[str, Any]], checkin: dict[str, str]) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "fleet.json"
    previous = None
    if json_path.is_file():
        try:
            previous = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None
    changes = grade_changes(previous, records)
    document = {
        "format": FORMAT,
        "rubric": RUBRIC,
        "owner": owner,
        "checked_at": checkin["checked_at"],
        "run_url": checkin.get("run_url", ""),
        "portfolio_ops_latest": latest[0] if latest else None,
        "summary": summary(records),
        "repositories": records,
    }
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    (out / "README.md").write_text(render_markdown(owner, latest, records, checkin),
                                   encoding="utf-8")
    return changes


def commit_message(changes: list[str]) -> str:
    if not changes:
        return "Fleet scoreboard check-in: no grade changes"
    head = f"Fleet scoreboard: {len(changes)} grade change{'s' if len(changes) != 1 else ''}"
    return head + "\n\n" + "\n".join(f"- {change}" for change in changes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--out", type=Path, default=Path("scoreboard"))
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="where shallow clones go; keep it outside the checkout")
    parser.add_argument("--exclude", action="append", default=[],
                        help="repository name to skip; repeatable")
    parser.add_argument("--offline", action="store_true",
                        help="preview: score clones already in --work-dir, skip the GitHub API")
    parser.add_argument("--message-file", type=Path, default=None,
                        help="write the commit message here")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    now = datetime.now(timezone.utc)
    latest = latest_ops_release(args.owner)
    excluded = set(args.exclude)

    if args.offline:
        repos = [
            {"name": path.name, "default_branch": "", "url": f"https://github.com/{args.owner}/{path.name}"}
            for path in sorted(args.work_dir.iterdir(), key=lambda path: path.name.lower())
            if (path / ".git").exists() and path.name not in excluded
        ]
    else:
        repos = [repo for repo in list_public_repos(args.owner, token) if repo["name"] not in excluded]
    if not repos:
        raise SystemExit("fleet scoreboard found no public repositories; scoreboard left untouched")

    records = []
    for repo in repos:
        root = args.work_dir / repo["name"]
        if args.offline:
            runs, jobs_for = None, None
        else:
            clone(args.owner, repo["name"], root)
            runs = default_branch_runs(args.owner, repo["name"], repo["default_branch"], token)

            def jobs_for(run_id: int, name: str = repo["name"]) -> list[dict[str, Any]]:
                return run_jobs(args.owner, name, run_id, token)

        records.append(score_repository(args.owner, repo, root, runs, latest, now, jobs_for))
        print(f"{repo['name']}: {records[-1]['grade']} {records[-1]['score']}")

    server = os.environ.get("GITHUB_SERVER_URL", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    checkin = {
        "checked_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if server and run_id else "",
    }
    changes = write_outputs(args.out, args.owner, latest, records, checkin)
    message = commit_message(changes)
    if args.message_file:
        args.message_file.write_text(message + "\n", encoding="utf-8")
    stats = summary(records)
    print(f"fleet scoreboard graded {stats['repositories']} repositories; "
          f"average {stats['average']}; grade changes: {len(changes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
