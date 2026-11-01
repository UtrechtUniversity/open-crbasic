"""`<file>` prefix of a message from an included file. Oracle, 2026-09-27: CR6/CR1000X print the
name as written without its drive; CR300/CR350 with it, adding `cpu:` when none was written."""

from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file


@pytest.mark.parametrize(
    "spec,cr6,cr300",
    [
        ("Lib.crb", "<Lib.crb>", "<cpu:Lib.crb>"),
        ("CPU:Lib.crb", "<Lib.crb>", "<CPU:Lib.crb>"),
        ("cpu:lib.CRB", "<lib.CRB>", "<cpu:lib.CRB>"),
    ],
)
def test_include_prefix(tmp_path: Path, spec, cr6, cr300):
    (tmp_path / "Lib.crb").write_text("Const Port = U99\n")
    main = tmp_path / "t.cr6"
    main.write_text(f'Public X\nInclude "{spec}"\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n')
    assert compile_file(main, profiles.get("cr6")).result_lines()[2].startswith(f"{cr6} line 1:")
    assert compile_file(main, profiles.get("cr300")).result_lines()[2].startswith(f"{cr300} line 1:")
