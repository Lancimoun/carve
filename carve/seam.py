"""carve.seam — the SHAPE of a weld, and the order it comes apart.

`dispatch.classify_chain` says WHICH internal names each welded branch needs.
This module answers the question a plan actually needs next: those names are not
equal. An `import`-alias (`_re = re`) re-imports for free; a constant or a data
table moves to a config module; a single shared function is the real seam a
dependency injection has to carry. Counting welded branches hides that
inequality — weighing each name by *how it is carried* reveals the order the
chain unlocks in, and how small the genuinely-hard residue actually is.

The staircase this produces (`unlock_tiers`) turns "N welded, needs an
architecture decision" into "one injected seam unlocks K of them; only the last
few need real dependency injection" — a plan, not a wall.

Analysis only — pure AST over source text, like the rest of CARVE. Nothing here
is imported or executed.
"""

import ast
from collections import Counter

from . import dispatch


# --- name kinds ------------------------------------------------------------- #

def classify_kinds(source):
    """Map every module-level name -> its KIND.

    function / async-fn / class / import / import-alias (`x = other_module`) /
    constant / regex-const / data-literal / computed-const / assign. The kind is
    what decides the *cost* of carrying that name across a module boundary.
    """
    tree = ast.parse(source)
    kind = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            kind[node.name] = "function"
        elif isinstance(node, ast.AsyncFunctionDef):
            kind[node.name] = "async-fn"
        elif isinstance(node, ast.ClassDef):
            kind[node.name] = "class"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                kind[a.asname or a.name.split(".")[0]] = "import"
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    kind[tgt.id] = _value_kind(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kind[node.target.id] = _value_kind(node.value)
    return kind


def _value_kind(value):
    if value is None:
        return "assign"
    if isinstance(value, ast.Name):
        # `_re = re`: the RHS is another module-level name — re-import it in the
        # target and the reference is satisfied. A near-leaf, not a real weld.
        return "import-alias"
    if isinstance(value, ast.Call):
        fn = getattr(value.func, "attr", getattr(value.func, "id", ""))
        if fn in ("Lock", "RLock", "Condition", "Semaphore", "Event"):
            return "shared-state"
        if fn == "compile":
            return "regex-const"
        return "computed-const"
    if isinstance(value, ast.Constant):
        return "constant"
    if isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)):
        return "data-literal"
    return "assign"


# kinds a config/data module + a re-import carry with NO seam (the cheap tier).
CHEAP_KINDS = frozenset({"import-alias", "constant", "regex-const", "data-literal", "assign"})


# --- the shape of the weld -------------------------------------------------- #

def weld_shape(source, function_name, discriminant="name"):
    """Return the shape of a dispatch chain's weld — not just its size.

    {
      'welded':  {branch_value -> [dep names]},        from dispatch.classify_chain
      'by_kind': {kind -> [(refcount, name), ...]},    every internal name, grouped
      'sizes':   {n_deps -> branch_count},             how concentrated the weld is
    }
    """
    _, welded = dispatch.classify_chain(source, function_name, discriminant=discriminant)
    kind = classify_kinds(source)
    freq = Counter()
    for deps in welded.values():
        freq.update(deps)
    by_kind = {}
    for name, count in freq.items():
        by_kind.setdefault(kind.get(name, "unknown"), []).append((count, name))
    for names in by_kind.values():
        names.sort(reverse=True)
    sizes = dict(Counter(len(d) for d in welded.values()))
    return {"welded": welded, "by_kind": by_kind, "sizes": sizes}


# --- the order it unlocks --------------------------------------------------- #

def unlock_tiers(source, function_name, seam=frozenset(), discriminant="name"):
    """Partition welded branches into a cumulative unlock staircase.

    tier0: every dep is a cheap-carry kind (config/data module, no seam)
    tier1: + the injected `seam` name-set — the ONE accessor you choose to build
    tier2: + each branch's single-use private helper (co-moves with its branch)
    tier3: the remainder — genuine shared-service seams

    `services` counts the internal names still needed by tier3 — the short list a
    real dependency injection has to carry. Returns
    {'t0','t1','t2','t3': sets of branch values, 'services': Counter}.
    """
    _, welded = dispatch.classify_chain(source, function_name, discriminant=discriminant)
    kind = classify_kinds(source)
    seam = set(seam)

    cheap = {n for n, k in kind.items() if k in CHEAP_KINDS}
    single_fn = {
        n for n, k in kind.items()
        if k in ("function", "async-fn")
        and sum(1 for deps in welded.values() if n in deps) == 1
    }

    def within(branch, allowed):
        return all(dep in allowed for dep in welded[branch])

    t0 = {b for b in welded if within(b, cheap)}
    t1 = {b for b in welded if within(b, cheap | seam)} - t0
    t2 = {b for b in welded if within(b, cheap | seam | single_fn)} - t0 - t1
    t3 = set(welded) - t0 - t1 - t2

    services = Counter()
    for b in t3:
        for dep in welded[b]:
            if dep not in cheap and dep not in seam and dep not in single_fn:
                services[dep] += 1
    return {"t0": t0, "t1": t1, "t2": t2, "t3": t3, "services": services}


# --- human-readable report -------------------------------------------------- #

_KIND_ORDER = [
    "import-alias", "constant", "regex-const", "data-literal", "assign",
    "computed-const", "shared-state", "class", "async-fn", "function", "unknown",
]

_CARRY = {
    "import-alias": "re-import (a near-leaf, not a weld)",
    "constant": "move to a config module, import it",
    "regex-const": "move to a shared module, import it",
    "data-literal": "move the table to a data module, import it",
    "assign": "config module (path / derived constant)",
    "computed-const": "config, or inject if it is a live handle",
    "shared-state": "inject (one shared lock / handle)",
    "class": "import or inject (a type dependency)",
    "async-fn": "INJECT — behaviour a seam must carry",
    "function": "INJECT — behaviour a seam must carry",
    "unknown": "investigate — not found at module level",
}


def report(source, function_name, seam=frozenset(), discriminant="name"):
    """Human-readable: the by-kind weld shape, then the cumulative unlock staircase.

    `seam` is the name-set you would inject as a single accessor (e.g. a
    load/save pair); pass it to see how much that one seam unlocks.
    """
    shape = weld_shape(source, function_name, discriminant=discriminant)
    welded, by_kind, sizes = shape["welded"], shape["by_kind"], shape["sizes"]
    if not welded:
        return f"{function_name}(): no welded branches to shape"

    out = []
    n_names = sum(len(v) for v in by_kind.values())
    out.append(f"{function_name}(): {len(welded)} welded branches, "
               f"{n_names} distinct internal names carried by the weld")
    thin = sum(c for n, c in sizes.items() if n <= 2)
    out.append(f"  {thin}/{len(welded)} welded branches need only 1-2 names\n")

    out.append("the internal names, by kind (cheapest to carry first):")
    for k in _KIND_ORDER:
        if k not in by_kind:
            continue
        items = by_kind[k]
        tot = sum(c for c, _ in items)
        out.append(f"  [{k}] {len(items)} names, {tot} refs  -- {_CARRY.get(k, '')}")

    tiers = unlock_tiers(source, function_name, seam=seam, discriminant=discriminant)
    t0, t1, t2, t3 = tiers["t0"], tiers["t1"], tiers["t2"], tiers["t3"]
    out.append("\ncumulative unlock staircase:")
    out.append(f"  tier 0  cheap-carry only (config/data, no seam) : {len(t0):>3}   (cum {len(t0)})")
    out.append(f"  tier 1  + the one injected seam                 : {len(t1):>3}   (cum {len(t0 | t1)})")
    out.append(f"  tier 2  + each branch's single-use helper       : {len(t2):>3}   (cum {len(t0 | t1 | t2)})")
    out.append(f"  tier 3  genuine shared-service seams remain     : {len(t3):>3}")
    if tiers["services"]:
        out.append("    tier-3 services: "
                   + ", ".join(f"{c}x {n}" for n, c in tiers["services"].most_common()))
    return "\n".join(out)
