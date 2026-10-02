from __future__ import annotations

import os


def pytest_sessionstart(session):
    os.environ["OPENCRBASIC_STRICT"] = "1"
