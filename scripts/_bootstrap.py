"""Make ``src/`` importable when running scripts from a checkout.

Also holds the tiny argument helpers the scripts share, so ``--set`` means the
same thing everywhere.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def parse_value(text: str):
    """Turn a CLI string into the type a config would have held."""
    if text in ("none", "null"):
        return None
    if text in ("true", "false"):
        return text == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def parse_assignments(assignments: list[str]) -> dict:
    """``["a=1", "b=x"]`` -> ``{"a": 1, "b": "x"}``."""
    params = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise SystemExit(f"--set expects NAME=VALUE; got {assignment!r}")
        name, raw = assignment.split("=", 1)
        params[name] = parse_value(raw)
    return params
