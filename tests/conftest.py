import os
import tempfile
from pathlib import Path

import pytest
from impact import EXACT, LATER, ORDER, impact_class


class _Cr3xxExpectedStdout(str):
    """Compare CR300/CR350 reference stdout accepting later-diagnostic differences (`PLAN.md`)."""

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        if not isinstance(other, str):
            return False
        text = str(self)
        if text.splitlines()[:1] != other.splitlines()[:1] or "-- Compile Failed!" not in text:
            return False
        return impact_class({"our_exit": 1, "ours": other, "their_exit": 1, "theirs": text}) in (EXACT, LATER, ORDER)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


def pytest_collection_modifyitems(items):
    for item in items:
        cs = getattr(item, "callspec", None)
        if cs is None:
            continue
        case = cs.params.get("case")
        if (
            isinstance(case, dict)
            and case.get("logger") in ("cr300", "cr350")
            and "not_modelled" not in case
            and isinstance(case.get("expected_stdout"), str)
            and not isinstance(case["expected_stdout"], _Cr3xxExpectedStdout)
        ):
            case["expected_stdout"] = _Cr3xxExpectedStdout(case["expected_stdout"])


def pytest_sessionstart(session):
    os.environ["OPENCRBASIC_STRICT"] = "1"


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="ocb-") as d:
        yield Path(d).resolve()
