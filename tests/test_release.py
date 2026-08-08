"""Repository-release contracts for the public CARVE source clone."""

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
README = ROOT / "README.md"


class ReadmeDocumentsTheWholePublicAPI(unittest.TestCase):
    """A README can be entirely true and still omit a third of the library.

    Every other README check here verifies that what IS written is correct. None
    asked whether anything was MISSING — and by the time this was written, two
    public functions had shipped undocumented: `seam.cluster_closure` and
    `dispatch.find_function`. Both were reachable, both were tested, and a reader
    of the API table would never learn they existed.

    That is the same completeness blind spot as a checker that validates the rows
    it is given and never asks whether the rows are all of them. So this discovers
    the public surface from the SOURCE rather than from a list: a function added
    six months from now is covered the day it is written, without anyone
    remembering to extend a fixture.
    """

    MODULES = ("coupling", "dispatch", "resolve", "seam")

    def test_every_public_function_appears_in_the_readme(self):
        import ast

        readme = README.read_text(encoding="utf-8")
        missing = []
        for module in self.MODULES:
            source = (ROOT / "carve" / f"{module}.py").read_text(encoding="utf-8")
            for node in ast.parse(source).body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                if f"{node.name}(" not in readme:
                    missing.append(f"{module}.{node.name}")
        self.assertEqual(
            missing, [],
            "public function(s) absent from the README API table: %s. A reader "
            "cannot use what the documentation does not mention." % ", ".join(missing))

    def test_the_detector_would_actually_notice_an_omission(self):
        """Both directions. The check above passes on a complete README, which is
        exactly what a check looking at the wrong thing also does."""
        readme = README.read_text(encoding="utf-8")
        self.assertNotIn("definitely_not_a_real_carve_function(", readme)


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
        # Both ends of this pipe are pinned to UTF-8 on purpose.
        #
        # The example's output contains an em-dash, and the README is read as
        # UTF-8 above. Left to their defaults, the child encodes stdout with
        # `locale.getpreferredencoding()` and the parent decodes with it too --
        # so on Windows both sides silently become cp1252, and whether they
        # agree depends on the operator's ambient `PYTHONIOENCODING`. With it
        # set to utf-8 the child emits UTF-8 while the parent decodes cp1252,
        # and the em-dash arrives as 'â€”': a diff of thousands of characters
        # reporting that a correct README does not match its correct example.
        #
        # This suite is the proof behind a public "144 tests, no installs"
        # claim. A test whose verdict depends on an environment variable is not
        # proof of anything, so neither side is left to the environment.
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, "-m", "examples.seam_report"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
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
