"""CARVE: static, conservative planning for splitting large Python modules.

It measures four questions without importing or executing the source:
  coupling.classify(source) -> which functions reference module-owned names
  dispatch.classify_chain(src, "run_tool") -> the same for dispatch targets
  resolve.unresolved_names(source) -> which names are unresolved
  seam.unlock_tiers(src, "run_tool") -> the shape and proof-gated carry order

These are extraction-planning signals, not proof of behavioral equivalence.
"""

from . import coupling, dispatch, resolve, seam  # noqa: F401

__all__ = ["coupling", "dispatch", "resolve", "seam"]
__version__ = "0.2.0"
