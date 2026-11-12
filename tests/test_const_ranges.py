"""Constant-parameter range checks. Expected output is the real compilers' verbatim output; the
fixture records each compiler's sha256 and the observation date."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    case
    for name in (
        "const_ranges.json",
        "r93_cdm_bridge_options.json",
        "r102_ec100_options.json",
        "r103_sdm_destination_aliases.json",
        "r104_avw200_outputs.json",
        "r105_timed_sdm.json",
        "r106_cdm_fft.json",
        "r107_snmp_spaced_oid.json",
        "r108_fieldclassify_expressions.json",
        "r109_quadrature.json",
        "r110_pulsecount_channels.json",
        "r111_splitstr_structure_options.json",
        "r112_serialopen_comms_mode.json",
        "r113_datatable_executing_sequences.json",
        "r114_calltable_auto_sizes.json",
    )
    for case in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_const_range_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    actual = "\n".join(result.result_lines())
    if reason := case.get("not_modelled"):
        assert actual != case["expected_stdout"], f"Remove the expected failure for {case['id']}"
        pytest.xfail(reason)
    assert actual == case["expected_stdout"]
