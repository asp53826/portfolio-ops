from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.quickstart_rehearsal import (RehearsalError, documented_commands, normalise,
                                          rehearse)

VERIFY = """# demo

## Verify it

```bash
make test
make proof-test PROOF_COUNT=300 PROOF_VARS=120
```

## Something else

```bash
make unrelated
```
"""


def workspace(directory: str, manifest: str, readme: str = VERIFY) -> tuple[Path, Path]:
    root = Path(directory)
    (root / "README.md").write_text(readme)
    path = root / "claims.toml"
    path.write_text(manifest)
    return root, path


def claim(cid: str, command: str, extra: str = "") -> str:
    return f'''
[[claim]]
id = "{cid}"
statement = "irrelevant here"
command = "{command}"
pattern = "([0-9]+)"
expected = 1
{extra}
'''


class NormaliseTests(TestCase):
    def test_collapses_line_continuations(self):
        self.assertEqual(normalise("make test \\\n  --flag"), "make test --flag")

    def test_strips_shell_prompt_and_trailing_comment(self):
        self.assertEqual(normalise("$ make test    # 19 tests"), "make test")

    def test_collapses_repeated_whitespace(self):
        self.assertEqual(normalise("make   test\t\tnow"), "make test now")


class ExtractionTests(TestCase):
    def test_scopes_to_the_named_section(self):
        offered = documented_commands(VERIFY, ["Verify it"])
        self.assertIn("make test", offered)
        self.assertNotIn("make unrelated", offered)

    def test_unscoped_search_sees_every_block(self):
        self.assertIn("make unrelated", documented_commands(VERIFY, None))

    def test_conjunctions_are_split_into_parts(self):
        offered = documented_commands("```bash\nmake drat-trim && make proof-test\n```", None)
        self.assertIn("make drat-trim && make proof-test", offered)
        self.assertIn("make drat-trim", offered)
        self.assertIn("make proof-test", offered)

    def test_non_shell_languages_are_ignored(self):
        offered = documented_commands("```python\nmake test\n```", None)
        self.assertEqual(offered, set())

    def test_tilde_fences_and_console_prompts(self):
        offered = documented_commands("~~~console\n$ make test\n~~~", None)
        self.assertIn("make test", offered)


class RehearsalTests(TestCase):
    def test_documented_command_passes(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make test"))
            report = rehearse(root, manifest, headings=["Verify it"])
            self.assertTrue(report["ok"], report["failures"])

    def test_undocumented_command_fails(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make proof-test"))
            report = rehearse(root, manifest, headings=["Verify it"])
            self.assertFalse(report["ok"])
            self.assertEqual(report["claims"][0]["status"], "undocumented")

    def test_the_cdcl_sat_defect_is_caught(self):
        """The README said `make proof-test`; the table needed different parameters."""
        readme = "# d\n\n## Verify it\n\n```bash\nmake drat-trim && make proof-test\n```\n"
        with TemporaryDirectory() as directory:
            root, manifest = workspace(
                directory, claim("proofs", "make proof-test PROOF_COUNT=300 PROOF_VARS=120"),
                readme=readme)
            report = rehearse(root, manifest, headings=["Verify it"])
            self.assertFalse(report["ok"])
            self.assertIn("PROOF_COUNT=300", report["claims"][0]["detail"])

    def test_wrapped_documented_command_still_matches(self):
        readme = "# d\n\n## Verify it\n\n```bash\nmake proof-test \\\n  PROOF_COUNT=300\n```\n"
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make proof-test PROOF_COUNT=300"),
                                       readme=readme)
            self.assertTrue(rehearse(root, manifest, headings=["Verify it"])["ok"])

    def test_exemption_requires_a_reason_and_is_reported_separately(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(
                directory,
                claim("internal", "python -c 'print(1)'",
                      extra='undocumented_reason = "internal probe, not a reader-facing step"'))
            report = rehearse(root, manifest, headings=["Verify it"])
            self.assertTrue(report["ok"])
            self.assertEqual(report["claims"][0]["status"], "exempt")

    def test_blank_exemption_reason_is_rejected(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory,
                                       claim("x", "make thing", extra='undocumented_reason = "  "'))
            with self.assertRaises(RehearsalError):
                rehearse(root, manifest, headings=["Verify it"])

    def test_renamed_reproduction_section_is_a_failure_not_a_pass(self):
        readme = "# d\n\n## How to check\n\n```bash\nmake test\n```\n"
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make test"), readme=readme)
            with self.assertRaises(RehearsalError) as caught:
                rehearse(root, manifest, headings=["Verify it"])
            self.assertIn("missing or renamed", str(caught.exception))

    def test_missing_document_is_rejected(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make test"))
            with self.assertRaises(RehearsalError):
                rehearse(root, manifest, document="ABSENT.md")

    def test_document_cannot_escape_the_repository_root(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, claim("t", "make test"))
            with self.assertRaises(RehearsalError):
                rehearse(root, manifest, document="../outside.md")

    def test_empty_manifest_is_rejected(self):
        with TemporaryDirectory() as directory:
            root, manifest = workspace(directory, "# no claims\n")
            with self.assertRaises(RehearsalError):
                rehearse(root, manifest)
