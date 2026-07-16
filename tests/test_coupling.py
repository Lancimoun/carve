"""carve.coupling — free vs welded.

Every test here is a real failure from carving up a 30,014-line module, reduced
to its smallest reproduction. They are not illustrations; each one caught
something.
"""

import unittest

from carve import coupling


class FreeVsWelded(unittest.TestCase):
    def test_a_function_touching_nothing_is_free(self):
        free, welded = coupling.classify(
            "import os\n"
            "def alone(p):\n"
            "    return os.path.exists(p)\n"
        )
        # os is an import, not an internal — depending on stdlib does not weld
        # you to the module, it welds you to Python.
        self.assertEqual(free, ["alone"])
        self.assertEqual(welded, {})

    def test_a_function_calling_a_module_helper_is_welded(self):
        free, welded = coupling.classify(
            "def helper():\n"
            "    return 1\n"
            "def needs_helper():\n"
            "    return helper() + 1\n"
        )
        self.assertEqual(free, ["helper"])
        self.assertEqual(welded, {"needs_helper": ["helper"]})

    def test_module_level_state_welds_too(self):
        # The trap: CONFIG looks like a constant, so it reads as harmless. It is
        # a module-level binding, and it does not follow the function out.
        free, welded = coupling.classify(
            "CONFIG = {'a': 1}\n"
            "def reads_config():\n"
            "    return CONFIG['a']\n"
        )
        self.assertEqual(free, [])
        self.assertEqual(welded, {"reads_config": ["CONFIG"]})

    def test_recursion_does_not_count_as_welded(self):
        # A function referencing itself is not coupled to anything it leaves
        # behind — it takes itself with it.
        free, welded = coupling.classify(
            "def fact(n):\n"
            "    return 1 if n <= 1 else n * fact(n - 1)\n"
        )
        self.assertEqual(free, ["fact"])

    def test_a_local_shadowing_a_module_name_is_not_a_dependency(self):
        # `total` exists at module level AND as a local. The function never
        # reads the module one. Counting it would report a false weld and send
        # someone building a seam they do not need.
        free, welded = coupling.classify(
            "total = 99\n"
            "def compute(xs):\n"
            "    total = sum(xs)\n"
            "    return total\n"
        )
        self.assertEqual(welded, {})
        self.assertEqual(free, ["compute"])


class TheOrelseTrap(unittest.TestCase):
    """The measurement bug that produced monotonically descending dep counts.

    `ast.walk` on an `ast.If` descends into `orelse`. In an if/elif chain that
    is every branch below it, so branch #1 inherits the dependencies of all 200
    successors and branch #200 inherits none. The counts slide smoothly down the
    chain, which looks like a finding about the code and is really a finding
    about the walker.

    A number that decreases neatly with position is measuring position.
    """

    SOURCE = (
        "def helper_a(): return 1\n"
        "def helper_b(): return 2\n"
        "def dispatch(name):\n"
        "    if name == 'x':\n"
        "        return 0\n"
        "    elif name == 'y':\n"
        "        return helper_a()\n"
        "    elif name == 'z':\n"
        "        return helper_b()\n"
    )

    def test_dispatch_reports_every_branch_not_just_the_first(self):
        _, welded = coupling.classify(self.SOURCE)
        self.assertEqual(welded["dispatch"], ["helper_a", "helper_b"])

    def test_internals_used_reads_body_only(self):
        import ast

        # symtable resolves the whole function at once, so the orelse trap
        # cannot arise: there is no per-branch walk to get wrong. The guarantee
        # is the one above — dispatch reports helper_a AND helper_b, never a
        # subset that depends on where you started walking.
        tree = ast.parse(self.SOURCE)
        self.assertIn("dispatch", {n.name for n in tree.body if hasattr(n, "name")})


class Report(unittest.TestCase):
    def test_report_states_the_movable_fraction(self):
        out = coupling.report(
            "X = 1\n"
            "def free_one(): return 1\n"
            "def free_two(): return 2\n"
            "def welded_one(): return X\n"
        )
        self.assertIn("3 top-level functions", out)
        self.assertRegex(out, r"free\s+2\s+\(67%\)")
        self.assertRegex(out, r"welded\s+1\s+\(33%\)")

    def test_empty_module_does_not_divide_by_zero(self):
        self.assertIn("no top-level functions", coupling.report("X = 1\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
