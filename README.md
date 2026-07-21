# CARVE

**Prove a refactor changed nothing.**

You have one enormous module. You want to break it up. Two questions decide whether that goes well, and both are usually answered by vibes:

1. **What can I move first?** — usually guessed by topic ("let's do the file tools")
2. **Did I break anything?** — usually answered by "the tests pass"

CARVE answers both by measuring.

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

## Why coupling, not domain

A function is **free** if it depends on nothing its module *defines* — it moves as a pure relocation. It is **welded** if it does — it needs a seam first (dependency injection, or moving what it depends on), which is a design decision, not a mechanical one.

That distinction cuts *across* domains, and domains don't predict it. On the 30,014-line module this was extracted from, the file and web tools happened to be free; the memory tools were welded to 20 internals and could not move at all. The plan said "extract by domain" and sent the work straight at a wall. `coupling.classify` would have said so in one call.

## Two traps it gets right

**An import is not a weld.** `os`, `requests`, `Path` are module-level names, but they follow you — copy the import line and the function works. Only what the module *defines* stays behind. Counting imports as welds reports every function as stuck.

**Scope matters.** A function with a local `total` shadowing a module-level `total` depends on nothing. A bare `ast` walk sees a `Name` load and reports a weld that isn't there — sending you to build a seam you don't need. CARVE uses `symtable`, so locals, closure cells and genuine global reads are told apart.

## free ≠ import-complete

This is the pair that matters, and the reason `resolve` exists.

`coupling` ignores imports **by design** — that's what makes a function movable. But the origin module's import block was silently satisfying those names, and the destination has its own. So a function can be perfectly free and still `NameError` in its new home.

This happened for real. A `zip_files` function calling `Path(src)` moved into a module with no `from pathlib import Path`. A surface contract, twelve behavioural tests and a byte-for-byte differential were **all green** — because none of them *called* it. It would have failed the first time a human asked the program to zip something.

Run both checks, or ship the gap.

## Provenance

Extracted from carving up a real 30,014-line Python module: **82 of 219 dispatch branches relocated, zero regressions**, every move proven byte-identical against its original. The tools here are the ones that made that safe, generalised out of it.

They are also the ones that caught the mistakes. In a single session the measuring instruments were the defect **six times** — dependency counts that secretly measured position (`ast.walk` descending into `orelse`), a classifier whose fallback bucket inflated "silent errors" from 133 to 399, a differential baseline typed from memory that failed against correct code. Every one looked like a finding about the code. The rule that came out of it, and the reason this library is small:

> **An instrument that reports many failures at once is presumed broken until one failure is confirmed by hand.**

## Install

Nothing to install. Stdlib only — `ast`, `symtable`, `builtins`. It never imports or executes the code it analyses, so it is safe to point at modules that need credentials or hit the network at import time.

```
python -m unittest discover tests
```

MIT. Built by [Architect L.](https://github.com/Lancimoun) with Claude Code.
