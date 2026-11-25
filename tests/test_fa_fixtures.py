"""Fuzz round 5 false-accept families (docs/instructions.md "Fuzz round 5 false accepts")."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

NAMES = [
    "alias_flat_dup",
    "routers_dest_index",
    "argos_error_dest",
    "samplefieldcal_a",
    "samplefieldcal_b",
    "ch400status",
    "fft_source_length",
    "therm_settling_time",
    "cdm_ranges",
    "cdm_reps",
    "cdm_port_and_type",
    "gps_nmea_strings",
    "mqtt_payload_expr",
    "matrix_illegal",
    "dnp_variable_a",
    "dnp_variable_b",
    "r100_cdm_mux_options",
]
CASES = [
    c
    for n in NAMES
    for c in json.loads((Path(__file__).parent / "fixtures" / f"{n}.json").read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
        if "not_modelled" in case
        else case
        for case in CASES
    ],
    ids=lambda case: case["id"],
)
def test_fa_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
