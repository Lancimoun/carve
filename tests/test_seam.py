"""carve.seam — the shape of a weld, and the order it unlocks.

The count "N welded" is true and nearly useless: it hides that the names a weld
references are wildly unequal in extraction cost. These tests pin that a weld is
sorted by *how each name is carried* (an import-alias is a near-leaf; a shared
function is a real seam), and that the unlock staircase moves the right branches
between tiers when a seam is injected — because a plan built on a miscount sends
the work at a wall, which is the failure CARVE exists to prevent.
"""

import ast
import unittest
from unittest import mock

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

    def test_only_an_alias_rooted_in_an_import_is_an_import_alias(self):
        source = (
            "import re\n"
            "_re = re\n"
            "def backend(): return 1\n"
            "BACKEND = backend\n"
        )
        kind = seam.classify_kinds(source)
        self.assertEqual(kind["_re"], "import-alias")
        self.assertNotIn(kind["BACKEND"], seam.CHEAP_KINDS)

    def test_a_rebound_import_name_is_not_still_an_import_origin(self):
        source = (
            "import re\n"
            "import threading\n"
            "re = threading.Lock()\n"
            "ALIAS = re\n"
            "CLIENT = re.acquire\n"
        )
        kind = seam.classify_kinds(source)
        self.assertEqual(kind["re"], "shared-state")
        self.assertNotIn(kind["ALIAS"], seam.CHEAP_KINDS)
        self.assertNotIn(kind["CLIENT"], seam.CHEAP_KINDS)

    def test_project_relative_re_compile_is_not_a_stdlib_regex_constant(self):
        source = (
            "from . import re\n"
            "PATTERN = re.compile('a')\n"
        )
        kind = seam.classify_kinds(source)
        self.assertNotIn(kind["PATTERN"], seam.CHEAP_KINDS)

    def test_aliases_of_live_module_values_stay_noncheap(self):
        source = (
            "import threading\n"
            "LOCK = threading.Lock()\n"
            "LOCK_ALIAS = LOCK\n"
            "CLIENT = LOCK.acquire\n"
        )
        kind = seam.classify_kinds(source)
        self.assertNotIn(kind["LOCK_ALIAS"], seam.CHEAP_KINDS)
        self.assertNotIn(kind["CLIENT"], seam.CHEAP_KINDS)

    def test_an_indirect_constant_is_not_a_syntactic_literal(self):
        source = (
            "MODEL = 'sonnet'\n"
            "MODEL_ALIAS = MODEL\n"
        )
        kind = seam.classify_kinds(source)
        self.assertEqual(kind["MODEL"], "constant")
        self.assertNotIn(kind["MODEL_ALIAS"], seam.CHEAP_KINDS)

    def test_a_container_is_cheap_only_when_literal_eval_proves_it(self):
        source = (
            "def make(): return 1\n"
            "STATIC = {'x': [1, 2]}\n"
            "DYNAMIC = [make()]\n"
        )
        kind = seam.classify_kinds(source)
        self.assertEqual(kind["STATIC"], "data-literal")
        self.assertNotIn(kind["DYNAMIC"], seam.CHEAP_KINDS)

    def test_only_re_compile_is_a_regex_constant(self):
        source = (
            "import re\n"
            "from re import compile as regex_compile\n"
            "def pattern(): return 'a'\n"
            "PATTERN_A = re.compile('a')\n"
            "PATTERN_B = regex_compile('b')\n"
            "PATTERN_DYNAMIC = re.compile(pattern())\n"
            "HANDLE = builder.compile()\n"
            "CODE = compile('1 + 1', '<test>', 'eval')\n"
        )
        kind = seam.classify_kinds(source)
        self.assertEqual(kind["PATTERN_A"], "regex-const")
        self.assertEqual(kind["PATTERN_B"], "regex-const")
        self.assertNotIn(kind["PATTERN_DYNAMIC"], seam.CHEAP_KINDS)
        self.assertNotIn(kind["HANDLE"], seam.CHEAP_KINDS)
        self.assertNotIn(kind["CODE"], seam.CHEAP_KINDS)


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

    def test_targets_and_physical_branches_are_not_conflated(self):
        source = (
            "def _helper(): return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name in ('a', 'b'):\n"
            "        return _helper()\n"
            "    elif name == 'c':\n"
            "        return 3\n"
            "    elif name == 'd':\n"
            "        return 4\n"
        )
        shape = seam.weld_shape(source, "run_tool")
        self.assertEqual(set(shape["welded"]), {"a", "b"})
        self.assertEqual(shape["branch_count"], 1)
        self.assertEqual(shape["by_kind"]["function"], [(1, "_helper")])


class UnlockStaircase(unittest.TestCase):
    def test_cheap_carry_branches_are_tier_0_with_no_seam(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertEqual(t["t0"], {"alias", "const", "data"})

    def test_private_helper_needs_explicit_external_exclusivity_evidence(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertNotIn("helper", t["t2"])
        self.assertIn("helper", t["t3"])

    def test_externally_confirmed_single_use_helper_co_moves_at_tier_2(self):
        t = seam.unlock_tiers(
            FIXTURE,
            "run_tool",
            exclusive_helpers={"_one_off"},
        )
        self.assertIn("helper", t["t2"])

    def test_shared_function_and_lock_are_the_tier_3_remainder(self):
        t = seam.unlock_tiers(FIXTURE, "run_tool")
        self.assertEqual(t["t3"], {"helper", "mem_a", "mem_b", "stateful"})
        self.assertEqual(t["services"]["load_memory"], 2)
        self.assertEqual(t["services"]["_lock"], 1)
        self.assertEqual(t["services"]["_one_off"], 1)

    def test_injecting_one_seam_moves_its_branches_out_of_the_remainder(self):
        # The whole point: name load_memory as the injected seam and the two
        # branches welded only to it unlock — leaving just the lock behind.
        t = seam.unlock_tiers(FIXTURE, "run_tool", seam={"load_memory"})
        self.assertEqual(t["t1"], {"mem_a", "mem_b"})
        self.assertEqual(t["t3"], {"helper", "stateful"})

    def test_a_confirmed_helpers_transitive_dependency_can_be_the_seam(self):
        source = (
            "def service(): return 1\n"
            "def _helper(): return service()\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a': return _helper()\n"
            "    elif name == 'b': return 2\n"
            "    elif name == 'c': return 3\n"
        )
        tiers = seam.unlock_tiers(
            source,
            "run_tool",
            seam={"service"},
            exclusive_helpers={"_helper"},
        )
        self.assertEqual(tiers["t2"], {"a"})
        self.assertEqual(tiers["t3"], set())

    def test_recursive_dispatcher_is_never_a_single_use_helper(self):
        source = (
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return run_tool('b', inputs)\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        tiers = seam.unlock_tiers(source, "run_tool")
        self.assertEqual(tiers["t2"], set())
        self.assertEqual(tiers["t3"], {"a"})
        self.assertEqual(tiers["services"]["run_tool"], 1)

    def test_public_single_use_function_is_not_assumed_to_co_move(self):
        source = (
            "def helper(): return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return helper()\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        tiers = seam.unlock_tiers(source, "run_tool")
        self.assertEqual(tiers["t2"], set())
        self.assertEqual(tiers["t3"], {"a"})

    def test_private_helper_used_elsewhere_is_not_assumed_to_co_move(self):
        source = (
            "def _helper(): return 1\n"
            "def still_here(): return _helper()\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return _helper()\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        tiers = seam.unlock_tiers(source, "run_tool")
        self.assertEqual(tiers["t2"], set())
        self.assertEqual(tiers["t3"], {"a"})

    def test_helper_with_its_own_live_dependency_is_not_unlocked(self):
        source = (
            "import threading\n"
            "_lock = threading.Lock()\n"
            "def _helper():\n"
            "    with _lock:\n"
            "        return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return _helper()\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        tiers = seam.unlock_tiers(
            source,
            "run_tool",
            exclusive_helpers={"_helper"},
        )
        self.assertEqual(tiers["t2"], set())
        self.assertEqual(tiers["t3"], {"a"})
        self.assertEqual(tiers["services"]["_helper"], 1)

    def test_helper_defaults_and_decorators_are_definition_time_dependencies(self):
        for helper in (
            (
                "DEFAULT = object()\n"
                "def _helper(value=DEFAULT):\n"
                "    return value\n"
            ),
            (
                "def decorate(fn): return fn\n"
                "@decorate\n"
                "def _helper():\n"
                "    return 1\n"
            ),
        ):
            with self.subTest(helper=helper):
                source = (
                    helper
                    + "def run_tool(name, inputs):\n"
                    "    if name == 'a':\n"
                    "        return _helper()\n"
                    "    elif name == 'b':\n"
                    "        return 2\n"
                    "    elif name == 'c':\n"
                    "        return 3\n"
                )
                tiers = seam.unlock_tiers(
                    source,
                    "run_tool",
                    exclusive_helpers={"_helper"},
                )
                self.assertEqual(tiers["t2"], set())
                self.assertEqual(tiers["t3"], {"a"})

    def test_helper_that_writes_module_state_is_not_unlocked(self):
        source = (
            "STATE = {}\n"
            "def _helper():\n"
            "    global STATE\n"
            "    STATE = {'moved': True}\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a':\n"
            "        return _helper()\n"
            "    elif name == 'b':\n"
            "        return 2\n"
            "    elif name == 'c':\n"
            "        return 3\n"
        )
        tiers = seam.unlock_tiers(
            source,
            "run_tool",
            exclusive_helpers={"_helper"},
        )
        self.assertEqual(tiers["t2"], set())
        self.assertEqual(tiers["t3"], {"a"})

    def test_one_private_self_contained_physical_helper_co_moves(self):
        source = (
            "def _helper(): return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name in ('a', 'b'):\n"
            "        return _helper()\n"
            "    elif name == 'c':\n"
            "        return 3\n"
            "    elif name == 'd':\n"
            "        return 4\n"
        )
        tiers = seam.unlock_tiers(
            source,
            "run_tool",
            exclusive_helpers={"_helper"},
        )
        self.assertEqual(tiers["t2"], {"a", "b"})
        self.assertEqual(tiers["t3"], set())

    def test_seam_rejects_ambiguous_or_unknown_input(self):
        with self.assertRaises(TypeError):
            seam.unlock_tiers(FIXTURE, "run_tool", seam="load_memory")
        with self.assertRaises(TypeError):
            seam.unlock_tiers(FIXTURE, "run_tool", seam={"load_memory", 3})
        with self.assertRaises(ValueError):
            seam.unlock_tiers(FIXTURE, "run_tool", seam={"typo"})
        with self.assertRaises(TypeError):
            seam.unlock_tiers(
                FIXTURE,
                "run_tool",
                exclusive_helpers="_one_off",
            )
        with self.assertRaises(ValueError):
            seam.unlock_tiers(
                FIXTURE,
                "run_tool",
                exclusive_helpers={"_missing"},
            )


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

    def test_tied_services_render_in_count_then_name_order(self):
        source = (
            "def service_b(): return 1\n"
            "def service_a(): return 1\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'a': return service_b()\n"
            "    elif name == 'b': return service_a()\n"
            "    elif name == 'c': return service_b()\n"
            "    elif name == 'd': return service_a()\n"
            "    elif name == 'e': return 5\n"
        )
        text = seam.report(source, "run_tool")
        self.assertIn("2x service_a, 2x service_b", text)


class ClusterClosure(unittest.TestCase):
    """The direct dependency count answers "what does this touch". The number you
    plan with is "what must MOVE TOGETHER", and on real code they differ by an
    order of magnitude -- which is how a week of work gets scheduled as an
    afternoon. This was written after a queue entry read "+2 helpers" and the
    honest figure was fifteen: the helper pulled a whole subsystem behind it.
    """

    CHAIN = (
        "def _deep(): return 1\n"
        "def _mid(): return _deep()\n"
        "def _top(): return _mid()\n"
        "def _lonely(): return 2\n"
        "def run_tool(name, inputs):\n"
        "    if name == 'shallow':\n"
        "        return _lonely()\n"
        "    elif name == 'deep':\n"
        "        return _top()\n"
        # a third branch: find_chain requires min_length=3 before it will call a
        # run of elifs a dispatch chain at all, so a two-branch fixture is
        # classified as nothing and every assertion below silently passes on {}.
        "    elif name == 'free':\n"
        "        return inputs['x']\n"
        "    return None\n"
    )

    def test_closure_follows_the_chain_not_just_the_first_hop(self):
        self.assertEqual(seam.cluster_closure(self.CHAIN, {"_top"}), {"_mid", "_deep"})

    def test_closure_excludes_the_seeds_so_len_is_the_extra_cost(self):
        self.assertNotIn("_top", seam.cluster_closure(self.CHAIN, {"_top"}))

    def test_a_self_contained_helper_has_an_empty_closure(self):
        self.assertEqual(seam.cluster_closure(self.CHAIN, {"_lonely"}), set())

    def test_cluster_cost_separates_direct_from_transitive(self):
        cost = seam.cluster_cost(self.CHAIN, "run_tool")
        # 'deep' touches ONE name directly and drags THREE definitions along.
        self.assertEqual(cost["deep"]["direct"], 1)
        self.assertEqual(cost["deep"]["closure"], 3)
        # 'shallow' is the honest one-file move the other only looks like.
        self.assertEqual(cost["shallow"]["direct"], 1)
        self.assertEqual(cost["shallow"]["closure"], 1)

    def test_recursion_terminates(self):
        source = (
            "def _a(): return _b()\n"
            "def _b(): return _a()\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'x':\n"
            "        return _a()\n"
            "    return None\n"
        )
        self.assertEqual(seam.cluster_closure(source, {"_a"}), {"_b"})

    def test_imports_are_not_counted_as_cluster_members(self):
        # Only names the module DEFINES have to move; imports follow you.
        source = (
            "import os\n"
            "def _uses_import(): return os.getcwd()\n"
            "def run_tool(name, inputs):\n"
            "    if name == 'x':\n"
            "        return _uses_import()\n"
            "    return None\n"
        )
        self.assertEqual(seam.cluster_closure(source, {"_uses_import"}), set())

    # --- the parse-once optimisation -------------------------------------
    # These pin the two things a speedup can quietly break: the ANSWER, and the
    # property that bought the speed. Timing is not asserted -- a wall-clock
    # assertion is flaky on shared runners and would not say *why* it got slow.
    # Counting parses does, and it fails the moment someone reintroduces the
    # per-target re-parse.

    def test_passing_a_tree_gives_the_identical_answer(self):
        tree = ast.parse(self.CHAIN)
        self.assertEqual(
            seam.cluster_closure(self.CHAIN, {"_top"}, tree=tree),
            seam.cluster_closure(self.CHAIN, {"_top"}),
        )

    def test_cluster_cost_parses_the_source_a_bounded_number_of_times(self):
        """The regression that cost 46 s: one parse PER WELDED TARGET."""
        real, calls = ast.parse, []

        def counting(src, *a, **k):
            calls.append(len(src) if isinstance(src, str) else 0)
            return real(src, *a, **k)

        with mock.patch.object(seam.ast, "parse", counting):
            cost = seam.cluster_cost(self.CHAIN, "run_tool")

        # three welded/free targets in the fixture, and the module must be read
        # a fixed number of times regardless -- not once per target.
        self.assertGreaterEqual(len(cost), 2)
        self.assertLessEqual(
            len(calls), 2,
            f"cluster_cost parsed the module {len(calls)} times for "
            f"{len(cost)} targets; it must parse once and reuse the tree",
        )


class ValueCarryRiskTests(unittest.TestCase):
    """A closure of zero says no CODE comes along. It says nothing about whether
    the VALUE survives being copied — and that gap ships silently."""

    SOURCE = (
        "import os\n"
        "from pathlib import Path\n"
        "BASE = Path(__file__).parent\n"
        "DATA = BASE / 'data'\n"
        "FILE = DATA / 'x.json'\n"
        "TZ = os.getenv('TZ', 'UTC')\n"
        "if os.getenv('OVERRIDE'):\n"
        "    TZ = 'Asia/Manila'\n"
        "FEEDS = ['a', 'b']\n"
        "LIMIT = 5\n"
    )

    def risk(self, *names):
        return seam.value_carry_risk(self.SOURCE, names)

    def test_a_plain_literal_is_safe_to_carry(self):
        self.assertEqual(self.risk("FEEDS", "LIMIT"), {"FEEDS": [], "LIMIT": []})

    def test_a_file_relative_value_is_flagged(self):
        self.assertTrue(any("file-relative" in r for r in self.risk("BASE")["BASE"]))

    def test_risk_propagates_through_one_hop(self):
        # DATA = BASE / 'data' contains no __file__ of its own.
        reasons = self.risk("DATA")["DATA"]
        self.assertTrue(any("file-relative" in r and "via BASE" in r for r in reasons))

    def test_risk_propagates_through_two_hops(self):
        """The regression that mattered: the first version stopped at one hop and
        called a two-hop file-relative path safe."""
        reasons = self.risk("FILE")["FILE"]
        self.assertTrue(reasons, "FILE derives from BASE via DATA and must not read as safe")
        self.assertTrue(any("file-relative" in r for r in reasons))

    def test_a_conditionally_rebound_name_is_flagged(self):
        # Bound once at module level, then again inside `if:` — the visible first
        # assignment is not what the program runs with.
        self.assertTrue(any("rebound" in r for r in self.risk("TZ")["TZ"]))

    def test_environment_dependence_is_flagged(self):
        self.assertTrue(any("environment-dependent" in r for r in self.risk("TZ")["TZ"]))

    def test_an_unknown_name_is_reported_not_silently_passed(self):
        self.assertEqual(self.risk("NOPE")["NOPE"], ["undefined-at-module-level"])

    def test_a_cycle_terminates(self):
        source = "A = B\nB = A\n"
        self.assertIsInstance(seam.value_carry_risk(source, ["A"])["A"], list)


class CarryStrategyTests(unittest.TestCase):
    """`value_carry_risk` says whether copying is safe and stops there. This says
    what to do instead — the half that was being re-derived by hand every time."""

    SOURCE = (
        "import os\n"
        "import re\n"
        "import threading\n"
        "from pathlib import Path\n"
        "_re = re\n"
        "LOCK = threading.Lock()\n"
        "LIMIT = 5\n"
        "BASE = Path(__file__).parent\n"
        "TZ = os.getenv('TZ', 'UTC')\n"
        "if os.getenv('OVERRIDE'):\n"
        "    TZ = 'Asia/Manila'\n"
    )

    def strategy(self, name):
        return seam.carry_strategy(self.SOURCE, [name])[name]["strategy"]

    def test_an_import_alias_is_re_derived_because_imports_follow_you(self):
        self.assertEqual(self.strategy("_re"), seam.CARRY_REDERIVE)

    def test_a_plain_literal_is_copied(self):
        self.assertEqual(self.strategy("LIMIT"), seam.CARRY_COPY)

    def test_a_file_relative_path_is_bound_not_copied(self):
        self.assertEqual(self.strategy("BASE"), seam.CARRY_BIND)

    def test_a_rebound_environment_value_is_bound(self):
        self.assertEqual(self.strategy("TZ"), seam.CARRY_BIND)

    def test_a_live_lock_is_injected_never_captured(self):
        self.assertEqual(self.strategy("LOCK"), seam.CARRY_INJECT)

    def test_every_answer_carries_its_reason(self):
        for name in ("_re", "LIMIT", "BASE", "TZ", "LOCK"):
            with self.subTest(name=name):
                self.assertTrue(seam.carry_strategy(self.SOURCE, [name])[name]["why"])

    def test_it_is_conservative_rather_than_clever(self):
        """A flag set in the two arms of a try/except around an import really is
        re-derivable — but nothing in the syntax proves those arms correspond to
        import success and failure. CARVE answers `bind`, the safe direction, and
        a caller holding whole-program evidence may override it, exactly as
        `exclusive_helpers` already works. A wrong `copy` compiles and ships; a
        wrong `bind` fails loudly at startup."""
        source = (
            "try:\n"
            "    import mss\n"
            "    AVAILABLE = True\n"
            "except ImportError:\n"
            "    AVAILABLE = False\n"
        )
        self.assertEqual(
            seam.carry_strategy(source, ["AVAILABLE"])["AVAILABLE"]["strategy"],
            seam.CARRY_BIND)


if __name__ == "__main__":
    unittest.main(verbosity=2)
