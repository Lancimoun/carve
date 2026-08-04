"""What does this code REFUSE? — the surface no interface comparison can see.

The gap this fills
------------------
Every structural check answers *what does this expose*: signatures, exported
names, return types. Two implementations can agree on all of it and still differ
in the only way that matters — **what they turn down**.

That is not hypothetical. Two copies of one engine, same constants, same
`__init__` signature, same `run(authorize=...)` signature, diverged like this:

    run(authorize={"typo"})   # a node that does not exist
    run(authorize={"ungated"})# a node that needs no authorization
    run(authorize=["name"])   # a list, not a set

One raised on all three. The other accepted all three **silently**. A diff of
their public surfaces showed nothing, the test suites of both passed, and the
weaker one had been running the security-relevant path for weeks.

Validation is the first thing a copy loses and the last thing anyone checks,
because refusals live in the negative space: they are behaviour on inputs you
would never deliberately send.

What a "refusal" is here
------------------------
A `raise` reachable from the function, paired with the condition that guards it.
Conditions are captured as source text (`ast.unparse`) rather than interpreted —
this is a **comparison** tool, not a theorem prover, and normalising conditions
would quietly equate guards that differ in a detail that matters.

Deliberately NOT counted as refusals:

  * `raise` inside `except` — that is error *translation*, not input rejection;
  * bare `return` / `return None` on a guard — a silent no-op is the opposite of
    a refusal and is often the bug. `bare_guards()` reports those separately, so
    "A raises where B quietly returns" is visible rather than invisible;
  * anything in a nested function — it belongs to that function's surface.

Analysis only. Parses source, never imports or executes it.
"""

import ast

__all__ = ["refusals", "bare_guards", "class_refusals", "compare",
           "compare_classes", "report"]


def _find(tree, name):
    """The top-level function/class member matching `name` ('f' or 'Class.m')."""
    if "." in name:
        cls_name, attr = name.split(".", 1)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and member.name == attr:
                        return member
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _own_body(func):
    """Walk `func` without descending into nested function or class bodies.

    A nested helper's guards belong to the helper. Counting them here would make
    a function look stricter than it is -- the same class of error as `ast.walk`
    descending into `orelse` and inventing dependencies, which `carve.dispatch`
    exists to avoid.
    """
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    out, stack = [], list(func.body)
    while stack:
        node = stack.pop()
        # The check belongs on the node being VISITED, not on its children. The
        # first version filtered children only, so a nested `def` popped off the
        # stack still had its own body pushed -- and the parent inherited the
        # child's guards, which is precisely the attribution error this function
        # exists to prevent. Caught by its own test.
        if isinstance(node, nested):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def _exception_name(node):
    exc = node.exc
    if exc is None:
        return "re-raise"
    call = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(call, ast.Name):
        return call.id
    if isinstance(call, ast.Attribute):
        return call.attr
    return "raise"


def _guard_of(func, target):
    """The nearest enclosing `if` test for `target`, as source text."""
    for node in _own_body(func):
        if not isinstance(node, ast.If):
            continue
        for branch, label in ((node.body, ""), (node.orelse, "not ")):
            for stmt in branch:
                for sub in ast.walk(stmt):
                    if sub is target:
                        try:
                            return label + ast.unparse(node.test)
                        except Exception:      # pragma: no cover - very old ASTs
                            return label + "<condition>"
    return "unconditional"


def refusals(source, name):
    """{(exception, condition)} that `name` raises on its own inputs.

    Empty for a function that never rejects anything -- which is itself the
    finding when its counterpart's set is not empty.
    """
    func = _find(ast.parse(source), name)
    if func is None:
        raise LookupError(f"no function or method named {name!r} at module level")

    handled = set()
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Raise):
                    handled.add(id(sub))

    found = set()
    for node in _own_body(func):
        if isinstance(node, ast.Raise) and id(node) not in handled:
            found.add((_exception_name(node), _guard_of(func, node)))
    return found


def bare_guards(source, name):
    """Conditions under which `name` returns early instead of raising.

    Reported separately and on purpose. "A raises where B silently returns" is
    the single most useful line this module can produce, and folding both into
    one set would hide it.
    """
    func = _find(ast.parse(source), name)
    if func is None:
        raise LookupError(f"no function or method named {name!r} at module level")

    found = set()
    for node in _own_body(func):
        if isinstance(node, ast.Return):
            value = node.value
            is_bare = value is None or (isinstance(value, ast.Constant) and value.value is None)
            if is_bare:
                guard = _guard_of(func, node)
                if guard != "unconditional":
                    found.add(guard)
    return found


def class_refusals(source, class_name):
    """Every refusal across a class's methods, unioned.

    Necessary, not a convenience. Validation is routinely *delegated*: the real
    divergence that motivated this module lived in `Graph._validate_authorization`,
    which `Graph.run` calls. Comparing `run` alone showed two differences and
    missed the three that mattered, because a method-level view stops at the call.

    Unioning the class is the honest unit for "what does this component refuse",
    since a caller cannot tell which method did the rejecting.
    """
    tree = ast.parse(source)
    node = next((n for n in tree.body
                 if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    if node is None:
        raise LookupError(f"no class named {class_name!r} at module level")
    found = set()
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found |= refusals(source, f"{class_name}.{member.name}")
    return found


def compare_classes(source_a, source_b, class_name):
    """Refusal diff for a whole class. `only_a` is what B forgot to check."""
    a, b = class_refusals(source_a, class_name), class_refusals(source_b, class_name)
    return {"only_a": sorted(a - b), "only_b": sorted(b - a), "shared": sorted(a & b)}


def compare(source_a, source_b, name, name_b=None):
    """How two implementations of the same thing differ in what they refuse.

    Returns {"only_a", "only_b", "shared", "a_raises_where_b_returns"}. The last
    key is the one worth reading first: it names conditions where one version
    rejects and the other quietly does nothing.
    """
    name_b = name if name_b is None else name_b
    a, b = refusals(source_a, name), refusals(source_b, name_b)
    b_returns = bare_guards(source_b, name_b)
    return {
        "only_a": sorted(a - b),
        "only_b": sorted(b - a),
        "shared": sorted(a & b),
        "a_raises_where_b_returns": sorted(
            (exc, cond) for exc, cond in (a - b) if cond in b_returns
        ),
    }


def report(source_a, source_b, name, name_b=None, label_a="A", label_b="B"):
    """Human-readable comparison. Empty `only_*` lists mean the guards agree."""
    diff = compare(source_a, source_b, name, name_b)
    lines = [f"refusal comparison: {name} ({label_a}) vs {name_b or name} ({label_b})"]
    if not diff["only_a"] and not diff["only_b"]:
        lines.append(f"  guards agree ({len(diff['shared'])} shared refusal(s))")
        return "\n".join(lines)
    for key, label in (("only_a", label_a), ("only_b", label_b)):
        if diff[key]:
            lines.append(f"  refused ONLY by {label}:")
            for exc, cond in diff[key]:
                lines.append(f"    {exc:18} when: {cond}")
    if diff["a_raises_where_b_returns"]:
        lines.append(f"  {label_a} raises where {label_b} returns silently:")
        for exc, cond in diff["a_raises_where_b_returns"]:
            lines.append(f"    {exc:18} when: {cond}")
    lines.append(f"  shared: {len(diff['shared'])}")
    return "\n".join(lines)
