"""carve.seam — the shape of a weld, and the order it unlocks.

The count "N welded" is true and nearly useless: it hides that the names a weld
references are wildly unequal in extraction cost. These tests pin that a weld is
sorted by *how each name is carried* (an import-alias is a near-leaf; a shared
function is a real seam), and that the unlock staircase moves the right branches
between tiers when a seam is injected — because a plan built on a miscount sends
the work at a wall, which is the failure CARVE exists to prevent.
"""

import unittest

from carve import seam

# One branch per name-kind, so every tier is exercised:
#   leaf     -> free (no internal names)
#   alias    -> _re     (import-alias)  -> cheap, tier 0
#   const    -> MODEL   (constant)      -> cheap, tier 0
#   data     -> CITIES  (data-literal)  -> cheap, tier 0
#   mem_a/b  -> load_memory (function, used by TWO branches) -> a real seam
#   helper   -> _one_off (function, used by ONE branch)      -> co-moves, tier 2
#   stateful -> _lock   (shared-state)  -> genuine service, tier 3
FIXTURE = (
    "import re\n"
    "_re = re\n"
    "MODEL = 'sonnet'\n"
    "CITIES = {'x': 1}\n"
    "import threading\n"
    "_lock = threading.Lock()\n"
    "def load_memory():\n"
    "    return {}\n"
    "def _one_off(x):\n"
    "    return x + 1\n"
    "def run_tool(name, inputs):\n"
    "    if name == 'leaf':\n"
    "        return inputs['x']\n"
    "    elif name == 'alias':\n"
    "        return _re.findall('a', inputs['s'])\n"
    "    elif name == 'const':\n"
    "        return MODEL\n"
    "    elif name == 'data':\n"
    "        return CITIES['x']\n"
    "    elif name == 'mem_a':\n"
    "        return load_memory()\n"
    "    elif name == 'mem_b':\n"
    "        return load_memory()\n"
    "    elif name == 'helper':\n"
    "        return _one_off(inputs['x'])\n"
    "    elif name == 'stateful':\n"
    "        with _lock:\n"
    "            return 1\n"
    "    return None\n"
)


class Kinds(unittest.TestCase):
    def setUp(self):
        self.kind = seam.classify_kinds(FIXTURE)

    def test_import_alias_is_not_a_real_weld(self):
        # `_re = re` is the exact near-leaf CARVE's resolve lesson is about.
        self.assertEqual(self.kind["_re"], "import-alias")

    def test_constant_and_data_literal_are_told_apart(self):
        self.assertEqual(self.kind["MODEL"], "constant")
        self.assertEqual(self.kind["CITIES"], "data-literal")

    def test_a_lock_is_shared_state_not_a_plain_call(self):
        self.assertEqual(self.kind["_lock"], "shared-state")

    def test_a_function_is_the_real_seam_kind(self):
        self.assertEqual(self.kind["load_memory"], "function")

    def test_a_bare_import_is_classified_as_import(self):
        self.assertEqual(self.kind["re"], "import")


class WeldShape(unittest.TestCase):
    def setUp(self):
        self.shape = seam.weld_shape(FIXTURE, "run_tool")

    def test_free_branch_is_not_in_the_weld(self):
        self.assertNotIn("leaf", self.shape["welded"])
        self.assertEqual(len(self.shape["welded"]), 7)

    def test_names_are_grouped_by_kind(self):
        by_kind = self.shape["by_kind"]
        self.assertEqual(by_kind["import-alias"], [(1, "_re")])
        self.assertEqual(by_kind["data-literal"], [(1, "CITIES")])

    def test_the_shared_function_outweighs_the_single_use_one(self):
        # both are `function`; the group is ordered by refcount, so the seam that
        # matters (load_memory, 2 refs) sorts above the co-mover (_one_off, 1).
        self.assertEqual(self.shape["by_kind"]["function"], [(2, "load_memory"), (1, "_one_off")])

    def test_sizes_report_how_concentrated_the_weld_is(self):
        self.assertEqual(self.shape["sizes"], {1: 7})

    def test_a_chain_with_no_weld_has_an_empty_shape(self):
        free_only = (
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return inputs['x']\n"
            "    elif name == 'b':\n"
            "        return inputs['y']\n"
            "    return None\n"
        )
        self.assertEqual(seam.weld_shape(free_only, "run_tool")["welded"], {})


class UnlockStaircase(unittest.TestCase):
    def test_cheap_carry_branches_are_tier_0_with_no_seam(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertEqual(t["t0"], {"alias", "const", "data"})

    def test_single_use_helper_co_moves_at_tier_2(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertIn("helper", t["t2"])

    def test_shared_function_and_lock_are_the_tier_3_remainder(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertEqual(t["t3"], {"mem_a", "mem_b", "stateful"})
        self.assertEqual(t["services"]["load_memory"], 2)
        self.assertEqual(t["services"]["_lock"], 1)

    def test_injecting_one_seam_moves_its_branches_out_of_the_remainder(self):
        # The whole point: name load_memory as the injected seam and the two
        # branches welded only to it unlock — leaving just the lock behind.
        t = seam.unlock_tiers(FIXTURE, "run_tool", seam={"load_memory"})
        self.assertEqual(t["t1"], {"mem_a", "mem_b"})
        self.assertEqual(t["t3"], {"stateful"})


class Report(unittest.TestCase):
    def test_report_names_the_kinds_and_the_staircase(self):
        text = seam.report(FIXTURE, "run_tool")
        self.assertIn("[import-alias]", text)
        self.assertIn("unlock staircase", text)
        self.assertIn("welded branches", text)

    def test_report_reflects_the_injected_seam(self):
        with_seam = seam.report(FIXTURE, "run_tool", seam={"load_memory"})
        # tier 1 should now carry the two memory branches (cum reaches 5 of 7).
        self.assertIn("tier 1", with_seam)
        self.assertIn("(cum 5)", with_seam)

    def test_no_welded_branches_is_stated_not_crashed(self):
        free_only = (
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return inputs['x']\n"
            "    elif name == 'b':\n"
            "        return inputs['y']\n"
            "    return None\n"
        )
        self.assertIn("no welded branches", seam.report(free_only, "run_tool"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
