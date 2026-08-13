# Portfolio Ops

Autonomous maintenance workflows for a public engineering portfolio. The bots
run project-owned verification commands, preserve benchmark evidence, maintain
dependencies, check documentation and demos, and report verified GitHub
achievement changes.

The system is deliberately boring about authorship: automation commits as
`github-actions[bot]`, never as the repository owner. It does not manufacture
commits, stars, follows, reviews, issues, pull requests, or community answers.

## Bot roster

| bot | responsibility | mutation policy |
|---|---|---|
| Repo Medic | re-run a repository's real tests and build commands | read-only |
| Benchmark Keeper | run declared benchmarks and retain logs plus runner metadata | artifact upload only |
| Dependency Caretaker | process Dependabot updates | patch releases may auto-merge after required checks |
| Documentation Scribe | validate repository-local documentation links | one tracked issue on failure |
| Demo Watchtower | probe explicitly listed public endpoints | one tracked issue on failure |
| Release Steward | verify a tag, package source, checksum and attest it | publishes only for an explicit `v*` tag |
| Achievement Scout | detect changes on the owner's public achievement page | updates local state and opens one notification issue |

## Consumer example

```yaml
name: Autonomous Engineering Lab

on:
  schedule:
    - cron: "23 6 * * 2"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  medic:
    uses: asp53826/portfolio-ops/.github/workflows/repo-medic.yml@v1.0.0
    with:
      runtime: python
      install-command: python -m pip install -e '.[dev]'
      test-command: python -m pytest -q
```

Consumer workflows should pin a release tag or commit. Commands are declared
in each project so the automation cannot silently replace a repository's own
definition of correctness.

## Operating rules

- Permissions are denied by default and granted per job.
- Pull-request automation never checks out or executes Dependabot PR code.
- Major and minor dependency upgrades remain human-reviewed.
- Failures are deduplicated into a single issue and closed on recovery.
- Benchmarks retain commands, commits, runner identity and raw output.
- External repositories are read-only unless a human explicitly chooses to
  submit a contribution.

See [BOT_POLICY.md](BOT_POLICY.md) for the complete boundary.

## Local verification

```bash
python3 -m unittest discover -s tests -v
python3 scripts/docs_scribe.py --root . --report /tmp/docs-report.json
```

## License

MIT
