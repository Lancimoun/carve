"""Repository-release contracts for the public CARVE source clone."""

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"


class WorkflowContract(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_candidate_pushes_and_manual_recovery_are_gated(self):
        self.assertIn("  push:\n", self.text)
        self.assertIn("  pull_request:\n    branches: [main]", self.text)
        self.assertIn("  workflow_dispatch:", self.text)
        self.assertNotIn("push:\n    branches: [main]", self.text)

    def test_token_and_runtime_boundaries_are_explicit(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("timeout-minutes: 5", self.text)

    def test_current_actions_are_immutably_pinned(self):
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            self.text,
        )
        self.assertIn(
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0",
            self.text,
        )

    def test_all_current_supported_python_lines_run_without_installs(self):
        for version in ("3.11", "3.12", "3.13", "3.14"):
            self.assertIn(f'"{version}"', self.text)
        self.assertIn("python -m unittest discover tests -v", self.text)
        self.assertNotIn("pip install", "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("#")
        ))


class ReadmeContract(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text(encoding="utf-8")

    def test_scope_is_static_planning_not_behavioral_equivalence(self):
        self.assertIn("does not prove behavioral equivalence", self.text)
        self.assertNotIn("Prove a refactor changed nothing", self.text)
        self.assertNotIn("CARVE answers both", self.text)

    def test_ast_accepting_helpers_are_documented_as_ast_accepting(self):
        self.assertIn("defined_names(tree)", self.text)
        self.assertIn("imported_names(tree)", self.text)
        self.assertIn("find_chain(tree, func)", self.text)
        self.assertIn("exclusive_helpers", self.text)
        self.assertNotIn("Every function takes source text or a path", self.text)

    def test_the_checked_in_example_runs_and_readme_carries_its_output(self):
        proc = subprocess.run(
            [sys.executable, "-m", "examples.seam_report"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        output = proc.stdout.strip()
        self.assertTrue(output)
        self.assertIn(output, self.text)

    def test_version_marks_the_new_public_module(self):
        from carve import __version__

        self.assertEqual(__version__, "0.2.0")

    def test_package_docstring_does_not_claim_behavioral_proof(self):
        import carve

        self.assertNotIn("prove a refactor changed nothing", carve.__doc__.lower())
        self.assertIn("static", carve.__doc__.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
