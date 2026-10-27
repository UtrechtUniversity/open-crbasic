
import os
import tempfile
from pathlib import Path

import pytest


def pytest_sessionstart(session):
    os.environ["OPENCRBASIC_STRICT"] = "1"


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="ocb-") as d:
        yield Path(d).resolve()
