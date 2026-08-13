from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.docs_scribe import audit


class DocsScribeTests(TestCase):
    def test_accepts_existing_local_target_and_ignores_external_url(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("guide")
            (root / "README.md").write_text(
                "[guide](docs/guide.md) [site](https://example.com)"
            )
            report = audit(root)
            self.assertTrue(report["ok"])
            self.assertEqual(report["local_links_checked"], 1)

    def test_reports_missing_and_escaping_targets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("[missing](no.md) [escape](../outside.md)")
            report = audit(root)
            self.assertFalse(report["ok"])
            self.assertEqual(len(report["broken"]), 2)
