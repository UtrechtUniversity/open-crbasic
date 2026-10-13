import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

CASES = [c for f in ("r7_include_cutoff", "r7_include_existing_ext", "r13_include_subdir",
                                                "r13_include_nesting", "r13_two_word_routine_end", "r13_include_prefix", "r13_include_dupdecl", "r13_include_scan_cascade", "r14_include_incomplete_prefix", "r16_include_eof_continuation")
         if (Path(__file__).parent / "fixtures" / f"{f}.json").exists()
         for c in json.loads((Path(__file__).parent / "fixtures" / f"{f}.json").read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_include_validation_observations(case, tmp_path):
    for name, text in case["siblings"].items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]


def test_include_in_subdirectory_through_overlay_and_case_fold(tmp_path):
    from opencrbasic.sources import DiskSource, OverlaySource

    (tmp_path / "Sub").mkdir()
    program = tmp_path / "t.CR6"
    program.write_text('Include "CPU:sub/Lib.crb"\nBeginProg\n  LibV = 1\nEndProg\n', encoding="utf-8")
    (tmp_path / "Sub" / "Lib.crb").write_text("Public LibV\n", encoding="utf-8")
    assert compile_file(program, profiles.get("cr6")).ok            # directory name folds case
    overlay = OverlaySource(DiskSource(), {tmp_path / "sub" / "Lib.crb": b"Public LibV\n"})
    (tmp_path / "Sub" / "Lib.crb").unlink()
    (tmp_path / "Sub").rmdir()
    assert compile_file(program, profiles.get("cr6"), source=overlay).ok  # unsaved file in a new directory
