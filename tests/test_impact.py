"""`tools/impact.py`: the v1.0 impact classes and regression gate (its own self-check)."""

import runpy
from pathlib import Path


def test_impact_self_check():
    runpy.run_path(str(Path(__file__).parents[1] / "tools" / "impact.py"), run_name="__main__")
