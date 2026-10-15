import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.fleet_scoreboard import (
    classify_ref,
    commit_message,
    evidence_check,
    grade,
    lab_check,
    latest_by_workflow,
    list_public_repos,
    parse_ls_remote,
    privacy_check,
    score_repository,
    supply_chain_check,
    workflows_check,
    write_outputs,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
SHA_A = "a" * 40


def run(path, conclusion="success", event="push", updated="2026-10-14T09:00:00Z", run_id=1,
        name=None):
    return {
        "id": run_id,
        "path": path,
        "name": name or Path(path).stem,
        "event": event,
        "conclusion": conclusion,
        "updated_at": updated,
    }


def write(root: Path, relative: str, text: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def git_repo(root: Path, email: str) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    write(root, "README.md", "# demo\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Demo", "-c", f"user.email={email}",
         "commit", "-q", "-m", "init"],
        check=True,
    )


class RefTests(TestCase):
    def test_classifies_action_references(self):
        self.assertEqual(classify_ref("./.github/actions/x", "me"), "local")
        self.assertEqual(classify_ref("me/portfolio-ops/.github/workflows/a.yml@v1.0.0", "me"),
                         "owned")
        self.assertEqual(classify_ref(f"actions/checkout@{SHA_A}", "me"), "pinned")
        self.assertEqual(classify_ref("actions/checkout@v4", "me"), "unpinned")
        self.assertEqual(classify_ref("docker://alpine:3", "me"), "unpinned")
        self.assertEqual(classify_ref("docker://alpine@sha256:abc", "me"), "pinned")

    def test_latest_release_uses_semver_order_and_peeled_commit(self):
        listing = "\n".join([
            f"{'1' * 40}\trefs/tags/v1.9.0",
            f"{'2' * 40}\trefs/tags/v1.10.0",
            f"{'3' * 40}\trefs/tags/v1.10.0^{{}}",
            f"{'4' * 40}\trefs/tags/nightly",
        ])
        self.assertEqual(parse_ls_remote(listing), ("v1.10.0", "3" * 40))
        self.assertIsNone(parse_ls_remote(""))


class SupplyChainTests(TestCase):
    def test_full_marks_for_pinned_permissioned_current_repository(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml",
                  f"permissions:\n  contents: read\njobs:\n  t:\n    steps:\n"
                  f"      - uses: actions/checkout@{SHA_A} # v4\n")
            write(root, ".github/workflows/lab.yml",
                  "permissions: {}\njobs:\n  m:\n"
                  "    uses: me/portfolio-ops/.github/workflows/repo-medic.yml@v1.3.1\n")
            write(root, ".github/dependabot.yml", "version: 2\n")
            result = supply_chain_check(root, "me", ("v1.3.1", "b" * 40))
        self.assertEqual(result["points"], 20)
        self.assertEqual(result["details"], [])

    def test_reports_each_gap_without_double_counting(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/ci.yml",
                  "jobs:\n  t:\n    steps:\n      - uses: actions/checkout@v4\n"
                  f"      - uses: astral-sh/setup-uv@{SHA_A}\n")
            write(root, ".github/workflows/lab.yml",
                  "permissions:\n  contents: read\njobs:\n  m:\n"
                  "    uses: me/portfolio-ops/.github/workflows/repo-medic.yml@v1.0.0\n")
            result = supply_chain_check(root, "me", ("v1.3.1", "b" * 40))
        # 8 * 1/2 pinned + 6 * 1/2 permissioned + 0 dependabot + 0 stale ops pin
        self.assertEqual(result["points"], 7)
        joined = " ".join(result["details"])
        self.assertIn("actions/checkout@v4", joined)
        self.assertIn("ci.yml", joined)
        self.assertIn("Dependabot", joined)
        self.assertIn("v1.0.0", joined)

    def test_release_commit_sha_counts_as_current(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, ".github/workflows/lab.yml",
                  "permissions: {}\njobs:\n  m:\n"
                  f"    uses: me/portfolio-ops/.github/workflows/repo-medic.yml@{SHA_A}\n")
            write(root, ".github/dependabot.yml", "version: 2\n")
            result = supply_chain_check(root, "me", ("v1.3.1", SHA_A))
        self.assertEqual(result["points"], 20)


class EvidenceTests(TestCase):
    def manifest(self, command: str) -> str:
        return (
            "[[claim]]\n"
            'id = "speed"\n'
            'statement = "runs **3x** faster"\n'
            f'command = "{command}"\n'
            'pattern = "([0-9.]+)x"\n'
            "expected = 3\n"
            "tolerance = 0.1\n"
        )

    def test_missing_manifest_scores_zero(self):
        with TemporaryDirectory() as tmp:
            result = evidence_check(Path(tmp))
        self.assertEqual(result["points"], 0)

    def test_documented_claim_earns_full_marks_without_running_it(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The command would fail if executed; the check must stay string-only.
            write(root, "claims.toml", self.manifest("exit 9"))
            write(root, "README.md", "It runs **3x** faster.\n\n```bash\nexit 9\n```\n")
            result = evidence_check(root)
        self.assertEqual(result["points"], 20)

    def test_undocumented_command_loses_rehearsal_points(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "claims.toml", self.manifest("make bench"))
            write(root, "README.md", "It runs **3x** faster.\n")
            result = evidence_check(root)
        self.assertEqual(result["points"], 10)
        self.assertIn("no README command", result["details"][0])


class PrivacyTests(TestCase):
    def test_noreply_author_passes(self):
        with TemporaryDirectory() as tmp:
            git_repo(Path(tmp), "1+me@users.noreply.github.com")
            self.assertEqual(privacy_check(Path(tmp))["points"], 5)

    def test_public_address_fails_without_being_printed(self):
        with TemporaryDirectory() as tmp:
            git_repo(Path(tmp), "someone@school.edu")
            result = privacy_check(Path(tmp))
        self.assertEqual(result["points"], 0)
        self.assertNotIn("school.edu", json.dumps(result))


class WorkflowTests(TestCase):
    def test_latest_run_per_workflow_skips_noise(self):
        runs = [
            run(".github/workflows/ci.yml", conclusion="cancelled", run_id=5),
            run("dynamic/dependabot/dependabot-updates", conclusion="failure", run_id=4),
            run(".github/workflows/ci.yml", conclusion="success", run_id=3),
            run(".github/workflows/ci.yml", conclusion="failure", run_id=2),
            run(".github/workflows/autonomous-lab.yml", event="schedule", run_id=1),
        ]
        chosen = latest_by_workflow(runs)
        self.assertEqual(list(chosen), [".github/workflows/ci.yml"])
        self.assertEqual(chosen[".github/workflows/ci.yml"]["id"], 3)

    def test_failures_and_stale_successes_lose_points(self):
        runs = [
            run(".github/workflows/ci.yml"),
            run(".github/workflows/pages.yml", conclusion="failure"),
            run(".github/workflows/codeql.yml", event="schedule", updated="2026-07-01T00:00:00Z"),
            run(".github/workflows/release.yml"),
            # An old push run still describes unchanged code, so it keeps full credit.
            run(".github/workflows/build.yml", updated="2026-06-01T00:00:00Z"),
        ]
        result = workflows_check(runs, NOW)
        self.assertEqual(result["points"], 17.5)  # 25 * 3.5 / 5
        self.assertEqual(len(result["details"]), 2)

    def test_no_runs_scores_zero(self):
        self.assertEqual(workflows_check([], NOW)["points"], 0)


class LabTests(TestCase):
    texts: ClassVar[dict[str, str]] = {"autonomous-lab.yml": "on: schedule"}

    def test_not_enrolled(self):
        result = lab_check({}, [], NOW, lambda run_id: [])
        self.assertEqual(result["points"], 0)
        self.assertIn("not enrolled", result["details"][0])

    def test_scores_jobs_from_latest_scheduled_run(self):
        runs = [
            run(".github/workflows/autonomous-lab.yml", event="workflow_run", run_id=9),
            run(".github/workflows/autonomous-lab.yml", event="schedule", run_id=8),
        ]
        jobs = {8: [
            {"name": "repo-medic / verify", "conclusion": "success"},
            {"name": "documentation-scribe / documentation", "conclusion": "failure"},
            {"name": "benchmark-keeper / benchmark", "conclusion": "skipped"},
            {"name": "claim-auditor / audit", "conclusion": "success"},
        ]}
        result = lab_check(self.texts, runs, NOW, lambda run_id: jobs[run_id])
        self.assertEqual(result["points"], 13.3)  # 20 * 2/3
        self.assertEqual(result["details"],
                         ["documentation-scribe failed in the latest scheduled lab run"])

    def test_stale_lab_is_halved(self):
        runs = [run(".github/workflows/autonomous-lab.yml", event="schedule",
                    updated="2026-07-01T00:00:00Z")]
        result = lab_check(self.texts, runs, NOW,
                           lambda run_id: [{"name": "repo-medic / verify", "conclusion": "success"}])
        self.assertEqual(result["points"], 10)
        self.assertIn("schedule may have been disabled", result["details"][0])


class RepositoryListingTests(TestCase):
    def test_only_public_original_active_repositories_are_scored(self):
        page = [
            {"name": "Zeta", "html_url": "u", "default_branch": "main"},
            {"name": "alpha", "html_url": "u", "default_branch": "trunk"},
            {"name": "secret", "html_url": "u", "private": True},
            {"name": "forked", "html_url": "u", "fork": True},
            {"name": "old", "html_url": "u", "archived": True},
            {"name": "internal", "html_url": "u", "visibility": "internal"},
        ]
        repos = list_public_repos("me", "", fetch=lambda url, token: page)
        self.assertEqual([repo["name"] for repo in repos], ["alpha", "Zeta"])
        self.assertEqual(repos[0]["default_branch"], "trunk")


class OutputTests(TestCase):
    def record(self, root: Path) -> dict:
        repo = {"name": "demo", "url": "https://github.com/me/demo"}
        runs = [run(".github/workflows/ci.yml")]
        return score_repository("me", repo, root, runs, None, NOW, lambda run_id: [])

    def test_every_run_checks_in_and_reports_grade_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            git_repo(root, "1+me@users.noreply.github.com")
            out = Path(tmp) / "scoreboard"
            first = write_outputs(out, "me", None, [self.record(root)],
                                  {"checked_at": "2026-10-15 12:00 UTC", "run_url": ""})
            second = write_outputs(out, "me", None, [self.record(root)],
                                   {"checked_at": "2026-10-15 15:00 UTC", "run_url": ""})
            document = json.loads((out / "fleet.json").read_text())
            readme = (out / "README.md").read_text()
        # workflows 25 + lab 0 + evidence 0 + docs 10 + supply chain 17 (no dependabot) + privacy 5
        self.assertEqual(first, ["demo added at F 57"])
        self.assertEqual(second, [])
        self.assertEqual(document["checked_at"], "2026-10-15 15:00 UTC")
        self.assertIn("2026-10-15 15:00 UTC", readme)
        self.assertEqual(commit_message(second), "Fleet scoreboard check-in: no grade changes")
        self.assertTrue(commit_message(first).startswith("Fleet scoreboard: 1 grade change\n"))

    def test_grade_boundaries(self):
        self.assertEqual([grade(score) for score in (100, 90, 89, 80, 70, 60, 59)],
                         ["A", "A", "B", "B", "C", "D", "F"])
