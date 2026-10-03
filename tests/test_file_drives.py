"""FileManage, TableFile, SetSetting/SetStatus index, DataTable member and SDI12Recorder structure destination observations; expected output is the real compilers'."""
import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

CASES = [case for name in ("filemanage_drives", "tablefile_drives", "settings_index", "table_members", "sdi12_structure_dest",
                     "readonly_struct_scope", "table_members_scope", "spi_open_order")
         for case in json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_file_drive_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
