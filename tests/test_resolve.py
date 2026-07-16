"""carve.resolve — does extracted code resolve where it landed?

The headline test is `test_the_zip_files_bug`: the real defect that motivated
this module, reduced to four lines.
"""

import os
import tempfile
import unittest

from carve import resolve


class UnresolvedNames(unittest.TestCase):
    def test_the_zip_files_bug(self):
        # The real one. A function moved into a module that never imported Path.
        # A surface contract, twelve behavioural tests and a byte-for-byte
        # differential were all green, because none of them CALLED it. It would
        # have failed the first time a human asked the program to zip something.
        problems = resolve.unresolved_names(
            "import os\n"
            "def zip_files(src):\n"
            "    p = Path(src)\n"
            "    return os.path.exists(p)\n"
        )
        self.assertEqual(problems, {"zip_files": ["Path"]})

    def test_a_complete_module_reports_nothing(self):
        self.assertEqual(
            resolve.unresolved_names(
                "from pathlib import Path\n"
                "def zip_files(src):\n"
                "    return Path(src).exists()\n"
            ),
            {},
        )

    def test_a_function_local_import_counts_as_bound(self):
        # Deferred imports are load-bearing, not sloppiness: a module-level
        # `import pdfplumber` turns "this one function degrades when the library
        # is absent" into "the package fails to import" — i.e. the whole program
        # refuses to start. A checker that flags them pushes you to hoist them
        # and break exactly that.
        self.assertEqual(
            resolve.unresolved_names(
                "def read_pdf(path):\n"
                "    import pdfplumber\n"
                "    return pdfplumber.open(path)\n"
            ),
            {},
        )

    def test_builtins_are_not_unresolved(self):
        self.assertEqual(
            resolve.unresolved_names("def f(xs):\n    return len(sorted(xs))\n"), {}
        )

    def test_closures_do_not_false_positive(self):
        # The naive ast version reported failures inside its own decorator
        # factory: `fn` and `name` are closure cells, not globals. An instrument
        # that cries wolf on correct code teaches its reader to ignore it —
        # which is worse than having no instrument.
        self.assertEqual(
            resolve.unresolved_names(
                "REGISTRY = {}\n"
                "def tool(name):\n"
                "    def decorator(fn):\n"
                "        REGISTRY[name] = fn\n"
                "        return fn\n"
                "    return decorator\n"
            ),
            {},
        )

    def test_comprehension_targets_do_not_false_positive(self):
        self.assertEqual(
            resolve.unresolved_names("def f(xs):\n    return [y * 2 for y in xs]\n"), {}
        )

    def test_reports_every_missing_name_not_just_the_first(self):
        problems = resolve.unresolved_names(
            "def f():\n    return Path('x'), datetime.now(), math.pi\n"
        )
        self.assertEqual(problems["f"], ["Path", "datetime", "math"])


class CheckPackage(unittest.TestCase):
    def test_check_package_names_file_function_and_symbol(self):
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "broken.py"), "w", encoding="utf-8") as fh:
            fh.write("def grab(p):\n    return Path(p)\n")
        with open(os.path.join(tmp, "fine.py"), "w", encoding="utf-8") as fh:
            fh.write("import os\ndef ok(p):\n    return os.getcwd()\n")
        problems = resolve.check_package(tmp)
        self.assertEqual(len(problems), 1)
        # The message has to name all three, because it will be read months
        # later by someone who was not here.
        self.assertIn("broken.py", problems[0])
        self.assertIn("grab()", problems[0])
        self.assertIn("'Path'", problems[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
