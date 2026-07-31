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

    def test_a_nested_function_is_not_a_top_level_dispatcher(self):
        source = (
            "def wrapper():\n"
            "    def run_tool(name, inputs):\n"
            "        if name == 'a': return 1\n"
            "        elif name == 'b': return 2\n"
            "        elif name == 'c': return 3\n"
            "    return run_tool\n"
        )
        self.assertEqual(dispatch.classify_chain(source, "run_tool"), ([], {}))

    def test_a_nested_longer_chain_cannot_replace_the_real_chain(self):
        source = (
            "def run_tool(name, inputs):\n"
            "    if name == 'outer_a': return 1\n"
            "    elif name == 'outer_b': return 2\n"
            "    elif name == 'outer_c': return 3\n"
            "    def nested(name):\n"
            "        if name == 'inner_a': return 1\n"
            "        elif name == 'inner_b': return 2\n"
            "        elif name == 'inner_c': return 3\n"
            "        elif name == 'inner_d': return 4\n"
            "    return nested\n"
        )
        free, _ = dispatch.classify_chain(source, "run_tool")
        self.assertEqual(sorted(free), ["outer_a", "outer_b", "outer_c"])

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


class ComparisonSemantics(unittest.TestCase):
    """A dispatch target exists only when the comparison actually selects it."""

    def test_not_equal_does_not_invert_into_a_named_target(self):
        src = (
            "def helper(): return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name != 'blocked':\n"
            "        return helper()\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
            "    elif name == 'd':\n"
            "        return 4\n"
        )
        free, welded = dispatch.classify_chain(src, "run_tool")
        self.assertEqual(free, [])
        self.assertEqual(welded, {})
        self.assertNotIn("blocked", welded)

    def test_not_in_and_ordering_comparisons_are_not_dispatch_targets(self):
        src = (
            "def run_tool(name, inputs):\n"
            "    if name not in ('a', 'b'):\n"
            "        return 1\n"
            "    elif name < 'm':\n"
            "        return 2\n"
            "    elif name == 'real':\n"
            "        return 3\n"
            "    elif name == 'also_real':\n"
            "        return 4\n"
            "    elif name == 'third_real':\n"
            "        return 5\n"
        )
        free, _ = dispatch.classify_chain(src, "run_tool")
        self.assertEqual(free, [])

    def test_equal_to_a_tuple_is_not_the_same_as_membership(self):
        src = (
            "def run_tool(name, inputs):\n"
            "    if name == ('invented_a', 'invented_b'):\n"
            "        return 1\n"
            "    elif name == 'real_a':\n"
            "        return 2\n"
            "    elif name == 'real_b':\n"
            "        return 3\n"
            "    elif name == 'real_c':\n"
            "        return 4\n"
        )
        free, _ = dispatch.classify_chain(src, "run_tool")
        self.assertEqual(free, [])

    def test_reversed_equality_is_a_real_dispatch_target(self):
        src = (
            "def run_tool(name, inputs):\n"
            "    if 'a' == name:\n"
            "        return 1\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name in ('c', 'd'):\n"
            "        return 3\n"
        )
        free, _ = dispatch.classify_chain(src, "run_tool")
        self.assertEqual(sorted(free), ["a", "b", "c", "d"])


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

    def test_a_branch_writing_a_declared_global_is_welded(self):
        source = (
            "STATE = {}\n"
            "def run_tool(name, inputs):\n"
            "    global STATE\n"
            "    if name == 'replace':\n"
            "        STATE = {'moved': True}\n"
            "        return STATE\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        _, welded = dispatch.classify_chain(source, "run_tool")
        self.assertEqual(welded["replace"], ["STATE"])

    def test_module_scope_bindings_under_complex_targets_are_welds(self):
        source = (
            "LEFT, RIGHT = (1, 2)\n"
            "if True:\n"
            "    STATE = {}\n"
            "for ITEM in [1]:\n"
            "    pass\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'left':\n"
            "        return LEFT\n"
            "    elif name == 'state':\n"
            "        return STATE\n"
            "    elif name == 'item':\n"
            "        return ITEM\n"
        )
        free, welded = dispatch.classify_chain(source, "run_tool")
        self.assertEqual(free, [])
        self.assertEqual(
            welded,
            {"item": ["ITEM"], "left": ["LEFT"], "state": ["STATE"]},
        )


class TheRecursiveDispatchTrap(unittest.TestCase):
    """A branch calling its own dispatcher is welded, not free.

    The real one, and the worst error this library can make: a FALSE FREE.

    maxima's `web_search` falls back to `run_tool("web_search_live")`. carve
    inherited coupling.classify's rule that a function referencing itself is
    recursion -- true there, because the function takes itself with it -- and
    applied it to branches, where it is exactly backwards. The branch reported
    as movable. It is not: extracting it needs a circular import back to the
    origin or a tool-runner seam.

    A false weld costs a wasted investigation. A false free costs a broken
    extraction, discovered later, by a user.
    """

    # Three branches minimum: find_chain's min_length guard exists so a small
    # unrelated `if` earlier in a function cannot be mistaken for the chain.
    SOURCE = (
        "def helper(): return 1\n"
        "def run_tool(name, inputs):\n"
        "    if name == 'plain':\n"
        "        return 1\n"
        "    elif name == 'other':\n"
        "        return 2\n"
        "    elif name == 'recurses':\n"
        "        return run_tool('plain', inputs)\n"
    )

    def test_a_branch_calling_the_dispatcher_is_welded_to_it(self):
        free, welded = dispatch.classify_chain(self.SOURCE, "run_tool")
        self.assertIn("plain", free)
        self.assertEqual(
            welded.get("recurses"), ["run_tool"],
            "a branch that calls its own dispatcher was reported movable -- "
            "extracting it needs a callback or a circular import",
        )


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
