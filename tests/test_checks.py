"""opencrbasic checks (`checks.py`) and the `crbc --strict` output contract: findings on stderr
only, stdout and CR6cmple.xxx unchanged, exit 3 for an accepted program with a strict error."""

import subprocess
import sys
from pathlib import Path

import pytest

from opencrbasic import checks, profiles
from opencrbasic.compiler import compile_file

SRC = "Public D(3), X\nAlias D(1)={name}\nBeginProg\n  Scan(1,Sec,0,0)\n    X = D(1)\n  NextScan\nEndProg\n"


def _findings(tmp_path, name, logger="cr6"):
    p = tmp_path / "t.CR6"
    p.write_text(SRC.format(name=name))
    result = compile_file(p, profiles.get(logger))
    return result.ok, [f.code for f in checks.run(result.program_ast, profiles.get(logger))]


# Each name compiles on the reference (oracle, 2026-10-24, docs/checker.md "Alias names that are
# CRBasic names")
@pytest.mark.parametrize(
    "name",
    [
        "Public",
        "Status",
        "Settings",
        "Sin",
        "Battery",
        "VoltSe",
        "DataTable",
        "INF",
        "Then",
        "AND",
        "Else",
        "Next",
        "Dim",
        "MOD",
        "Double",
    ],
)
def test_alias_keyword_fires(tmp_path, name):
    assert _findings(tmp_path, name) == (True, ["OCB001"])


@pytest.mark.parametrize("name", ["Wind", "Temp_C", "Status_1"])
def test_alias_keyword_controls(tmp_path, name):
    assert _findings(tmp_path, name) == (True, [])


def test_alias_keyword_profile_specific(tmp_path):
    # `Long` is a predefined CONST on CR300 (a reference error, not a check)
    assert _findings(tmp_path, "Long", "cr6") == (True, ["OCB001"])
    assert _findings(tmp_path, "Long", "cr300")[1] == []


def _crbc(tmp_path, *args):
    return subprocess.run(
        [sys.executable, "-m", "opencrbasic", *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )


def test_strict_output_contract(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC.format(name="Status"))
    plain = _crbc(tmp_path, "t.CR6")
    message = (tmp_path / "CR6cmple.xxx").read_bytes()
    strict = _crbc(tmp_path, "--strict", "t.CR6")
    assert (plain.returncode, strict.returncode) == (0, 3)
    assert strict.stdout == plain.stdout == "t.CR6 -- Compiled in PipelineMode.\n"
    assert (tmp_path / "CR6cmple.xxx").read_bytes() == message
    assert plain.stderr == ""
    assert strict.stderr == (
        "t.CR6 line 2: error OCB001: Alias name Status is also the Status system table; choose another name.\n"
    )


def test_strict_keeps_reference_rejection(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC.format(name="Status").replace("D(1)\n", "Nope\n"))
    assert _crbc(tmp_path, "--strict", "t.CR6").returncode == 1
