import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.claim_summary import main, render

REPORT = {
    "claims_checked": 2,
    "claims": [
        {"id": "accuracy", "status": "verified", "detail": "measured 5.6e-16, claim requires exactly 5.6e-16"},
        {"id": "speedup", "status": "failed", "detail": "measured 31, claim requires 49.0 ±10%"},
    ],
    "failures": [
        {"id": "speedup", "status": "failed", "detail": "measured 31, claim requires 49.0 ±10%"},
    ],
}


class RenderTests(TestCase):
    def test_full_report_lists_every_claim(self):
        output = render(REPORT, failures_only=False, run_url=None)
        self.assertIn("2 claims checked, 1 failing", output)
        self.assertIn("`accuracy`", output)
        self.assertIn("`speedup`", output)

    def test_failures_only_omits_passing_claims(self):
        output = render(REPORT, failures_only=True, run_url="https://example.com/run/1")
        self.assertNotIn("`accuracy`", output)
        self.assertIn("`speedup`", output)
        self.assertIn("Evidence: https://example.com/run/1", output)

    def test_pipes_in_detail_do_not_break_the_table(self):
        report = {
            "claims_checked": 1,
            "claims": [{"id": "x", "status": "errored", "detail": "cmd a | b failed"}],
            "failures": [],
        }
        self.assertIn("cmd a \\| b failed", render(report, False, None))

    def test_empty_failure_list_still_renders(self):
        report = {"claims_checked": 1, "claims": [], "failures": []}
        self.assertIn("No claims to report.", render(report, True, None))


class EntryPointTests(TestCase):
    def test_missing_report_does_not_crash_the_workflow(self):
        with TemporaryDirectory() as directory:
            self.assertEqual(main(["--report", str(Path(directory) / "absent.json")]), 0)

    def test_reads_a_written_report(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "claim-audit.json"
            path.write_text(json.dumps(REPORT))
            self.assertEqual(main(["--report", str(path)]), 0)
