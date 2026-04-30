from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pandas as pd

from agent.recipe import _validate_custom_python  # reuse AST scan


SAFE_BUILTINS = {
    name: getattr(__builtins__, name) if hasattr(__builtins__, name) else __builtins__[name]
    for name in [
        "len", "range", "sum", "min", "max", "abs", "round", "sorted",
        "enumerate", "zip", "dict", "list", "tuple", "set",
        "str", "int", "float", "bool",
    ]
}

TIMEOUT_SECONDS = 5.0


class SandboxError(RuntimeError):
    """Raised when sandboxed code fails, times out, or returns a bad type."""


def run_user_code(code: str, df_in: pd.DataFrame) -> pd.DataFrame:
    """Execute `code` in a restricted namespace; return its DataFrame result.

    Convention: the code receives `df` (input), `pd`, `np`, and must assign the
    final DataFrame to a variable named `df` (overwriting the input is fine).
    """
    _validate_custom_python(code)

    namespace: dict[str, Any] = {
        "__builtins__": SAFE_BUILTINS,
        "pd": pd,
        "np": np,
        "df": df_in.copy(),
    }
    interrupt = {"fired": False}

    def _trip() -> None:
        interrupt["fired"] = True

    timer = threading.Timer(TIMEOUT_SECONDS, _trip)
    timer.start()
    try:
        try:
            exec(compile(code, "<custom_python>", "exec"), namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"{type(exc).__name__}: {exc}") from exc
        if interrupt["fired"]:
            raise SandboxError(
                f"custom_python timed out (best-effort {TIMEOUT_SECONDS}s)."
            )
    finally:
        timer.cancel()

    result = namespace.get("df")
    if not isinstance(result, pd.DataFrame):
        raise SandboxError(
            f"custom_python must leave a pandas.DataFrame in `df` (got {type(result).__name__})."
        )
    return result