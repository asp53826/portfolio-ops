from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.claim_auditor import ManifestError, audit, normalise, to_number

STATEMENT = "Relative error against the analytic formula is 5.6e-16."


def workspace(directory: str, manifest: str, readme: str = STATEMENT) -> tuple[Path, Path]:
    root = Path(directory)
    (root / "README.md").write_text(readme)
    path = root / "claims.toml"
    path.write_text(manifest)
    return root, path


class HelperTests(TestCase):
    def test_normalise_collapses_wrapped_markdown(self):
        self.assertEqual(normalise("flat at roughly\n  1.7-2.4x across"), "flat at roughly 1.7-2.4x across")

    def test_to_number_strips_thousands_separators_and_units(self):
        self.assertEqual(to_number("1,453.2"), 1453.2)
        self.assertEqual(to_number(" 49.0× "), 49.0)
        self.assertEqual(to_number("−0.5"), -0.5)

    def test_to_number_rejects_prose(self):
        with self.assertRaises(ManifestError):
            to_number("about fifty")


class StatementBindingTests(TestCase):
    def test_wrapped_statement_still_matches_the_document(self):
        readme = "Relative error against the\nanalytic formula is 5.6e-16."
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "accuracy"
statement = "{STATEMENT}"
command = "echo 5.6e-16"
pattern = "([0-9.e-]+)"
expected = 5.6e-16
tolerance = 1e-17
''', readme=readme)
            report = audit(root, manifest)
            self.assertTrue(report["ok"], report["failures"])

    def test_removed_statement_drifts_without_running_the_command(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "accuracy"
statement = "{STATEMENT}"
command = "exit 1"
pattern = "([0-9.]+)"
expected = 1.0
''', readme="This README no longer says anything measurable.")
            report = audit(root, manifest)
            self.assertFalse(report["ok"])
            self.assertEqual(report["claims"][0]["status"], "drifted")

    def test_missing_document_is_a_failure(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "accuracy"
statement = "{STATEMENT}"
document = "docs/BENCH.md"
command = "echo 1"
pattern = "([0-9.]+)"
expected = 1.0
''')
            report = audit(root, manifest)
            self.assertEqual(report["claims"][0]["status"], "drifted")
            self.assertIn("does not exist", report["claims"][0]["detail"])

    def test_document_cannot_escape_the_repository_root(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "accuracy"
statement = "{STATEMENT}"
document = "../outside.md"
command = "echo 1"
pattern = "([0-9.]+)"
expected = 1.0
''')
            report = audit(root, manifest)
            self.assertIn("escapes the repository root", report["claims"][0]["detail"])


class MeasurementTests(TestCase):
    def test_pattern_capture_within_tolerance_passes(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "tests"
statement = "{STATEMENT}"
command = "echo '19 passed in 0.42s'"
pattern = "([0-9]+) passed"
expected = 19
''')
            report = audit(root, manifest)
            self.assertTrue(report["ok"], report["failures"])
            self.assertEqual(report["claims"][0]["measured"], 19.0)

    def test_value_outside_tolerance_fails_with_the_measurement(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "tests"
statement = "{STATEMENT}"
command = "echo '17 passed'"
pattern = "([0-9]+) passed"
expected = 19
''')
            report = audit(root, manifest)
            self.assertFalse(report["ok"])
            self.assertEqual(report["claims"][0]["status"], "failed")
            self.assertEqual(report["claims"][0]["measured"], 17.0)

    def test_percentage_tolerance_absorbs_wall_clock_noise(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "speedup"
statement = "{STATEMENT}"
command = "echo 'speedup 53.3x'"
pattern = "speedup ([0-9.]+)x"
expected = 49.0
tolerance_pct = 10
''')
            self.assertTrue(audit(root, manifest)["ok"])

    def test_percentage_tolerance_still_catches_a_real_regression(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "speedup"
statement = "{STATEMENT}"
command = "echo 'speedup 31.0x'"
pattern = "speedup ([0-9.]+)x"
expected = 49.0
tolerance_pct = 10
''')
            self.assertEqual(audit(root, manifest)["claims"][0]["status"], "failed")

    def test_bounds_express_a_shape_claim(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "ratio-flat"
statement = "{STATEMENT}"
command = "echo 'max ratio 2.36'"
pattern = "max ratio ([0-9.]+)"
min = 1.7
max = 2.4
''')
            self.assertTrue(audit(root, manifest)["ok"])

    def test_json_path_reads_nested_output(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "nodes"
statement = "{STATEMENT}"
command = """echo '{{"tape": {{"nodes": [24, 27]}}}}'"""
json_path = "tape.nodes.0"
expected = 24
''')
            report = audit(root, manifest)
            self.assertTrue(report["ok"], report["failures"])

    def test_unmatched_pattern_errors_rather_than_passing(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "tests"
statement = "{STATEMENT}"
command = "echo 'nothing numeric here'"
pattern = "([0-9]+) passed"
expected = 19
''')
            report = audit(root, manifest)
            self.assertEqual(report["claims"][0]["status"], "errored")

    def test_failing_command_is_reported_not_swallowed(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "tests"
statement = "{STATEMENT}"
command = "echo boom >&2; exit 3"
pattern = "([0-9]+)"
expected = 1
''')
            report = audit(root, manifest)
            self.assertEqual(report["claims"][0]["status"], "errored")
            self.assertIn("exited 3", report["claims"][0]["detail"])

    def test_check_only_skips_execution(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "tests"
statement = "{STATEMENT}"
command = "exit 1"
pattern = "([0-9]+)"
expected = 1
''')
            report = audit(root, manifest, execute=False)
            self.assertTrue(report["ok"])
            self.assertFalse(report["commands_executed"])


class ManifestValidationTests(TestCase):
    def assert_rejected(self, manifest: str, message: str):
        with TemporaryDirectory() as directory:
            root, path = workspace(directory, manifest)
            with self.assertRaises(ManifestError) as caught:
                audit(root, path)
            self.assertIn(message, str(caught.exception))

    def test_empty_manifest_is_rejected(self):
        self.assert_rejected("# nothing here\n", "declares no [[claim]] entries")

    def test_missing_manifest_is_rejected(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ManifestError):
                audit(Path(directory), Path(directory) / "absent.toml")

    def test_duplicate_ids_are_rejected(self):
        self.assert_rejected(f'''
[[claim]]
id = "same"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1

[[claim]]
id = "same"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1
''', "duplicate claim id")

    def test_claim_without_a_bound_is_rejected(self):
        self.assert_rejected(f'''
[[claim]]
id = "unbounded"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
''', "declare 'expected', or 'min' and/or 'max'")

    def test_claim_without_an_extractor_is_rejected(self):
        self.assert_rejected(f'''
[[claim]]
id = "unextractable"
statement = "{STATEMENT}"
command = "echo 1"
expected = 1
''', "exactly one of pattern or json_path")

    def test_two_extractors_are_rejected(self):
        self.assert_rejected(f'''
[[claim]]
id = "ambiguous"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
json_path = "a.b"
expected = 1
''', "exactly one of pattern or json_path")

    def test_pattern_needs_exactly_one_capture_group(self):
        self.assert_rejected(f'''
[[claim]]
id = "greedy"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+) of ([0-9]+)"
expected = 1
''', "exactly one capture group")

    def test_expected_cannot_be_combined_with_bounds(self):
        self.assert_rejected(f'''
[[claim]]
id = "confused"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1
max = 5
''', "cannot be combined")

    def test_both_tolerances_are_rejected(self):
        self.assert_rejected(f'''
[[claim]]
id = "confused"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1
tolerance = 1
tolerance_pct = 5
''', "not both")

    def test_missing_required_field_is_rejected(self):
        self.assert_rejected('''
[[claim]]
id = "nameless"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1
''', "'statement' is required")

    def test_only_selects_a_single_claim(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, f'''
[[claim]]
id = "first"
statement = "{STATEMENT}"
command = "echo 1"
pattern = "([0-9]+)"
expected = 1

[[claim]]
id = "second"
statement = "{STATEMENT}"
command = "echo 2"
pattern = "([0-9]+)"
expected = 2
''')
            report = audit(root, manifest, only="second")
            self.assertEqual(report["claims_checked"], 1)
            self.assertEqual(report["claims"][0]["id"], "second")
