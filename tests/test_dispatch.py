"""carve.dispatch — coupling inside a 219-branch if/elif chain.

The reduced reproductions of what actually went wrong analysing a 7,789-line
run_tool. Each test is a bug that shipped, not an illustration.
"""

import unittest

from carve import dispatch

CHAIN = (
    "import os\n"
    "HELPER_STATE = {}\n"
    "def load_memory():\n"
    "    return HELPER_STATE\n"
    "def run_tool(name, inputs):\n"
    "    path = inputs.get('path')\n"
    "    if name == 'list_dir':\n"
    "        return os.listdir(path)\n"
    "    elif name == 'save':\n"
    "        return load_memory()\n"
    "    elif name in ('a', 'b'):\n"
    "        return 1\n"
    "    elif name == 'stateful':\n"
    "        return HELPER_STATE\n"
    "    return None\n"
)


class ChainDiscovery(unittest.TestCase):
    def test_finds_every_branch(self):
        chain = dispatch.find_chain(__import__("ast").parse(CHAIN), "run_tool")
        self.assertEqual(len(chain), 4)

    def test_a_branch_serving_several_names_yields_all_of_them(self):
        # `name in ('a','b')` is ONE branch and TWO tools. Collapsing it to one
        # value silently drops a tool from the inventory -- and the surface
        # contract that should catch that is built from this same inventory.
        free, welded = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertIn("a", free)
        self.assertIn("b", free)

    def test_missing_function_is_not_a_crash(self):
        self.assertEqual(dispatch.classify_chain(CHAIN, "nonexistent"), ([], {}))

    def test_short_unrelated_ifs_are_not_mistaken_for_the_chain(self):
        # A two-branch guard early in the function must not win over the real
        # chain. Getting this wrong is silent: you analyse 2 branches, report
        # them confidently, and never learn the other 216 exist.
        src = (
            "def run_tool(name, inputs):\n"
            "    if name == 'guard':\n"
            "        return 'blocked'\n"
            "    if name == 'x':\n"
            "        return 1\n"
            "    elif name == 'y':\n"
            "        return 2\n"
            "    elif name == 'z':\n"
            "        return 3\n"
        )
        free, welded = dispatch.classify_chain(src, "run_tool")
        self.assertEqual(sorted(free), ["x", "y", "z"])


class FreeVsWelded(unittest.TestCase):
    def test_a_branch_using_only_imports_is_free(self):
        free, _ = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertIn("list_dir", free)

    def test_a_branch_calling_a_module_helper_is_welded(self):
        _, welded = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertEqual(welded["save"], ["load_memory"])

    def test_a_branch_reading_module_state_is_welded(self):
        _, welded = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertEqual(welded["stateful"], ["HELPER_STATE"])

    def test_containing_functions_locals_are_not_dependencies(self):
        # `path` is run_tool's local. A branch reading it is not coupled to the
        # module. Counting it welds every branch to its own function's variables
        # and the analysis reads as "nothing can move" -- which is wrong and,
        # worse, plausible.
        free, welded = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertNotIn("path", welded.get("list_dir", []))
        self.assertIn("list_dir", free)


class TheOrelseTrap(unittest.TestCase):
    """The bug that produced monotonically descending dependency counts.

    ast.walk on an ast.If descends into orelse, which in an if/elif chain is
    every branch below it. Branch #1 inherits the dependencies of all its
    successors; the last inherits none. Counts slide down the chain and look
    like a finding about layering.
    """

    def test_the_first_branch_does_not_inherit_later_branches(self):
        # 'list_dir' precedes branches that use load_memory and HELPER_STATE.
        # If orelse leaked, it would show both and be reported welded.
        free, welded = dispatch.classify_chain(CHAIN, "run_tool")
        self.assertIn("list_dir", free)
        self.assertNotIn("list_dir", welded)

    def test_dependencies_do_not_decrease_with_position(self):
        # The signature of the bug, asserted directly: under the orelse leak the
        # first branch has the most deps and the last has none, strictly.
        free, welded = dispatch.classify_chain(CHAIN, "run_tool")
        counts = [len(welded.get(v, [])) for v in ("list_dir", "save", "a", "stateful")]
        self.assertNotEqual(
            counts, sorted(counts, reverse=True),
            "dependency counts descend monotonically with chain position -- "
            "the walker is measuring position, not dependencies",
        )


class Report(unittest.TestCase):
    def test_report_states_the_movable_fraction(self):
        # 4 branches, 5 targets: `name in ('a','b')` is ONE branch serving TWO
        # tools. You move targets and you delete branches -- reporting one
        # number under the other's label is how an inventory loses a tool, and
        # the surface contract that should catch that is built from this same
        # inventory.
        out = dispatch.report(CHAIN, "run_tool")
        self.assertIn("run_tool(): 5 dispatch targets", out)
        self.assertIn("across 4 branches", out)
        self.assertRegex(out, r"free\s+3\s+\(60%\)")

    def test_no_chain_says_so_rather_than_dividing_by_zero(self):
        out = dispatch.report("def f(x):\n    return x\n", "f")
        self.assertIn("no dispatch chain found", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
