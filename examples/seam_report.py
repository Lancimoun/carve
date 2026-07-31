"""Run a complete seam report against a small, checked-in source fixture."""

from carve import seam


SOURCE = """\
import re
import threading

_re = re
MODEL = "sonnet"
_lock = threading.Lock()

def load_memory():
    return {}

def _one_off(value):
    return value + 1

def run_tool(name, inputs):
    if name == "leaf":
        return inputs["x"]
    elif name == "alias":
        return _re.findall("a", inputs["text"])
    elif name == "model":
        return MODEL
    elif name in ("memory_read", "memory_refresh"):
        return load_memory()
    elif name == "format":
        return _one_off(inputs["x"])
    elif name == "stateful":
        with _lock:
            return 1
    return None
"""


def main():
    print(seam.report(
        SOURCE,
        "run_tool",
        seam={"load_memory"},
        exclusive_helpers={"_one_off"},
    ))


if __name__ == "__main__":
    main()
