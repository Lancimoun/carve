"""Coupling analysis *inside* a dispatch chain.

Why this exists separately from `carve.coupling`
------------------------------------------------
`coupling` classifies a module's top-level functions. That is the common case
and it is not the interesting one, because the worst monoliths do not hide their
mass in many functions -- they hide it in **one**.

The real target this was extracted from is a 7,789-line `run_tool(name, inputs)`
containing a 219-branch `if name == "..." / elif ...` chain. To `coupling` that
is a single welded function with 70 dependencies: technically true, completely
useless. The 219 units you actually want to move are invisible.

This module makes each branch a unit.

The orelse trap, in detail
--------------------------
`ast.walk` on an `ast.If` descends into `orelse`. In an if/elif chain, `orelse`
is *the entire rest of the chain* -- so branch #1 appears to depend on everything
branches #2..#219 depend on, and #219 on nothing. The dependency counts slide
smoothly down the chain and look like a real finding about layering.

They are a finding about the walker. **A number that decreases neatly with
position is measuring position.** Every function here walks `node.body` only.

Locals are not dependencies
---------------------------
A branch reading `path` when `run_tool` did `path = inputs["path"]` three lines
earlier is not coupled to the module. `symtable` resolves the *containing
function's* scope, and those locals are excluded -- otherwise every branch looks
welded to its own function's variables and the whole analysis reads as "nothing
can move".
"""

import ast
import builtins
import symtable

from .coupling import _global_writes, defined_names

_BUILTINS = frozenset(dir(builtins))


def find_function(tree, name):
    """The top-level function named `name`, or None."""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _walk_function_scope(function):
    """Yield `(node, parent)` without descending into nested scopes."""
    stack = [(node, function) for node in reversed(function.body)]
    while stack:
        node, parent = stack.pop()
        yield node, parent
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        children = list(ast.iter_child_nodes(node))
        stack.extend((child, node) for child in reversed(children))


def _branch_value(test, discriminant):
    """The constant a branch compares the discriminant against.

    Matches only `name == "x"` (in either operand order) and
    `name in ("x", "y")`. Returns a list, because one branch can serve several
    names -- collapsing that to a single value silently drops tools from the
    inventory.

    The operator check is load-bearing. Treating `name != "x"` or
    `name not in (...)` as a named target inverts runtime behavior while still
    producing a plausible-looking inventory.
    """
    if not isinstance(test, ast.Compare):
        return []
    if len(test.ops) != 1 or len(test.comparators) != 1:
        return []

    op = test.ops[0]
    right = test.comparators[0]

    if isinstance(op, ast.Eq):
        if (
            isinstance(test.left, ast.Name)
            and test.left.id == discriminant
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
        ):
            return [right.value]
        if (
            isinstance(right, ast.Name)
            and right.id == discriminant
            and isinstance(test.left, ast.Constant)
            and isinstance(test.left.value, str)
        ):
            return [test.left.value]
        return []

    if not isinstance(op, ast.In):
        return []
    if not (isinstance(test.left, ast.Name) and test.left.id == discriminant):
        return []
    if not isinstance(right, (ast.Tuple, ast.List, ast.Set)):
        return []
    if not all(
        isinstance(element, ast.Constant) and isinstance(element.value, str)
        for element in right.elts
    ):
        return []
    return [element.value for element in right.elts]


def find_chain(tree, function_name, discriminant="name", min_length=3):
    """Locate the dispatch chain and return [(values, if_node), ...] in order.

    `min_length` guards against latching onto some small unrelated `if` earlier
    in the function -- the chain you want is the long one. Getting this wrong is
    silent: you analyse three branches, report them, and never learn the other
    216 exist.
    """
    fn = find_function(tree, function_name)
    if fn is None:
        return []
    best = []
    for stmt, parent in _walk_function_scope(fn):
        if not isinstance(stmt, ast.If):
            continue
        # An elif is already visited through its chain head. Starting again at
        # every suffix makes a long dispatcher quadratic and can let the suffix
        # masquerade as a second candidate chain.
        if (
            isinstance(parent, ast.If)
            and len(parent.orelse) == 1
            and parent.orelse[0] is stmt
        ):
            continue
        chain, cur = [], stmt
        while isinstance(cur, ast.If):
            values = _branch_value(cur.test, discriminant)
            if not values:
                # Skipping an unsupported guard and stitching the later elifs
                # together invents a chain: `name != "x"` can make every
                # following equality unreachable. Reject the candidate whole.
                chain = []
                break
            chain.append((values, cur))
            cur = (
                cur.orelse[0]
                if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If)
                else None
            )
        if len(chain) > len(best):
            best = chain
    return best if len(best) >= min_length else []


def _function_scope(source, filename, function_name):
    """Return local bindings and names explicitly written through `global`."""
    top = symtable.symtable(source, filename, "exec")
    for child in top.get_children():
        if child.get_name() == function_name and child.get_type() == "function":
            symbols = child.get_symbols()
            local = {
                symbol.get_name()
                for symbol in symbols
                if (symbol.is_assigned() or symbol.is_parameter())
                and not symbol.is_global()
            }
            global_writes = _global_writes(child)
            return local, global_writes
    return set(), set()


def _function_locals(source, filename, function_name):
    """Names bound locally inside the containing function."""
    return _function_scope(source, filename, function_name)[0]


def _branch_deps(node, defined, skip, global_writes=frozenset()):
    """Origin-module names a branch's OWN body reads or writes."""
    used = set()
    for stmt in node.body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Name):
                is_owned_read = (
                    isinstance(child.ctx, ast.Load) and child.id in defined
                )
                is_global_write = (
                    isinstance(child.ctx, (ast.Store, ast.Del))
                    and child.id in global_writes
                )
                if (
                    (is_owned_read or is_global_write)
                    and child.id not in _BUILTINS
                    and child.id not in skip
                ):
                    used.add(child.id)
    return used


def classify_chain(source, function_name, discriminant="name", filename="<module>", ignore=()):
    """Split a dispatch chain's branches into free and welded.

    Returns (free, welded): `free` is a list of branch values movable as pure
    relocations; `welded` maps value -> sorted internals it needs.
    """
    tree = ast.parse(source)
    defined = defined_names(tree)
    # NOTE: `function_name` is deliberately NOT skipped here, unlike in
    # coupling.classify. The two have opposite semantics and conflating them
    # produces a FALSE FREE -- the worst error this library can make.
    #
    # In coupling, a function calling itself is recursion: it takes itself with
    # it, so it is not welded to anything left behind. In a dispatch chain, a
    # BRANCH calling the dispatcher is welded to it absolutely -- the branch
    # cannot leave without a callback or a circular import.
    #
    # Real case: maxima's `web_search` falls back to `run_tool("web_search_live")`.
    # Skipping the containing name reported it movable. It is not: extracting it
    # would need either a circular import back to the origin module or a
    # tool-runner seam, and the tempting shortcut -- calling the inner handler
    # directly -- silently drops the dispatcher's audit-log entry for the inner
    # tool, which is a behaviour change wearing a refactor's clothes.
    local, global_writes = _function_scope(source, filename, function_name)
    skip = local | set(ignore)

    free, welded = [], {}
    for values, node in find_chain(tree, function_name, discriminant):
        deps = sorted(_branch_deps(node, defined, skip, global_writes))
        for value in values:
            if deps:
                welded[value] = deps
            else:
                free.append(value)
    return free, welded


def report(source, function_name, discriminant="name", filename="<module>", ignore=()):
    """How much of this dispatch chain can move today?

    Reports targets AND branches, because they differ and both matter: one
    `elif name in ("a", "b")` is a single branch serving two tools. You move
    *targets*; you delete *branches*. Printing one number labelled as the other
    is how an inventory quietly loses a tool.
    """
    tree = ast.parse(source)
    branches = len(find_chain(tree, function_name, discriminant))
    free, welded = classify_chain(
        source, function_name, discriminant=discriminant, filename=filename, ignore=ignore
    )
    total = len(free) + len(welded)
    if not total:
        return f"no dispatch chain found in {function_name}() on `{discriminant}`"
    suffix = f" across {branches} branches" if branches != total else ""
    return "\n".join([
        f"{function_name}(): {total} dispatch targets on `{discriminant}`{suffix}",
        f"  free   {len(free):>4}  ({len(free) / total * 100:.0f}%) - movable as pure relocations",
        f"  welded {len(welded):>4}  ({len(welded) / total * 100:.0f}%) - need a seam first",
    ])
