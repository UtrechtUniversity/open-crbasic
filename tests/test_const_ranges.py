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
        "r115_datatable_field_resolution.json",
        "r116_cr3xx_forward_variables.json",
        "r117_table_size_sentinels.json",
        "r118_routine_parameter_recovery.json",
        "r119_windvector_options.json",
        "r120_cr3xx_packed_table_reps.json",
        "r121_iif_empty_assignment_slots.json",
        "r122_string_size_suffixes.json",
        "r123_include_integer_names.json",
        "r124_fieldnames_expressions.json",
        "r125_const_use_order.json",
        "r126_mux_cpi_timing.json",
        "r127_nsec_output.json",
        "r128_wide_sample_dtype.json",
        "r129_table_alias_spans.json",
        "r130_periodavg_channels.json",
        "r131_float_destinations.json",
        "r132_destination_expressions.json",
        "r133_array_destinations.json",
        "r134_serialopen_format.json",
        "r135_bridge_excitation.json",
        "r136_pulse_config.json",
        "r137_inline_group_recovery.json",
        "r138_pipeline_setup.json",
        "r139_reserved_output_fields.json",
        "r140_inline_sub_arguments.json",
        "r141_bridge_min_excitation.json",
        "r142_serialopen_recovery.json",
        "r143_settings_members.json",
        "r144_zero_measurement_channel.json",
        "r145_thermistor_spans.json",
        "r146_delay_limit.json",
    )
    for case in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_const_range_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    exit_code = 0 if result.ok else 1
    actual = "\n".join(result.result_lines())
    if reason := case.get("not_modelled"):
        assert (exit_code, actual) != (case["expected_exit"], case["expected_stdout"]), (
            f"Remove the expected failure for {case['id']}"
        )
        pytest.xfail(reason)
    assert exit_code == case["expected_exit"]
    assert actual == case["expected_stdout"]
