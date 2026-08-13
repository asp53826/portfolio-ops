from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.profile_curator import END, START, render, replace_block


class ProfileCuratorTests(TestCase):
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
