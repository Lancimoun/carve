"""carve — prove a refactor changed nothing.

Two questions, measured instead of guessed:
  coupling.classify(source) -> what can move today, and what needs a seam first
  dispatch.classify_chain(src, "run_tool") -> same, for branches inside ONE function
  resolve.unresolved_names(source) -> does the extracted code actually resolve

Battle-tested on a 30,014-line Python module: 82 of 219 dispatch branches
relocated with zero regressions.
"""

from . import coupling, dispatch, resolve  # noqa: F401

__all__ = ["coupling", "dispatch", "resolve"]
__version__ = "0.1.0"
