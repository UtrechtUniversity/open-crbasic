"""Tests for `opencrbasic.profiles`: extension-to-logger mapping, `--logger`, and per-logger data
loading. Evidence for the per-logger differences these profiles carry: `docs/loggers.md`."""

from pathlib import Path

import pytest

from opencrbasic import cli, profiles
from opencrbasic.compiler import compile_file

EXTENSION_CASES = [
    (".CR6", "cr6"),
    (".cr6", "cr6"),
    (".CR1X", "cr1000x"),
    (".cr1x", "cr1000x"),
    (".CR300", "cr300"),
    (".cr300", "cr300"),
    (".CR350", "cr350"),
    (".cr350", "cr350"),
]


@pytest.mark.parametrize("ext, expected", EXTENSION_CASES)
def test_for_file_extension_mapping(ext: str, expected: str) -> None:
    assert profiles.for_file(Path("Main" + ext)).name == expected


def test_for_file_unknown_extension_defaults_to_cr6() -> None:
    assert profiles.for_file(Path("Main.txt")).name == "cr6"


@pytest.mark.parametrize("name", ["cr6", "CR6", "cr1000x", "CR1000X", "cr300", "cr350"])
def test_get_case_insensitive(name: str) -> None:
    assert profiles.get(name).name == name.lower()


def test_get_unknown_logger_raises() -> None:
    with pytest.raises(ValueError, match="unknown logger"):
        profiles.get("cr23x")


@pytest.mark.parametrize("name", list(profiles.PROFILES))
def test_every_profile_loads_its_data(name: str) -> None:
    """`instructions`/`functions`/`symbols`/`mode_rules`/`terminals` all resolve without error and
    are non-empty (except `terminals`, which CR300/CR350 only partially populate -- `docs/loggers.md`)."""
    profile = profiles.get(name)
    assert profile.instructions
    assert profile.functions
    assert profile.symbols
    assert profile.mode_rules["always_sequential"]
    assert profile.mode_rules["task_instructions"]
    assert profile.terminals["channels"]
    assert profile.terminals["instructions"]


MINIMAL_PROGRAM = "Public X As Float\n\nBeginProg\n  X = 1\nEndProg\n"


@pytest.mark.parametrize("name", list(profiles.PROFILES))
def test_compile_file_uses_requested_profile(tmp_path: Path, name: str) -> None:
    """`compile_file`'s `profile` argument (not the `.CR6` extension) decides both the checker's
    profile and the parser's (`docs/loggers.md`: before this, `Const` folding during parsing always
    used the file-extension profile, independently of `--logger` / this argument)."""
    path = tmp_path / "t.CR6"
    path.write_text(MINIMAL_PROGRAM)
    profile = profiles.get(name)
    result = compile_file(path, profile)
    assert result.profile.name == name
    assert result.ok
    line = result.result_lines()[0]
    if profile.reports_mode:
        assert "Mode." in line
    else:
        assert line.endswith("Compiled OK.")


@pytest.mark.parametrize(
    "ext, expected", [(".CR6", "cr6"), (".CR1X", "cr1000x"), (".CR300", "cr300"), (".CR350", "cr350")]
)
def test_compile_file_defaults_profile_from_extension(tmp_path: Path, ext: str, expected: str) -> None:
    path = tmp_path / ("t" + ext)
    path.write_text(MINIMAL_PROGRAM)
    result = compile_file(path)
    assert result.profile.name == expected


def test_cli_logger_flag_overrides_extension(tmp_path: Path, capsys) -> None:
    path = tmp_path / "t.CR6"
    path.write_text(MINIMAL_PROGRAM)
    rc = cli.main(["--logger", "cr300", "--no-message-file", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().endswith("Compiled OK.")  # CR300 convention, not CR6's "...Mode."


def test_cli_logger_flag_unknown_value_raises(tmp_path: Path) -> None:
    path = tmp_path / "t.CR6"
    path.write_text(MINIMAL_PROGRAM)
    with pytest.raises(ValueError, match="unknown logger"):
        cli.main(["--logger", "cr23x", "--no-message-file", str(path)])
