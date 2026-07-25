<h1 align="center">CARVE</h1>

<p align="center"><strong>Prove a refactor changed nothing.</strong><br/>
Scope-aware coupling and dispatch-chain analysis for carving up large Python modules — safely.</p>

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
  <a href="#the-three-traps-it-gets-right">The traps</a> ·
  <a href="#provenance">Provenance</a> ·
  <a href="#install--run">Install</a>
</p>

---

## The problem

You have one enormous module. You want to break it up. Two questions decide whether that goes well, and both are usually answered by vibes:

1. **What can I move first?** — usually guessed by topic ("let's do the file tools")
2. **Did I break anything?** — usually answered by "the tests pass"

CARVE answers both by **measuring**, and it never runs the code it analyses — so it is safe to point at modules that need credentials or hit the network at import time.

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

`carve.dispatch` makes **each branch a unit** and classifies them individually, so a giant dispatcher becomes a ranked worklist instead of a wall.

### Sizing the seam — the order a weld comes apart

`dispatch` tells you a branch is welded. It does not tell you *how hard*, and the names a weld references are wildly unequal in cost. An `import`-alias (`_re = re`) re-imports for free; a constant or a lookup table moves to a config module; a shared function is the real seam a dependency injection has to carry. Count the welded branches and they all look equally stuck; weigh each name by *how it is carried* and a plan appears.

`carve.seam` does the weighing, and turns the count into a **cumulative unlock staircase**:

```python
from carve import seam
print(seam.report(open("monolith.py").read(), "run_tool",
                  seam={"load_memory", "save_memory", "_mem_write_lock", "DEFAULT_MEM"}))
# run_tool(): 79 welded branches, 67 distinct internal names carried by the weld
#   63/79 welded branches need only 1-2 names
#
# cumulative unlock staircase:
#   tier 0  cheap-carry only (config/data, no seam) :  19   (cum 19)
#   tier 1  + the one injected seam                 :  26   (cum 45)
#   tier 2  + each branch's single-use helper       :  24   (cum 69)
#   tier 3  genuine shared-service seams remain     :  10
```

That is the real shape of the 219-branch dispatcher above, once the leaves are gone. "79 welded, needs an architecture decision" becomes "one injected accessor unlocks **45** of them; only **10** need genuine dependency injection." The scary number was hiding a plan.

---

## API

Four modules, standard-library only. Every function takes source text or a path and returns data — nothing is imported or executed.

| Module | Function | What it answers |
|---|---|---|
| **`coupling`** | `classify(src)` | Splits top-level functions into free vs welded, with dependency counts |
| | `report(src)` | The human-readable summary shown above |
| | `defined_names(src)` · `imported_names(src)` | The two name sets the free/welded split is built from |
| **`dispatch`** | `find_chain(src, func)` | Locates the `if/elif` dispatch chain inside one big function |
| | `classify_chain(src, func)` | Classifies each **branch** as free or welded, per-branch deps |
| | `report(src, func)` | Ranked worklist for carving up a single dispatcher |
| **`resolve`** | `unresolved_names(src)` | Names a module uses but never imports or defines |
| | `check_path(path)` · `check_package(dir)` | The same check across a file or a whole package |
| **`seam`** | `weld_shape(src, func)` | The welded names grouped by *kind* — how costly each is to carry |
| | `unlock_tiers(src, func, seam=…)` | The cumulative staircase: what unlocks with a config move, one seam, then co-moved helpers |
| | `report(src, func, seam=…)` | The human-readable weld shape + unlock staircase |

---

## The three traps it gets right

**1 — An import is not a weld.** `os`, `requests`, `Path` are module-level names, but they *follow you*: copy the import line and the function still works. Only what the module **defines** stays behind. Count imports as welds and every function reports as stuck.

**2 — Scope matters.** A function with a local `total` shadowing a module-level `total` depends on nothing. A bare `ast` walk sees a `Name` load and reports a weld that isn't there — sending you to build a seam you don't need. CARVE uses `symtable`, so locals, closure cells and genuine global reads are told apart.

**3 — The `orelse` trap.** `ast.walk` on an `if` descends into `orelse` — which in an if/elif chain is *the entire rest of the chain*. So branch #1 appears to depend on everything #2…#219 do, and #219 on nothing. The counts slide smoothly down the chain and look like a real finding about layering. They are a finding about the walker: **a number that decreases neatly with position is measuring position.** Every function in `dispatch` walks `node.body` only.

---

## free ≠ import-complete

This is the pair that matters, and the reason `resolve` exists.

`coupling` ignores imports **by design** — that is what makes a function movable. But the origin module's import block was silently satisfying those names, and the destination has its own. So a function can be perfectly free and still `NameError` in its new home.

This happened for real. A `zip_files` function calling `Path(src)` moved into a module with no `from pathlib import Path`. A surface contract, twelve behavioural tests and a byte-for-byte differential were **all green** — because none of them *called* it. It would have failed the first time a human asked the program to zip something.

> **Run both checks, or ship the gap.**

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
python -m unittest discover tests    # 47 tests, no installs
```

The CI badge above runs that exact command on every push. Because CARVE has zero dependencies, a green run with **no `pip install` step** is itself the proof of the zero-dependency claim.

---

<p align="center"><sub>MIT · Built by <a href="https://github.com/Lancimoun">Architect L.</a> with Claude Code</sub></p>
