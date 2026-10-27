"""Assertions that also preserve optional-result narrowing in LSP tests."""


def required[T](value: T | None) -> T:
    assert value is not None
    return value
