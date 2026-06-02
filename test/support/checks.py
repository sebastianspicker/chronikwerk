from __future__ import annotations


def check(condition: bool, message: object = "assertion failed") -> None:
    if not condition:
        raise AssertionError(message)
