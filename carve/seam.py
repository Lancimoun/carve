"""carve.seam — the shape of a weld, and the order it comes apart.

`dispatch.classify_chain` says which internal names each welded dispatch target
needs. This module answers the planning question that follows: those names are
not equal. A proven import alias re-imports cheaply; a literal can move to a
config/data module; a live handle or shared function needs an explicit seam.

The staircase from `unlock_tiers` is deliberately conservative. A target enters
tier 2 only when the caller confirms its helper has no external consumers and
the local analysis also proves it private, used by one physical dispatch branch,
self-contained apart from cheap values or the chosen seam, and unused anywhere
else in the module. A false weld costs investigation; a false unlock can break
the extraction.

Analysis only — pure AST/symbol-table work over source text. The source is never
imported or executed.
"""

import ast
import symtable
from collections import Counter

from . import coupling, dispatch


# --- name kinds ------------------------------------------------------------- #

def _import_origins(tree):
    """Map imported bindings to the qualified names they came from."""
    origins = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                origins[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            relative = "." * node.level
            prefix = (
                f"{relative}{node.module}."
                if node.module
                else relative
            )
            for alias in node.names:
                if alias.name == "*":
                    continue
                origins[alias.asname or alias.name] = prefix + alias.name
    return origins


def _root_name(value):
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def _qualified_callable(call, imports):
    value = call.func
    if isinstance(value, ast.Name):
        return imports.get(value.id, value.id)

    parts = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if not isinstance(value, ast.Name):
        return ""
    root = imports.get(value.id, value.id)
    return ".".join([root, *reversed(parts)])


_SHARED_STATE_FACTORIES = frozenset({
    "asyncio.Condition",
    "asyncio.Event",
    "asyncio.Lock",
    "asyncio.Semaphore",
    "multiprocessing.Condition",
    "multiprocessing.Event",
    "multiprocessing.Lock",
    "multiprocessing.RLock",
    "multiprocessing.Semaphore",
    "threading.Condition",
    "threading.Event",
    "threading.Lock",
    "threading.RLock",
    "threading.Semaphore",
})


def classify_kinds(source):
    """Map supported module-level names to their carry kind.

    Kinds are: function / async-fn / class / import / import-alias /
    constant / regex-const / data-literal / computed-const / shared-state /
    assign / unknown. Only kinds in `CHEAP_KINDS` enter tier 0.
    """
    return _classify_kinds(ast.parse(source))


def _classify_kinds(tree):
    imports = {}
    kind = {}

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            imports.pop(node.name, None)
            kind[node.name] = "function"
        elif isinstance(node, ast.AsyncFunctionDef):
            imports.pop(node.name, None)
            kind[node.name] = "async-fn"
        elif isinstance(node, ast.ClassDef):
            imports.pop(node.name, None)
            kind[node.name] = "class"
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            origins = _import_origins(ast.Module(body=[node], type_ignores=[]))
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name.split(".")[0]
                imports[bound] = origins[bound]
                kind[bound] = "import"
        elif isinstance(node, ast.Assign):
            value_kind = _value_kind(node.value, kind, imports)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    kind[target.id] = value_kind
                    imports.pop(target.id, None)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            kind[node.target.id] = _value_kind(node.value, kind, imports)
            imports.pop(node.target.id, None)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            kind[node.target.id] = "assign"
            imports.pop(node.target.id, None)
        else:
            # A binding inside module-level control flow may or may not execute.
            # Either way, a later alias is no longer *proven* to terminate at
            # the original import, so fail closed.
            for name in _module_scope_rebindings(node):
                imports.pop(name, None)
    return kind


def _module_scope_rebindings(root):
    """Names a compound module statement may bind, excluding nested scopes."""
    names = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(
            node,
            (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".")[0])
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _value_kind(value, known, imports):
    if value is None:
        return "assign"

    if isinstance(value, ast.Name):
        # `_re = re` is cheap only because `re` is demonstrably imported.
        # `MODEL_ALIAS = MODEL` is not itself a literal: moving that assignment
        # still needs MODEL, and inheriting MODEL's kind hides that dependency.
        if value.id in imports:
            return "import-alias"
        return "assign"

    if isinstance(value, ast.Attribute):
        root = _root_name(value)
        if root in imports:
            return "import-alias"
        return "assign"

    if isinstance(value, ast.Call):
        function = _qualified_callable(value, imports)
        if function in _SHARED_STATE_FACTORIES:
            return "shared-state"
        if function == "re.compile" and _call_arguments_are_literal(value):
            return "regex-const"
        return "computed-const"

    # `literal_eval` is the proof boundary. Looking only at the outer container
    # falsely blesses `[make()]` and `{"x": live_handle}` as static data. It
    # also handles unary literal forms such as `-1`.
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError):
        return "assign"
    return "data-literal" if isinstance(literal, (dict, list, tuple, set)) else "constant"


def _call_arguments_are_literal(call):
    """Whether every argument can be recreated without another module name."""
    if any(keyword.arg is None for keyword in call.keywords):
        return False
    for value in [*call.args, *(keyword.value for keyword in call.keywords)]:
        try:
            ast.literal_eval(value)
        except (ValueError, TypeError):
            return False
    return True


# Only syntax that proves a cheap carrying strategy enters tier 0.
CHEAP_KINDS = frozenset({
    "import-alias",
    "constant",
    "regex-const",
    "data-literal",
})


# --- physical branch analysis ---------------------------------------------- #

def _welded_entries(source, function_name, discriminant):
    """Return parsed source plus welded physical dispatch branches.

    Each entry keeps its target values grouped around one AST branch. Flattening
    a `name in ("a", "b")` branch first makes one helper reference look like
    two and corrupts both the report and the tier decision.
    """
    tree = ast.parse(source)
    defined = coupling.defined_names(tree)
    symbols = symtable.symtable(source, "<module>", "exec")
    skip = set()
    global_writes = set()
    for child in symbols.get_children():
        if child.get_name() == function_name and child.get_type() == "function":
            skip = {
                symbol.get_name()
                for symbol in child.get_symbols()
                if symbol.is_assigned() or symbol.is_parameter()
                if not symbol.is_global()
            }
            global_writes = coupling._global_writes(child)
            break
    entries = []
    for values, node in dispatch.find_chain(tree, function_name, discriminant):
        dependencies = tuple(sorted(
            dispatch._branch_deps(node, defined, skip, global_writes)
        ))
        if dependencies:
            entries.append((tuple(values), dependencies, node))
    return tree, entries, symbols, defined


# --- the shape of the weld -------------------------------------------------- #

def weld_shape(source, function_name, discriminant="name"):
    """Return a dispatch chain's weld shape, preserving branch identity.

    {
      'welded':       {target_value -> [dependency names]},
      'branch_count': number of physical welded if/elif branches,
      'by_kind':      {kind -> [(physical_branch_refcount, name), ...]},
      'sizes':        {dependency_count -> physical_branch_count},
    }
    """
    tree, entries, _, _ = _welded_entries(source, function_name, discriminant)
    return _shape_from_entries(entries, _classify_kinds(tree))


def _shape_from_entries(entries, kind):
    welded = {
        value: list(dependencies)
        for values, dependencies, _ in entries
        for value in values
    }
    frequency = Counter()
    for _, dependencies, _ in entries:
        frequency.update(dependencies)

    by_kind = {}
    for name, count in frequency.items():
        by_kind.setdefault(kind.get(name, "unknown"), []).append((count, name))
    for names in by_kind.values():
        names.sort(key=lambda item: (-item[0], item[1]))

    sizes = dict(sorted(
        Counter(len(dependencies) for _, dependencies, _ in entries).items()
    ))
    return {
        "welded": welded,
        "branch_count": len(entries),
        "by_kind": by_kind,
        "sizes": sizes,
    }


# --- the order it unlocks --------------------------------------------------- #

def _validated_names(value, label, dependencies):
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"{label} must be an iterable of dependency-name strings, not text"
        )
    try:
        names = set(value)
    except TypeError as exc:
        raise TypeError(
            f"{label} must be an iterable of dependency-name strings"
        ) from exc
    if any(type(name) is not str for name in names):
        raise TypeError(f"every {label} member must be a string")
    unknown = names - dependencies
    if unknown:
        raise ValueError(
            f"unknown {label} dependencies: " + ", ".join(sorted(unknown))
        )
    return names


def _loads_outside_helper_and_branch(load_sites, name, helper, branch):
    """Whether `name` is loaded anywhere the proposed move would leave behind."""
    allowed = {id(node) for node in ast.walk(helper)}
    for statement in branch.body:
        allowed.update(id(node) for node in ast.walk(statement))
    return any(id(node) not in allowed for node in load_sites.get(name, ()))


def _confirmed_helper_analysis(tree, symbols, defined, names):
    """Local dependency facts for caller-confirmed top-level helpers."""
    definitions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    function_tables = {
        child.get_name(): child
        for child in symbols.get_children()
        if child.get_type() == "function"
    }
    analysis = {}
    for name in names:
        definition = definitions.get(name)
        table = function_tables.get(name)
        if definition is None or table is None:
            continue
        global_writes = coupling._global_writes(table)
        dependencies = {
            dependency
            for dependency in coupling._global_loads(table)
            if (dependency in defined or dependency in global_writes)
            and (dependency not in coupling._BUILTINS or dependency in global_writes)
            and (dependency != name or dependency in global_writes)
        }
        dependencies.update(_definition_time_dependencies(definition, defined))
        analysis[name] = {
            "definition": definition,
            "dependencies": dependencies,
            "global_writes": global_writes,
        }
    return analysis


def _co_movable_helpers(
    tree,
    entries,
    function_name,
    cheap,
    chosen_seam,
    helper_analysis,
    kind,
):
    """Externally confirmed helpers that also pass every local proof."""
    load_sites = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            load_sites.setdefault(node.id, []).append(node)

    uses = {}
    for _, dependencies, branch in entries:
        for name in dependencies:
            uses.setdefault(name, []).append(branch)

    movable = set()
    for name, branches in uses.items():
        if (
            len(branches) != 1
            or name not in helper_analysis
            or name == function_name
            or not name.startswith("_")
            or kind.get(name) not in ("function", "async-fn")
        ):
            continue

        facts = helper_analysis[name]
        global_writes = facts["global_writes"]
        # A write is location-sensitive even when the binding started as a
        # literal: moving `global STATE; STATE = ...` changes which module owns
        # the mutation. That needs an explicit state seam, never a tier-2 move.
        if global_writes:
            continue
        helper_dependencies = facts["dependencies"]
        if not helper_dependencies <= (cheap | chosen_seam):
            continue
        if _loads_outside_helper_and_branch(
            load_sites, name, facts["definition"], branches[0]
        ):
            continue
        movable.add(name)
    return movable


def _definition_time_dependencies(function, defined):
    """Module names evaluated while a function definition is created.

    `symtable` reports names loaded by the body. Defaults, decorators, and
    annotations run in the containing scope instead, so omitting them can label
    a helper self-contained when its definition still needs live state.
    """
    return {
        name
        for name in coupling._definition_time_loads(function)
        if name in defined
        and name not in coupling._BUILTINS
        and name != function.name
    }


def unlock_tiers(
    source,
    function_name,
    seam=frozenset(),
    discriminant="name",
    *,
    exclusive_helpers=frozenset(),
):
    """Partition welded dispatch targets into a cumulative unlock staircase.

    tier0: every dependency has a proven cheap-carry kind
    tier1: + the explicitly chosen seam name-set
    tier2: + externally confirmed exclusive helpers that pass local proofs
    tier3: the remainder — genuine shared-service seams

    `services` counts each physical tier-3 branch once. The tier sets contain
    dispatch target values, which may outnumber physical branches. CARVE cannot
    discover consumers in other modules, so helpers never enter tier 2 unless
    the caller names them in `exclusive_helpers`.
    """
    tree, entries, symbols, defined = _welded_entries(
        source, function_name, discriminant
    )
    kind = _classify_kinds(tree)
    return _unlock_tiers_from_analysis(
        source,
        tree,
        entries,
        symbols,
        defined,
        function_name,
        seam,
        kind,
        exclusive_helpers,
    )


def _unlock_tiers_from_analysis(
    source,
    tree,
    entries,
    symbols,
    defined,
    function_name,
    seam,
    kind,
    exclusive_helpers,
):
    dependencies = {
        dependency
        for _, branch_dependencies, _ in entries
        for dependency in branch_dependencies
    }
    confirmed_helpers = _validated_names(
        exclusive_helpers,
        "exclusive_helpers",
        dependencies,
    )
    helper_analysis = _confirmed_helper_analysis(
        tree,
        symbols,
        defined,
        confirmed_helpers,
    )
    seam_dependencies = dependencies | {
        dependency
        for facts in helper_analysis.values()
        for dependency in facts["dependencies"]
    }
    chosen_seam = _validated_names(seam, "seam", seam_dependencies)
    cheap = {name for name, name_kind in kind.items() if name_kind in CHEAP_KINDS}
    helpers = _co_movable_helpers(
        tree,
        entries,
        function_name,
        cheap,
        chosen_seam,
        helper_analysis,
        kind,
    )

    t0, t1, t2, t3 = set(), set(), set(), set()
    services = Counter()
    for values, branch_dependencies, _ in entries:
        targets = set(values)
        required = set(branch_dependencies)
        if required <= cheap:
            t0.update(targets)
        elif required <= (cheap | chosen_seam):
            t1.update(targets)
        elif required <= (cheap | chosen_seam | helpers):
            t2.update(targets)
        else:
            t3.update(targets)
            services.update(
                dependency
                for dependency in branch_dependencies
                if dependency not in cheap
                and dependency not in chosen_seam
                and dependency not in helpers
            )

    return {
        "t0": t0,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "services": services,
    }


# --- human-readable report -------------------------------------------------- #

_KIND_ORDER = [
    "import-alias",
    "constant",
    "regex-const",
    "data-literal",
    "assign",
    "computed-const",
    "shared-state",
    "class",
    "async-fn",
    "function",
    "unknown",
]

_CARRY = {
    "import-alias": "re-import (proven to terminate at an import)",
    "constant": "move to a config module, import it",
    "regex-const": "move to a shared module, import it",
    "data-literal": "move the table to a data module, import it",
    "assign": "inspect — static analysis cannot prove this value is cheap",
    "computed-const": "inspect or inject — this may be a live handle",
    "shared-state": "inject (one shared lock / handle)",
    "class": "import or inject (a type dependency)",
    "async-fn": "INJECT — behaviour a seam must carry",
    "function": "INJECT — behaviour a seam must carry",
    "unknown": "investigate — not proven at module level",
}


def report(
    source,
    function_name,
    seam=frozenset(),
    discriminant="name",
    *,
    exclusive_helpers=frozenset(),
):
    """Render the by-kind weld shape and cumulative unlock staircase."""
    tree, entries, symbols, defined = _welded_entries(
        source, function_name, discriminant
    )
    kind = _classify_kinds(tree)
    shape = _shape_from_entries(entries, kind)
    welded = shape["welded"]
    by_kind = shape["by_kind"]
    sizes = shape["sizes"]
    branch_count = shape["branch_count"]
    if not welded:
        return f"{function_name}(): no welded branches to shape"

    target_label = "target" if len(welded) == 1 else "targets"
    branch_label = "branch" if branch_count == 1 else "branches"
    name_count = sum(len(names) for names in by_kind.values())
    out = [
        (
            f"{function_name}(): {len(welded)} welded {target_label} "
            f"across {branch_count} physical {branch_label}, "
            f"{name_count} distinct internal names carried by the weld"
        )
    ]
    thin = sum(count for dependency_count, count in sizes.items() if dependency_count <= 2)
    out.append(f"  {thin}/{branch_count} welded branches need only 1-2 names\n")

    out.append("the internal names, by kind (cheapest to carry first):")
    for name_kind in _KIND_ORDER:
        if name_kind not in by_kind:
            continue
        items = by_kind[name_kind]
        refs = sum(count for count, _ in items)
        out.append(
            f"  [{name_kind}] {len(items)} names, {refs} refs  "
            f"-- {_CARRY.get(name_kind, '')}"
        )

    tiers = _unlock_tiers_from_analysis(
        source,
        tree,
        entries,
        symbols,
        defined,
        function_name,
        seam,
        kind,
        exclusive_helpers,
    )
    t0, t1, t2, t3 = (
        tiers["t0"],
        tiers["t1"],
        tiers["t2"],
        tiers["t3"],
    )
    out.append("\ncumulative unlock staircase:")
    out.append(
        f"  tier 0  proven cheap-carry only                 : "
        f"{len(t0):>3}   (cum {len(t0)})"
    )
    out.append(
        f"  tier 1  + the explicitly chosen seam            : "
        f"{len(t1):>3}   (cum {len(t0 | t1)})"
    )
    out.append(
        f"  tier 2  + confirmed exclusive local helpers     : "
        f"{len(t2):>3}   (cum {len(t0 | t1 | t2)})"
    )
    out.append(
        f"  tier 3  genuine shared-service seams remain     : {len(t3):>3}"
    )
    if tiers["services"]:
        ordered = sorted(
            tiers["services"].items(),
            key=lambda item: (-item[1], item[0]),
        )
        out.append(
            "    tier-3 services: "
            + ", ".join(f"{count}x {name}" for name, count in ordered)
        )
    return "\n".join(out)

# --- transitive cluster closure ---------------------------------------------- #

def cluster_closure(source, names, defined=None, tree=None):
    """Every module-level name reachable from `names`, following definitions.

    A dispatch target that needs one private helper looks like a two-file move.
    It is not, if that helper needs six more and each of those needs four. The
    direct dependency count answers "what does this touch", and the number people
    actually plan with is "what must MOVE TOGETHER" -- which is the transitive
    closure and is routinely an order of magnitude larger.

    This was written because a real queue entry read "+2 helpers" and the honest
    figure was fifteen: the tool's helper pulled a memory-triage subsystem behind
    it. Planning an extraction off the direct count schedules a week of work as an
    afternoon.

    Returns the closure EXCLUDING the seeds, so `len()` is "how many extra
    definitions come along".

    `tree` is an optional pre-parsed module for `source`. Parsing dominates the
    cost on a large file, and a caller asking for many closures over the SAME
    source (`cluster_cost` does, once per welded target) was re-parsing it every
    call. Passing the tree in is purely a saving: `source` stays the parameter
    that defines the answer, and when `tree` is omitted this parses exactly as
    before. Never pass a tree parsed from different text -- the two must
    describe the same module.
    """
    tree = ast.parse(source) if tree is None else tree
    if defined is None:
        defined = {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
    bodies = {
        n.name: n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    seen, stack = set(), list(names)
    while stack:
        current = stack.pop()
        node = bodies.get(current)
        if node is None:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                nm = sub.id
                if nm in defined and nm not in seen and nm not in names:
                    seen.add(nm)
                    stack.append(nm)
    return seen


def cluster_cost(source, function_name, discriminant="name"):
    """Per welded target: direct deps vs what must actually move with it.

    Returns {target: {"direct": N, "closure": M, "names": sorted}}. `direct` is
    what the dispatch analysis reports; `closure` is the honest planning number.

    The module is parsed ONCE and the tree reused for every target. It used to be
    re-parsed per target, which is invisible on a test fixture and quadratic in
    practice: on Maxima's 1.1 MB dispatcher, 23 welded targets meant 23 full
    parses and 46 s of wall clock -- over half the loop's entire gate sweep, for
    an answer that does not depend on how many times the text is read.
    """
    _, welded = dispatch.classify_chain(source, function_name, discriminant=discriminant)
    tree = ast.parse(source)
    defined = {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    out = {}
    for target, deps in welded.items():
        fns = [d for d in deps if d in defined]
        closure = cluster_closure(source, set(fns), defined, tree=tree) if fns else set()
        out[target] = {
            "direct": len(deps),
            "closure": len(set(fns) | closure),
            "names": sorted(set(fns) | closure),
        }
    return out


# --- carrying a VALUE is not the same as carrying no code -------------------

def _module_assign_targets(tree):
    """{name: [assignment nodes]} for every module-level binding, including the
    ones nested inside `if` / `try` at module scope — which is precisely where a
    conditional rebinding hides."""
    out = {}

    def walk(body):
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        out.setdefault(t.id, []).append(node)
            # Module-level control flow still binds at module level.
            for attr in ("body", "orelse", "finalbody"):
                inner = getattr(node, attr, None)
                if inner and isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                    walk(inner)
            for handler in getattr(node, "handlers", []):
                walk(handler.body)

    walk(tree.body)
    return out


def value_carry_risk(source, names, tree=None):
    """Why a module-level VALUE may be unsafe to copy into another module.

    `cluster_cost` answers "what code must move with this target". A closure of
    zero reads as "nothing comes along, this is free" — and for a dispatch target
    welded only to module-level values, that reading is wrong in a way that
    compiles, imports, passes tests, and is wrong at run time.

    Three ways a value refuses to be copied, each observed in real source:

      rebound       assigned in more than one place, so the visible first
                    assignment is not the value the program actually runs with.
                    (`TIMEZONE = a or b` then rebound inside `if:` eleven lines
                    later — copy the first line and the override silently stops
                    applying.)
      file-relative the right-hand side mentions `__file__`, whose meaning is the
                    file it is written in. An identical copy in a new module is a
                    DIFFERENT path — same bytes, different value.
      environment   the right-hand side branches on the environment (`os.getenv`,
                    `Path.exists`, `os.environ`), so it is a decision made at
                    import time, not a constant. Copying it re-decides, and the
                    two modules can disagree.

    Returns {name: [reasons]}, empty list meaning nothing objected. Names with no
    module-level assignment at all are reported as `undefined-at-module-level`
    rather than silently omitted — a missing name is a finding, not a pass.
    """
    tree = ast.parse(source) if tree is None else tree
    assigns = _module_assign_targets(tree)

    def direct(name, nodes):
        found = []
        if len(nodes) > 1:
            found.append(f"rebound ({len(nodes)} module-level assignments)")
        for node in nodes:
            value = getattr(node, "value", None)
            if value is None:
                continue
            for sub in ast.walk(value):
                if isinstance(sub, ast.Name) and sub.id == "__file__":
                    found.append("file-relative (__file__ means a different path once moved)")
                elif isinstance(sub, ast.Attribute) and sub.attr in ("getenv", "environ", "exists"):
                    found.append(f"environment-dependent (.{sub.attr} decides at import time)")
                elif isinstance(sub, ast.Name) and sub.id in ("getenv", "environ"):
                    found.append("environment-dependent (getenv decides at import time)")
        return found

    def refs(nodes):
        """Module-level names this binding's right-hand side reads."""
        out = set()
        for node in nodes:
            value = getattr(node, "value", None)
            if value is None:
                continue
            for sub in ast.walk(value):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in assigns:
                    out.add(sub.id)
        return out

    def resolve(name, seen):
        # Risk PROPAGATES. The first version of this function checked only a
        # binding's own right-hand side and pronounced
        # `SOUL_FILE = BOT_DIR.parent / "soul" / "x.md"` safe to carry -- while
        # `BOT_DIR` is `Path(__file__).parent`. The value is file-relative at one
        # remove, and a checker that stops at the first hop hands out exactly the
        # false green it was written to prevent.
        if name in seen:
            return []                      # cycle: already accounted for
        seen = seen | {name}
        nodes = assigns.get(name)
        if not nodes:
            return ["undefined-at-module-level"]
        found = list(direct(name, nodes))
        for parent in sorted(refs(nodes)):
            for reason in resolve(parent, seen):
                if reason == "undefined-at-module-level":
                    continue
                found.append(f"{reason}  [via {parent}]")
        return found

    risks = {}
    for name in names:
        seen_reasons, ordered = set(), []
        for reason in resolve(name, frozenset()):
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                ordered.append(reason)
        risks[name] = ordered
    return risks


# Techniques a welded VALUE can be carried by, cheapest first. The point of
# naming them is that `value_carry_risk` says why a copy is unsafe and stops --
# leaving "so what do I do instead?" to be re-derived by hand every time.
CARRY_COPY = "copy"          # restate the literal in the new module
CARRY_REDERIVE = "re-derive"  # recompute locally; safe only if it cannot disagree
CARRY_BIND = "bind"          # the origin hands the computed value over, once
CARRY_INJECT = "inject"      # a live handle: forward at call time, never capture


def carry_strategy(source, names, tree=None):
    """For each name: the technique that actually carries it, and why.

    `value_carry_risk` answers "is copying this safe?" and stops at no. That
    leaves the useful half unanswered, and it is the half that takes judgement:
    *re-derive, bind, or inject?* Three consecutive real extractions arrived at
    three different answers for values that all looked alike from the risk report,
    so the reasoning is written down here instead of being redone each time.

    The rule underneath is one question -- **does this thing's derivation mean the
    same in the new file?**

      import           yes. Imports follow you: re-running `import x` in another
                       module of the same process is a sys.modules cache hit and
                       cannot disagree.  -> re-derive
      plain literal    yes. Nothing to disagree with.  -> copy
      `__file__` path  NO. The same text names a different path.  -> bind
      env / filesystem NO. It is a decision made at import time, and the two
                       modules can be asked in different conditions.  -> bind
      rebound value    NO. The visible assignment is not the value in use.  -> bind
      live handle      NO, and worse -- there is only one, and it owns a
                       connection or a lock.  -> inject

    Returns {name: {"strategy": str, "why": [reasons]}}. Conservative by
    construction: anything not provably safe to copy is at least `bind`, because
    a wrong `copy` compiles and a wrong `bind` fails loudly at startup.
    """
    tree = ast.parse(source) if tree is None else tree
    risks = value_carry_risk(source, names, tree=tree)
    kinds = _classify_kinds(tree)

    out = {}
    for name in names:
        reasons = risks.get(name, [])
        kind = kinds.get(name)
        if kind in ("function", "async-fn", "class"):
            # A function is not a value and was never a candidate for `copy`.
            # The first version fell through to the value logic and reported
            # `bind` because of "undefined-at-module-level" -- technically a
            # refusal, but with a reason that is simply untrue: the function is
            # defined, it is just not an ASSIGNMENT, and `value_carry_risk` only
            # inspects assignments. A wrong reason is worse than no answer,
            # because the next reader debugs the wrong thing. Found on the tool's
            # first real use against a service cluster.
            out[name] = {"strategy": CARRY_INJECT,
                         "why": [f"a {kind} -- behaviour, not a value. Either move it "
                                 f"(only with whole-program proof it has no other "
                                 f"consumer) or pass it through a seam"]}
            continue
        if kind in ("import", "import-alias"):
            out[name] = {"strategy": CARRY_REDERIVE,
                         "why": ["an import follows you: re-running it in the same "
                                 "process is a cache hit and cannot disagree"]}
            continue
        if kind == "shared-state":
            out[name] = {"strategy": CARRY_INJECT,
                         "why": ["a live handle -- there is only one, and it owns a "
                                 "lock or a connection, so it must be forwarded at "
                                 "call time rather than captured"]}
            continue
        if not reasons:
            out[name] = {"strategy": CARRY_COPY,
                         "why": ["nothing objects: no rebinding, no __file__, no "
                                 "environment decision"]}
            continue
        out[name] = {"strategy": CARRY_BIND, "why": list(reasons)}
    return out
