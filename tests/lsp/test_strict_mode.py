"""Strict mode must be active in the process that runs the tests, including every xdist worker
(`tests/conftest.py` sets it at session start, which each worker runs)."""

from __future__ import annotations

import os


def test_strict_mode_is_active_in_this_process():
    assert os.environ.get("OPENCRBASIC_STRICT") == "1", (
        "OPENCRBASIC_STRICT is not 1 in "
        f"{os.environ.get('PYTEST_XDIST_WORKER', 'the main process')}")
