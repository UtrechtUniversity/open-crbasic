import json
import re
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "settings_fields.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
EXTENSIONS = {"cr6": "CR6", "cr1000x": "CR1X", "cr300": "CR300", "cr350": "CR350"}
FIELD_MESSAGE = re.compile(r"line (\d+): (.*) is not a (?:Status or )?Settings Field Name\.")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_settings_field_observations(case, tmp_path):
    program = tmp_path / f"t.{EXTENSIONS[case['logger']]}"
    program.write_bytes(case["source"].encode("latin-1"))

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]


@pytest.mark.parametrize("logger", sorted(EXTENSIONS))
@pytest.mark.parametrize("instruction", ["SetSetting", "SetStatus"])
def test_candidate_names_match_observed_acceptance(logger, instruction, tmp_path):
    """Every candidate name, one per line: exactly the names the real compiler rejected
    are flagged (the fixture's `accepted` list is the oracle observation)."""
    candidates = FIXTURE["candidate_names"]
    accepted = set(FIXTURE["accepted"][logger][instruction])
    program = tmp_path / f"t.{EXTENSIONS[logger]}"
    flagged = set()
    for start in range(0, len(candidates), 40):    # the observation was made 40 names at a time
        names = candidates[start:start + 40]
        body = "\n".join(f'{instruction}("{name}",5)' for name in names)
        program.write_text(f"Public x As Float\nBeginProg\n{body}\nEndProg\n", encoding="latin-1")
        result = compile_file(program, profiles.get(logger))
        flagged |= {names[int(m.group(1)) - 3]
                    for m in map(FIELD_MESSAGE.match, result.result_lines()) if m}
    assert flagged == set(candidates) - accepted
