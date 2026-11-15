import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from impact import LATER, ORDER, impact_class

# CR300/CR350 cases whose later diagnostics differ from the reference since the cascade passes
# were removed (`docs/loggers.md` "Later-diagnostic cascade no longer modelled"). Each line is
# `<case digest>:<digest of our stdout>`, so a listed case passes only with the recorded output and
# an unlisted case must match the reference exactly. Regenerate after an intended change with
#   OPENCRBASIC_RECORD_CR3XX_LATER=/tmp/later.txt uv run pytest -q -n auto -m 'not server'
#   sort -u /tmp/later.txt > tests/cr3xx_later_baseline.txt
_LATER_BASELINE = Path(__file__).with_name("cr3xx_later_baseline.txt")
_LATER = frozenset(_LATER_BASELINE.read_text().split()) if _LATER_BASELINE.exists() else frozenset()
_RECORD = os.environ.get("OPENCRBASIC_RECORD_CR3XX_LATER")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]


class _Cr3xxExpectedStdout(str):
    """Reference stdout of a CR300/CR350 case that also equals its recorded later-diagnostic output."""

    key = ""

    def __eq__(self, other: object) -> bool:
        if super().__eq__(other):
            return True
        if not isinstance(other, str):
            return False
        entry = f"{self.key}:{_digest(other)}"
        if not _RECORD:
            return entry in _LATER
        row = {"our_exit": 1, "ours": other, "their_exit": 1, "theirs": str(self)}
        if "-- Compile Failed!" not in self or impact_class(row) not in (LATER, ORDER):
            return False
        with open(_RECORD, "a") as out:
            out.write(entry + "\n")
        return True

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
            expected = _Cr3xxExpectedStdout(case["expected_stdout"])
            expected.key = _digest(case["logger"], str(case.get("source")), case["expected_stdout"])
            case["expected_stdout"] = expected


def pytest_sessionstart(session):
    os.environ["OPENCRBASIC_STRICT"] = "1"


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="ocb-") as d:
        yield Path(d).resolve()
