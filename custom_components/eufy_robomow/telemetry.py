"""Interpret task flags in complete E15 local status responses."""

from typing import Any


def task_active(dps: dict[str, Any]) -> bool | None:
    """An idle E15 omits DP1/DP2 until the first command after a connection reset.

    Only apply this observed idle convention to a complete status shape. Empty
    responses and partial notifications remain unknown and never end a session.
    Explicit flags always take precedence. This is not evidence of dock arrival.
    """
    value = dps.get("1")
    if type(value) is bool:
        return value
    if "1" not in dps and "2" not in dps and {"8", "110", "118"} <= dps.keys():
        if type(dps["8"]) is int and type(dps["110"]) is int and dps["118"] == 0:
            return False
    return None
