<h1 align="center">CARVE</h1>

<p align="center"><strong>Measure the seams before you move the code.</strong><br/>
Conservative, scope-aware planning for carving up large Python modules.</p>

<p align="center">
  <a href="https://github.com/Lancimoun/carve/actions/workflows/ci.yml"><img src="https://github.com/Lancimoun/carve/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-zero%20(stdlib)-5ed7bd?style=flat-square" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/analysis-only%20·%20never%20executes%20your%20code-8b5cf6?style=flat-square" alt="Analysis only">
  <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License: MIT">
</p>

<p align="center">
  <a href="#what-carve-measures">What it measures</a> ·
  <a href="#api">API</a> ·
  <a href="#the-traps-it-gets-right">The traps</a> ·
  <a href="#limits">Limits</a> ·
  <a href="#provenance">Provenance</a> ·
  <a href="#install--run">Install</a>
</p>

---

## The problem

You have one enormous module. You want to break it up. Two planning questions decide whether that starts well, and both are usually answered by vibes:

1. **What can I move first?** — usually guessed by topic ("let's do the file tools")
2. **What must move or be injected with it?** — usually discovered halfway through the extraction

CARVE measures both without importing or executing the code it analyses, so discovery cannot trigger credentials, network calls, or import-time side effects. It produces a conservative worklist; behavioral proof still belongs to tests and differentials after the move.

```python
from carve import coupling, resolve

print(coupling.report(open("monolith.py").read()))
# 487 top-level functions
#   free     89  (18%) - movable as pure relocations
#   welded  398  (82%) - need a seam first
#     1-2 deps    167
#     3-5 deps    116
#     6+ deps     115

resolve.check_package("mypackage/")
# ["files.py: zip_files() uses 'Path', which the module never imports or defines"]
```

---

## What CARVE measures

A function is **free** if it depends on nothing its module *defines* — it moves as a pure relocation. It is **welded** if it does — it needs a seam first (dependency injection, or moving what it depends on), which is a design decision, not a mechanical one.

That distinction is the whole game, and it cuts **across** domains — domains do not predict it. On the 30,014-line module CARVE was extracted from, the file and web tools happened to be free; the memory tools were welded to 20 internals and could not move at all. The plan said "extract by domain" and sent the work straight at a wall. `coupling.classify` would have said so in one call.

### The chain hidden inside one function

The worst monoliths do not scatter their mass across many functions — they hide it in **one**. CARVE's real target was a **7,789-line `run_tool(name, inputs)`** holding a 219-branch `if name == "…" / elif …` chain. To a top-level view that is a single welded function with 70 dependencies: technically true, completely useless — the 219 units you actually want to move are invisible.

`carve.dispatch` makes **each dispatch target a unit** and classifies it individually, while still reporting when several targets share one physical branch. A giant dispatcher becomes an aggregate worklist instead of a wall.

### Sizing the seam — the order a weld comes apart

`dispatch` tells you a target is welded. It does not tell you *how hard*, and the names a weld references are wildly unequal in cost. A **proven** import alias (`import re; _re = re`) re-imports cheaply; a literal can move to a config/data module; a live handle or shared function needs an explicit seam. Count the welded targets and they all look equally stuck; classify only what the syntax proves and a safer plan appears.

Only assignment syntax that proves its own carry strategy enters the cheap tier: direct import roots, immutable scalar literals, literal containers, and absolute stdlib `re.compile(...)` calls whose arguments are themselves literals. Indirect aliases, rebound imports, project-relative `re`, dynamic container members, and dynamic regex construction fail closed.

The checked-in fixture is executable:

```bash
python -m examples.seam_report
```

```text
run_tool(): 6 welded targets across 5 physical branches, 5 distinct internal names carried by the weld
  5/5 welded branches need only 1-2 names

the internal names, by kind (cheapest to carry first):
  [import-alias] 1 names, 1 refs  -- re-import (proven to terminate at an import)
  [constant] 1 names, 1 refs  -- move to a config module, import it
  [shared-state] 1 names, 1 refs  -- inject (one shared lock / handle)
  [function] 2 names, 2 refs  -- INJECT — behaviour a seam must carry

cumulative unlock staircase:
  tier 0  proven cheap-carry only                 :   2   (cum 2)
  tier 1  + the explicitly chosen seam            :   2   (cum 4)
  tier 2  + confirmed exclusive local helpers     :   1   (cum 5)
  tier 3  genuine shared-service seams remain     :   1
    tier-3 services: 1x _lock
```

Tier 2 is intentionally strict. Because one source file cannot prove that another module or repository never imports a private helper, the caller must first confirm that whole-project fact with `exclusive_helpers={"_one_off"}`. CARVE then independently requires the helper to be private, referenced by one **physical** branch, self-contained apart from proven cheap values or the chosen seam, and unused elsewhere in the analysed module. The first candidate counted any function seen in one flattened target as movable; adversarial tests proved that could hide the helper's own lock/state dependencies, definition-time defaults or decorators, a public API consumer, or the dispatcher itself. CARVE now keeps all unconfirmed or locally unproven helpers in tier 3. A less flattering staircase is better than an extraction plan that breaks.

---

## API

Four modules, standard-library only. High-level analyzers take source text; filesystem checks take paths; the low-level discovery helpers explicitly take parsed `ast` trees. Nothing is imported or executed.

| Module | Function | What it answers |
|---|---|---|
| **`coupling`** | `classify(src)` | Splits top-level functions into free vs welded, with dependency counts |
| | `report(src)` | The human-readable summary shown above |
| | `defined_names(tree)` · `imported_names(tree)` | Low-level name sets from a parsed `ast.Module` |
| **`dispatch`** | `find_chain(tree, func)` | Locates the `if/elif` dispatch chain in a parsed `ast.Module` |
| | `classify_chain(src, func)` | Classifies each dispatch **target** as free or welded |
| | `report(src, func)` | Aggregate free/welded targets plus physical-branch count |
| **`resolve`** | `unresolved_names(src)` | Names a module uses but never imports or defines |
| | `check_path(path)` · `check_package(dir)` | The same check across a file or a whole package |
| **`seam`** | `classify_kinds(src)` | Proven carry kinds for supported module-level bindings |
| | `weld_shape(src, func)` | Welded names grouped by kind, with target vs physical-branch counts |
| | `unlock_tiers(src, func, seam=…, exclusive_helpers=…)` | The cumulative staircase: what unlocks with a config move, one seam, then externally confirmed helpers |
| | `report(src, func, seam=…, exclusive_helpers=…)` | The human-readable weld shape + unlock staircase |
| | `cluster_cost(src, func)` | Direct dependencies vs the **transitive closure** — what must actually move together |

---

## The traps it gets right

**1 — An import is not a weld.** `os`, `requests`, `Path` are module-level names, but they *follow you*: copy the import line and the function still works. Only what the module **defines** stays behind. Count imports as welds and every function reports as stuck.

**2 — Scope matters.** A function with a local `total` shadowing a module-level `total` depends on nothing. A bare `ast` walk sees a `Name` load and reports a weld that isn't there — sending you to build a seam you don't need. CARVE uses `symtable`, so locals, closure cells, genuine global reads, and location-sensitive global writes are told apart. It separately scans decorators, defaults, annotations, and type parameters because those execute outside the function body's symbol table.

**3 — The `orelse` trap.** `ast.walk` on an `if` descends into `orelse` — which in an if/elif chain is *the entire rest of the chain*. So branch #1 appears to depend on everything #2…#219 do, and #219 on nothing. The counts slide smoothly down the chain and look like a real finding about layering. They are a finding about the walker: **a number that decreases neatly with position is measuring position.** Every function in `dispatch` walks `node.body` only.

**4 — The operator trap.** `name != "blocked"` contains the same names as `name == "blocked"` and means the opposite thing. CARVE accepts only exact equality to one string or membership in an all-string literal collection; a candidate chain containing `!=`, `not in`, ordering, chained comparisons, or equality to a tuple fails closed instead of splicing its potentially unreachable suffix into an invented inventory.

**5 — The helper trap.** "Used by one target" does not mean "moves with one branch." A helper may call or write shared state, depend on a default or decorator evaluated at definition time, be public, serve another function or module, or be the dispatcher itself. Tier 2 requires caller-confirmed external exclusivity plus a private, self-contained helper used only from one physical branch; everything unproven stays in tier 3.

---

## free ≠ import-complete

This is the pair that matters, and the reason `resolve` exists.

`coupling` ignores imports **by design** — that is what makes a function movable. But the origin module's import block was silently satisfying those names, and the destination has its own. So a function can be perfectly free and still `NameError` in its new home.

This happened for real. A `zip_files` function calling `Path(src)` moved into a module with no `from pathlib import Path`. A surface contract, twelve behavioural tests and a byte-for-byte differential were **all green** — because none of them *called* it. It would have failed the first time a human asked the program to zip something.

> **Run both checks, or ship the gap.**

---

## Limits

CARVE is a static planning instrument. It **does not prove behavioral equivalence**, execute before/after versions, understand dynamic `getattr`/monkey-patching, or discover consumers in other modules and repositories. A leading underscore is a module-level privacy convention, not a whole-program proof. `exclusive_helpers` is therefore an explicit assertion supplied only after a repository-wide consumer search; CARVE still applies its local checks and leaves every unconfirmed helper in tier 3.

Use CARVE to choose and bound the move. Then run `resolve` on the destination, the real test suite, and a differential or contract check over the behavior that moved. The conservative rule is deliberate: an unproven target remains welded instead of receiving a false green light.

---

## Provenance

Extracted from carving up a real 30,014-line Python module: **82 of 219 dispatch branches relocated, zero regressions**, every move proven byte-identical against its original. The tools here are the ones that made that safe, generalised out of it.

They are also the ones that caught the mistakes. In a single session the measuring instruments were the defect **six times** — dependency counts that secretly measured position (`ast.walk` descending into `orelse`), a classifier whose fallback bucket inflated "silent errors" from 133 to 399, a differential baseline typed from memory that failed against correct code. Every one looked like a finding about the code. The rule that came out of it, and the reason this library is small:

> **An instrument that reports many failures at once is presumed broken until one failure is confirmed by hand.**

---

## Install & run

Nothing to install. Standard library only — `ast`, `symtable`, `builtins` — and it never imports or executes the code it analyses.

```bash
git clone https://github.com/Lancimoun/carve
cd carve
python -m unittest discover tests    # 97 tests, no installs
```

The CI badge runs that exact command on every branch push, on pull requests to `main`, and through a manual recovery trigger across Python 3.11–3.14. Because CARVE has zero dependencies, a green matrix with **no install step** is itself the proof of the zero-dependency claim.

---

<p align="center"><sub>MIT · Built by <a href="https://github.com/Lancimoun">Architect L.</a> with Claude Code</sub></p>
