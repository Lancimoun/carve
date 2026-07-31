"""Which parts of a monolith can move, and which are welded in.

The question this answers
------------------------
You have one enormous module. You want to break it up. The first decision --
*what do I move first?* -- is usually made by vibes: "let's do the file tools",
"let's do the memory layer". That grouping is a guess about the code's shape,
and when it is wrong you find out halfway through an extraction, holding a
circular import.

The measurable answer: a function is **free** if it depends on nothing its
origin module *defines*, and **welded** if it does. Free functions move as pure
relocations. Welded ones need a seam first -- dependency injection, or moving
what they depend on -- which is a design decision, not a mechanical one.

Why coupling and not "by domain"
--------------------------------
On a real 30,014-line target, domain grouping predicted nothing. The file and
web tools happened to be free; the memory tools were welded to 20 internals and
could not move at all. The split cut *across* domains: 128 of 207 functions were
free, scattered through every one. Grouping by topic sent the work at a wall.

Two distinctions that this gets right and a naive version does not
-----------------------------------------------------------------
1. **An import is not a weld.** `os`, `requests`, `Path` are module-level names,
   but they follow you: copy the import line and the function works. Only names
   the module *defines* -- functions, classes, assignments -- are left behind.
   Counting imports as welds reports every function as stuck.
2. **Scope matters.** A function with a local `total` that shadows a module-level
   `total` depends on nothing. A bare `ast` walk sees a `Name` load and reports
   a weld that does not exist -- sending someone to build a seam they do not
   need. `symtable` resolves real scoping, so a local, a closure cell and a
   genuine global load are told apart.

The subtlety that still costs you
---------------------------------
Free does NOT mean import-complete. This ignores imports *by design* -- that is
what makes a function movable -- but the origin's import block was silently
satisfying them and the destination has its own. A function can be free and
still `NameError` in its new home. `carve.resolve` is the other half; use both.
"""

import ast
import builtins
import symtable

_BUILTINS = frozenset(dir(builtins))


def defined_names(tree):
    """Names the module DEFINES -- what a function loses when it moves out.

    Imports are deliberately excluded: they are copyable, so they do not weld
    anything. Anything here is state or behaviour that stays behind.
    """
    names = set()
    stack = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The name is bound in module scope; its body has a different scope.
            names.add(node.name)
            continue
        if isinstance(
            node,
            (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Imports remain deliberately copyable, even inside control flow.
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    return names


def imported_names(tree):
    """Names the module imports. Reported for completeness; never a weld."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _global_loads(table):
    """Names a function reads or writes in module/builtin scope, per symtable.

    An explicit global write is as location-dependent as a read: after moving
    the function, `global STATE` otherwise mutates the destination module.
    """
    out = set()
    for sym in table.get_symbols():
        if sym.is_global() and (sym.is_referenced() or sym.is_assigned()):
            out.add(sym.get_name())
    for child in table.get_children():
        out |= _global_loads(child)
    return out


def _global_writes(table):
    """Names explicitly assigned through module scope, including nested scopes."""
    out = {
        sym.get_name()
        for sym in table.get_symbols()
        if sym.is_global() and sym.is_assigned()
    }
    for child in table.get_children():
        out |= _global_writes(child)
    return out


def _definition_time_loads(function):
    """Names evaluated in module scope while a function object is created.

    Function symbol tables cover the body, not decorators, defaults, or
    annotations. Those expressions travel with the definition too. Annotations
    are counted conservatively even under postponed evaluation.
    """
    expressions = [
        *function.decorator_list,
        *function.args.defaults,
        *(value for value in function.args.kw_defaults if value is not None),
    ]
    arguments = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    if function.args.vararg is not None:
        arguments.append(function.args.vararg)
    if function.args.kwarg is not None:
        arguments.append(function.args.kwarg)
    expressions.extend(
        argument.annotation
        for argument in arguments
        if argument.annotation is not None
    )
    if function.returns is not None:
        expressions.append(function.returns)
    expressions.extend(getattr(function, "type_params", ()))
    return {
        child.id
        for expression in expressions
        for child in ast.walk(expression)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def classify(source, filename="<module>", ignore=()):
    """Split a module's top-level functions into free and welded.

    Returns (free, welded): `free` is a sorted list of names; `welded` maps
    name -> sorted internals it depends on. Measured, not eyeballed.
    """
    tree = ast.parse(source)
    defined = defined_names(tree)
    ignore = set(ignore)
    top = symtable.symtable(source, filename, "exec")
    tables = {c.get_name(): c for c in top.get_children() if c.get_type() == "function"}

    free, welded = [], {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        table = tables.get(node.name)
        if table is None:  # pragma: no cover - defensive
            continue
        loads = _global_loads(table) | _definition_time_loads(node)
        writes = _global_writes(table)
        deps = sorted(
            n
            for n in loads
            if (n in defined or n in writes)
            and (n not in _BUILTINS or n in writes)
            and (n != node.name or n in writes)
            and n not in ignore
        )
        if deps:
            welded[node.name] = deps
        else:
            free.append(node.name)
    return sorted(free), welded


def report(source, filename="<module>", ignore=()):
    """Human-readable summary. What fraction of this module can move today?"""
    free, welded = classify(source, filename=filename, ignore=ignore)
    total = len(free) + len(welded)
    if not total:
        return "no top-level functions found"
    lines = [
        f"{total} top-level functions",
        f"  free   {len(free):>4}  ({len(free) / total * 100:.0f}%) - movable as pure relocations",
        f"  welded {len(welded):>4}  ({len(welded) / total * 100:.0f}%) - need a seam first",
    ]
    if welded:
        buckets = {"1-2 deps": 0, "3-5 deps": 0, "6+ deps": 0}
        for deps in welded.values():
            n = len(deps)
            buckets["1-2 deps" if n <= 2 else "3-5 deps" if n <= 5 else "6+ deps"] += 1
        for label, count in buckets.items():
            if count:
                lines.append(f"    {label:<10} {count:>4}")
    return "\n".join(lines)
