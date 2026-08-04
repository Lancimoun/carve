"""carve.guards — comparing implementations by what they REFUSE.

Two things a copy loses first and a reviewer checks last: the guard clauses, and
the difference between raising and quietly returning. These pin both, plus the
boundaries that keep the analysis honest (no descent into nested functions, no
counting error-translation as input rejection).
"""

import unittest

from carve import guards


STRICT = (
    "def run(items):\n"
    "    if not isinstance(items, set):\n"
    "        raise TypeError('items must be a set')\n"
    "    if not items:\n"
    "        raise ValueError('items must not be empty')\n"
    "    return sorted(items)\n"
)

LOOSE = (
    "def run(items):\n"
    "    if not items:\n"
    "        return None\n"
    "    return sorted(items)\n"
)


class Refusals(unittest.TestCase):
    def test_each_raise_is_paired_with_its_condition(self):
        self.assertEqual(
            guards.refusals(STRICT, "run"),
            {("TypeError", "not isinstance(items, set)"),
             ("ValueError", "not items")},
        )

    def test_a_function_that_refuses_nothing_is_empty_not_an_error(self):
        # Emptiness IS the finding when the counterpart is not empty.
        self.assertEqual(guards.refusals(LOOSE, "run"), set())

    def test_an_unconditional_raise_is_labelled_as_such(self):
        source = "def f(x):\n    raise NotImplementedError\n"
        self.assertEqual(guards.refusals(source, "f"),
                         {("NotImplementedError", "unconditional")})

    def test_error_translation_is_not_an_input_refusal(self):
        """A `raise` inside `except` re-labels a failure that already happened;
        counting it would make every wrapper look like a validator."""
        source = ("def f(x):\n"
                  "    try:\n"
                  "        return int(x)\n"
                  "    except ValueError:\n"
                  "        raise RuntimeError('bad')\n")
        self.assertEqual(guards.refusals(source, "f"), set())

    def test_a_nested_function_guard_belongs_to_the_nested_function(self):
        # Same trap `carve.dispatch` exists to avoid: walking into a child scope
        # and attributing its behaviour to the parent.
        source = ("def outer(x):\n"
                  "    def inner(y):\n"
                  "        if y < 0:\n"
                  "            raise ValueError('neg')\n"
                  "    return inner\n")
        self.assertEqual(guards.refusals(source, "outer"), set())

    def test_a_method_is_addressable(self):
        source = ("class C:\n"
                  "    def go(self, x):\n"
                  "        if x is None:\n"
                  "            raise TypeError('x')\n")
        self.assertEqual(guards.refusals(source, "C.go"), {("TypeError", "x is None")})

    def test_a_missing_name_raises_rather_than_returning_empty(self):
        # Returning an empty set for a typo would read as "refuses nothing" --
        # a false finding in the direction of accusing the wrong file.
        with self.assertRaises(LookupError):
            guards.refusals(STRICT, "nope")


class BareGuards(unittest.TestCase):
    def test_a_guarded_silent_return_is_reported(self):
        self.assertEqual(guards.bare_guards(LOOSE, "run"), {"not items"})

    def test_a_final_return_is_not_a_guard(self):
        source = "def f(x):\n    return None\n"
        self.assertEqual(guards.bare_guards(source, "f"), set())

    def test_returning_a_value_is_not_a_silent_no_op(self):
        source = "def f(x):\n    if not x:\n        return []\n    return x\n"
        self.assertEqual(guards.bare_guards(source, "f"), set())


class Compare(unittest.TestCase):
    def test_it_names_what_only_the_strict_version_refuses(self):
        diff = guards.compare(STRICT, LOOSE, "run")
        self.assertEqual(diff["only_a"],
                         [("TypeError", "not isinstance(items, set)"),
                          ("ValueError", "not items")])
        self.assertEqual(diff["only_b"], [])

    def test_it_singles_out_raises_where_the_other_returns_silently(self):
        """The most useful line the module can produce: one version rejects the
        input, the other accepts it and does nothing."""
        diff = guards.compare(STRICT, LOOSE, "run")
        self.assertEqual(diff["a_raises_where_b_returns"], [("ValueError", "not items")])

    def test_identical_implementations_report_no_difference(self):
        diff = guards.compare(STRICT, STRICT, "run")
        self.assertEqual(diff["only_a"], [])
        self.assertEqual(diff["only_b"], [])
        self.assertEqual(len(diff["shared"]), 2)

    def test_report_says_guards_agree_when_they_do(self):
        self.assertIn("guards agree", guards.report(STRICT, STRICT, "run"))

    def test_report_names_the_stricter_side(self):
        text = guards.report(STRICT, LOOSE, "run", label_a="new", label_b="old")
        self.assertIn("refused ONLY by new", text)
        self.assertIn("raises where old returns silently", text)


class ClassLevel(unittest.TestCase):
    """Validation is routinely DELEGATED. A method-level comparison stops at the
    call and misses exactly the guards that live one hop away — which is how the
    real divergence this module was written for stayed invisible."""

    DELEGATING = (
        "class Engine:\n"
        "    def run(self, names):\n"
        "        self._validate(names)\n"
        "        return True\n"
        "    def _validate(self, names):\n"
        "        if not isinstance(names, set):\n"
        "            raise TypeError('set required')\n"
    )
    PERMISSIVE = (
        "class Engine:\n"
        "    def run(self, names):\n"
        "        return True\n"
    )

    def test_a_method_view_misses_a_delegated_guard(self):
        self.assertEqual(guards.refusals(self.DELEGATING, "Engine.run"), set())

    def test_the_class_view_finds_it(self):
        self.assertEqual(guards.class_refusals(self.DELEGATING, "Engine"),
                         {("TypeError", "not isinstance(names, set)")})

    def test_comparing_classes_surfaces_the_missing_validation(self):
        diff = guards.compare_classes(self.DELEGATING, self.PERMISSIVE, "Engine")
        self.assertEqual(diff["only_a"], [("TypeError", "not isinstance(names, set)")])
        self.assertEqual(diff["only_b"], [])

    def test_a_missing_class_raises(self):
        with self.assertRaises(LookupError):
            guards.class_refusals(self.PERMISSIVE, "Nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
