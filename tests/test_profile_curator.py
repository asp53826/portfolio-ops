from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.profile_curator import END, START, render, replace_block, select_workflow


class ProfileCuratorTests(TestCase):
    def test_prefers_engineering_evidence_over_dependabot_activity(self):
        runs = [
            {
                "name": "github_actions in / for actions/checkout - Update #123",
                "event": "dynamic",
                "path": "dynamic/dependabot/dependabot-updates",
                "actor": {"login": "dependabot[bot]"},
            },
            {
                "name": "CI",
                "event": "push",
                "path": ".github/workflows/ci.yml",
                "actor": {"login": "asp53826"},
            },
        ]

        self.assertEqual(select_workflow(runs), runs[1])

    def test_uses_security_workflow_when_it_is_the_only_engineering_evidence(self):
        runs = [
            {"name": "CodeQL", "event": "push", "actor": {"login": "asp53826"}},
        ]

        self.assertEqual(select_workflow(runs), runs[0])

    def test_omits_workflow_when_only_dependabot_activity_exists(self):
        runs = [
            {"name": "pip update", "event": "dynamic", "actor": {"login": "dependabot[bot]"}},
        ]

        self.assertIsNone(select_workflow(runs))

    def test_renders_verified_release_and_workflow_links(self):
        records = [
            {
                "slug": "asp53826/engine",
                "url": "https://github.com/asp53826/engine",
                "language": "C++",
                "archived": False,
                "release": {"tag": "v1.2.0", "url": "https://example.test/release"},
                "workflow": {
                    "name": "CI",
                    "conclusion": "success",
                    "url": "https://example.test/run",
                },
            }
        ]
        block = render(records)
        self.assertIn("[v1.2.0](https://example.test/release)", block)
        self.assertIn("[CI: success](https://example.test/run)", block)

    def test_replaces_only_the_marked_block(self):
        with TemporaryDirectory() as directory:
            readme = Path(directory) / "README.md"
            readme.write_text(f"before\n{START}\nold\n{END}\nafter\n")
            changed = replace_block(readme, f"{START}\nnew\n{END}")
            self.assertTrue(changed)
            self.assertEqual(readme.read_text(), f"before\n{START}\nnew\n{END}\nafter\n")
