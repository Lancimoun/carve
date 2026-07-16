"""Does the code you just extracted actually resolve in its new home?

The bug this exists for
-----------------------
`carve.coupling` tells you a function is free -- it touches none of its origin
module's internals, so it can move. That check deliberately ignores stdlib and
third-party names, because ignoring them is what makes the function movable.

But the origin module's import block was silently satisfying those names. The
destination has its own. So a function can be perfectly free and still raise
`NameError` the first time anyone calls it.

This happened for real: a `zip_files` function calling `Path(src)` moved into a
module with no `from pathlib import Path`. Every other test was green -- a
surface contract, twelve behavioural tests, and a byte-for-byte differential --
because **none of them called that function**. It would have surfaced the first
time a human asked the program to zip something.

Free ≠ import-complete. Run both checks or ship the gap.

Why symtable and not an ast walk
--------------------------------
A naive "collect every Name load, subtract the module's bindings" flags every
closure variable and comprehension target as undefined. The first version of
this reported failures inside its own decorator factory. `symtable` resolves
real Python scoping -- locals, closures, globals -- so `is_global()` means what
it says, and a function-local `import x` correctly counts as a binding.
"""

import builtins
import os
import symtable

_BUILTINS = frozenset(dir(builtins))


def unresolved_names(source, filename="<module>"):
    """Names a module's functions load that the module never binds.

    Returns {function_name: sorted(names)}. Empty dict means every name
    resolves. Static: nothing is imported or executed, so this is safe to run
    against code that needs credentials or hits the network at import time.
    """
    top = symtable.symtable(source, filename, "exec")
    bound = {
        sym.get_name()
        for sym in top.get_symbols()
        if sym.is_assigned() or sym.is_imported()
    }

    problems = {}

    def visit(table):
        for child in table.get_children():
            for sym in child.get_symbols():
                # is_global(): resolved against module/builtin scope — not a
                # local, not a closure cell. is_assigned() catches the
                # function-local `import x` / `x = ...` case.
                if sym.is_global() and not sym.is_assigned():
                    name = sym.get_name()
                    if name not in bound and name not in _BUILTINS:
                        problems.setdefault(child.get_name(), set()).add(name)
            visit(child)

    visit(top)
    return {fn: sorted(names) for fn, names in problems.items()}


def check_path(path):
    """unresolved_names for a file on disk."""
    with open(path, encoding="utf-8") as fh:
        return unresolved_names(fh.read(), os.path.basename(path))


def check_package(directory):
    """Every .py in a directory. Returns a list of human-readable problems.

    This is the shape you want in CI: one job, no installs, and a failure
    message that names the function and the missing name rather than a
    traceback from three weeks later.
    """
    problems = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".py"):
            continue
        for fn, names in check_path(os.path.join(directory, filename)).items():
            for name in names:
                problems.append(
                    f"{filename}: {fn}() uses {name!r}, "
                    f"which the module never imports or defines"
                )
    return problems
