"""Check configuration (`opencrbasic.toml`), `crbc --report` and the separate result field
(`PLAN.md` "Configuration", `docs/checker.md` "Check configuration and report file (round 52)").
The reference channel (stdout, `CR6cmple.xxx`) must be byte-identical with and without any of it."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opencrbasic import checkconfig, checks, profiles
from opencrbasic.compiler import compile_file

SRC = "Public D(3), X\nAlias D(1)=Status\nBeginProg\n  Scan(1,Sec,0,0)\n    X = D(1)\n  NextScan\nEndProg\n"
WARNING = ("t.CR6 line 2: warning OCB001: Alias name Status is also the Status system table; "
           "choose another name.\n")
ERROR = WARNING.replace("warning", "error")
CODES = checks.DEFAULT_SEVERITY


def _crbc(cwd, *args):
    return subprocess.run([sys.executable, "-m", "opencrbasic", *args], cwd=cwd, capture_output=True,
                          text=True, env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")})


def _analyze(tmp_path, toml="", cli=None, lsp=None, src=SRC, logger="cr6"):
    entry = tmp_path / "t.CR6"
    entry.write_text(src)
    cfg = checkconfig.Config()
    if toml:
        (tmp_path / checkconfig.FILE_NAME).write_text(toml)
        cfg = checkconfig.load(tmp_path / checkconfig.FILE_NAME, CODES)
    result = compile_file(entry, profiles.get(logger))
    items = list(result.diagnostics.items)
    checks.analyze(result, entry, cfg, cli=cli, lsp=lsp)
    assert result.ok and result.diagnostics.items == items       # separate result field
    return result


# --- configuration errors -----------------------------------------------------------------------

@pytest.mark.parametrize("toml, fragment", [
    ('[checks]\nOCB999 = "error"\n', "unknown check code OCB999"),
    ('[checks]\nOCB001 = "fatal"\n', "unknown severity 'fatal'"),
    ('[checks]\nOCB001 = true\n', "unknown severity"),
    ('[profile.cr9000.checks]\nOCB001 = "off"\n', "unknown logger profile"),
    ('[profile.cr6.checks]\nOCB001 = "loud"\n', "unknown severity"),
    ('[entry."a.CR6".checks]\nOCB404 = "off"\n', "unknown check code"),
    ('[[suppress]]\ncode = "OCB001"\nfile = "t.CR6"\n', "reason is required"),
    ('[[suppress]]\ncode = "OCB001"\nfile = "t.CR6"\nreason = " "\n', "reason is required"),
    ('[[suppress]]\ncode = "OCB099"\nfile = "t.CR6"\nreason = "x"\n', "unknown check code"),
    ('[[suppress]]\ncode = "OCB001"\nreason = "x"\n', "file (a glob) is required"),
    ('[[suppress]]\ncode = "OCB001"\nfile = "t.CR6"\nline = 0\nreason = "x"\n', "line must be"),
    ('[[suppress]]\ncode = []\nfile = "t.CR6"\nreason = "x"\n', "unknown check code"),
    ('[typo]\nx = 1\n', "unknown key typo"),
    ('[checks\n', "opencrbasic.toml"),
])
def test_config_errors(tmp_path, toml, fragment):
    path = tmp_path / checkconfig.FILE_NAME
    path.write_text(toml)
    with pytest.raises(checkconfig.ConfigError, match=fragment.replace("(", r"\(").replace(")", r"\)")):
        checkconfig.load(path, CODES)


def test_cli_override_errors():
    for bad in ("OCB001", "OCB001=loud", "OCB9=off"):
        with pytest.raises(checkconfig.ConfigError):
            checkconfig.parse_overrides([bad], CODES)
    assert checkconfig.parse_overrides(["OCB001=warning"], CODES) == {"OCB001": "warning"}


# --- precedence and suppression -----------------------------------------------------------------

LAYERS = ('[checks]\nOCB001 = "info"\n[profile.cr6.checks]\nOCB001 = "warning"\n'
          '[entry."t.CR6".checks]\nOCB001 = "off"\n')


def _severity(result):
    return [f.severity for f in result.findings]


def test_precedence(tmp_path):
    assert _severity(_analyze(tmp_path)) == ["error"]                                   # built-in
    assert _severity(_analyze(tmp_path, '[checks]\nOCB001 = "info"\n')) == ["info"]
    assert _severity(_analyze(tmp_path, '[checks]\nOCB001 = "info"\n[profile.cr6.checks]\nOCB001 = "warning"\n')) == ["warning"]
    assert _analyze(tmp_path, LAYERS).findings == []                                    # entry: off
    assert _severity(_analyze(tmp_path, LAYERS, lsp={"OCB001": "info"})) == ["info"]    # LSP > entry
    assert _severity(_analyze(tmp_path, LAYERS, lsp={"OCB001": "info"}, cli={"OCB001": "error"})) == ["error"]
    assert _analyze(tmp_path, '[checks]\nOCB001 = "error"\n', cli={"OCB001": "off"}).findings == []


def test_profile_layer_is_per_logger(tmp_path):
    toml = '[profile.cr300.checks]\nOCB001 = "off"\n'
    assert _severity(_analyze(tmp_path, toml)) == ["error"]
    assert _analyze(tmp_path, toml, logger="cr300").findings == []


@pytest.mark.parametrize("file, line, hit", [
    ("t.CR6", 2, True), ("T.cr6", 2, True), ("*.CR6", None, True), ("*.crb", 2, False),
    ("t.CR6", 3, False), ("other/*.CR6", None, False),
])
def test_suppression(tmp_path, file, line, hit):
    toml = f'[[suppress]]\ncode = "OCB001"\nfile = "{file}"\nreason = "kept for the 2025 tables"\n'
    if line:
        toml += f"line = {line}\n"
    result = _analyze(tmp_path, toml)
    assert bool(result.suppressed) is hit
    assert bool(result.findings) is not hit
    if hit:
        assert result.suppressed[0].reason == "kept for the 2025 tables"


@pytest.mark.parametrize("include", ["sub/inc.crb", "sub\\inc.crb"])
def test_suppression_of_included_file(tmp_path, include):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inc.crb").write_text("Alias D(2)=Sin\n")
    src = SRC.replace("BeginProg", f'Include "{include}"\nBeginProg')
    toml = '[[suppress]]\ncode = "OCB001"\nfile = "sub/*.crb"\nreason = "library"\n'
    result = _analyze(tmp_path, toml, src=src)
    assert [(f.file, f.line) for f in result.findings] == [("t.CR6", 2)]
    assert [(f.file, f.line) for f in result.suppressed] == [(include, 1)]


def test_config_found_in_nearest_ancestor(tmp_path):
    (tmp_path / "crbasic" / "deep").mkdir(parents=True)
    (tmp_path / checkconfig.FILE_NAME).write_text("")
    inner = tmp_path / "crbasic" / checkconfig.FILE_NAME
    inner.write_text("")
    entry = tmp_path / "crbasic" / "deep" / "t.CR6"
    assert checkconfig.find(entry) == inner.resolve()
    inner.unlink()
    assert checkconfig.find(entry) == (tmp_path / checkconfig.FILE_NAME).resolve()


def test_entry_path_is_relative_to_the_config_file(tmp_path):
    (tmp_path / "crbasic").mkdir()
    entry = tmp_path / "crbasic" / "t.CR6"
    entry.write_text(SRC)
    cfg_path = tmp_path / checkconfig.FILE_NAME
    cfg_path.write_text('[entry."crbasic/t.cr6".checks]\nOCB001 = "warning"\n')
    result = compile_file(entry, profiles.get("cr6"))
    checks.analyze(result, entry, checkconfig.load(cfg_path, CODES))
    assert _severity(result) == ["warning"]


# --- crbc contract ------------------------------------------------------------------------------

def _reference(tmp_path, *args, name="t.CR6"):
    run = _crbc(tmp_path, *args, name)
    return run, run.stdout, (tmp_path / "CR6cmple.xxx").read_bytes()


def test_reference_channel_is_byte_identical(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC)
    plain, out, message = _reference(tmp_path)
    plain_us = _reference(tmp_path, "-u", "-s")
    assert (plain.returncode, plain.stderr, out) == (0, "", "t.CR6 -- Compiled in PipelineMode.\n")
    (tmp_path / checkconfig.FILE_NAME).write_text(
        '[checks]\nOCB001 = "warning"\n[[suppress]]\ncode = "OCB001"\nfile = "x.CR6"\nreason = "r"\n')
    for args in (["--strict"], ["--report", "r.json"], ["--strict", "--report", "r.json"],
                 ["--strict", "--check", "OCB001=off"], ["--strict", "--config", "none.toml"],
                 ["--strict", "--check", "OCB404=off"]):
        _, o, m = _reference(tmp_path, *args)
        assert (o, m) == (out, message), args
    _, o, m = _reference(tmp_path, "--strict", "--report", "r.json", "-u", "-s")
    assert (o, m) == plain_us[1:]


def test_no_message_file_and_exit_codes(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC)
    run = _crbc(tmp_path, "--strict", "--no-message-file", "--check", "OCB001=warning", "t.CR6")
    assert (run.returncode, run.stdout, run.stderr) == (0, "t.CR6 -- Compiled in PipelineMode.\n", WARNING)
    assert not (tmp_path / "CR6cmple.xxx").exists()
    assert _crbc(tmp_path, "--strict", "t.CR6").returncode == 3
    assert _crbc(tmp_path, "--strict", "--check=OCB001=off", "t.CR6").returncode == 0
    (tmp_path / "bad.CR6").write_text(SRC.replace("X = D(1)", "X = Nope"))
    bad = _crbc(tmp_path, "--strict", "--check", "OCB404=off", "bad.CR6")
    assert bad.returncode == 1 and "configuration error" in bad.stderr           # reference verdict kept


def test_cr6comp_native_command_runs_no_checks(tmp_path):
    """`openloggernet.cr6comp.compile_program` with `CR6COMP=opencrbasic` judges success from the
    output text: a firing check and an invalid `opencrbasic.toml` change nothing there."""
    pytest.importorskip("openloggernet")
    (tmp_path / "t.CR6").write_text(SRC)
    (tmp_path / checkconfig.FILE_NAME).write_text('[checks]\nOCB001 = "fatal"\n')
    run = subprocess.run(
        [sys.executable, "-c", "import sys; from pathlib import Path; from openloggernet import cr6comp; "
         "print(cr6comp.compile_program(Path(sys.argv[1])))", str(tmp_path / "t.CR6")],
        capture_output=True, text=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "CR6COMP": "opencrbasic"})
    assert run.stdout == "(True, 't.CR6 -- Compiled in PipelineMode.')\n", run.stderr


@pytest.mark.parametrize("toml", ['[checks]\nOCB001 = "fatal"\n', '[[suppress]]\ncode = "OCB001"\nfile = "t.CR6"\n'])
def test_config_error_on_stderr_only(tmp_path, toml):
    (tmp_path / "t.CR6").write_text(SRC)
    plain, out, message = _reference(tmp_path)
    (tmp_path / checkconfig.FILE_NAME).write_text(toml)
    run, o, m = _reference(tmp_path, "--strict", "--report", "r.json")
    assert (o, m) == (out, message)
    assert run.returncode == 4 and run.stderr.startswith("opencrbasic: configuration error: ")
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["reference"]["ok"] is True and report["findings"] == []
    assert report["config"]["error"] == run.stderr.split("error: ", 1)[1].strip()
    # without --strict the exit status is the reference verdict
    assert _crbc(tmp_path, "--report", "r.json", "t.CR6").returncode == 0


def test_config_file_option_and_missing_file(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC)
    (tmp_path / "other.toml").write_text('[checks]\nOCB001 = "warning"\n')
    run = _crbc(tmp_path, "--strict", "--config", "other.toml", "t.CR6")
    assert (run.returncode, run.stderr) == (0, WARNING)
    missing = _crbc(tmp_path, "--strict", "--config", "nope.toml", "t.CR6")
    assert missing.returncode == 4 and "cannot read" in missing.stderr


def test_report(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC)
    run = _crbc(tmp_path, "--report", "r.json", "t.CR6")
    assert (run.returncode, run.stderr) == (0, "")                  # --report alone: no stderr, no exit 3
    report = json.loads((tmp_path / "r.json").read_text())
    assert report == {
        "schema_version": 2, "program": "t.CR6", "logger": "cr6",
        "reference": {"ok": True, "mode": "Pipeline", "diagnostics": []},
        "strict": False, "config": {"file": None, "error": None},
        "findings": [{"code": "OCB001", "severity": "error", "file": "t.CR6", "line": 2,
                      "message": "Alias name Status is also the Status system table; choose another name."}],
        "suppressed": [],
        "notices": [{"file": "t.CR6", "line": 4, "instruction": "SCAN",
                     "rules": json.loads((Path(checks.__file__).parent / "data" / "notices_cr6.json")
                                         .read_text())["missing"]["SCAN"]}],
    }
    (tmp_path / checkconfig.FILE_NAME).write_text(
        '[[suppress]]\ncode = "OCB001"\nfile = "t.CR6"\nreason = "kept"\n')
    run = _crbc(tmp_path, "--strict", "--report", "r.json", "t.CR6")
    assert (run.returncode, run.stderr) == (0, "")
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["strict"] is True and report["findings"] == []
    assert report["suppressed"][0]["reason"] == "kept"
    assert report["config"]["file"].endswith(checkconfig.FILE_NAME)


def test_report_of_a_rejected_program(tmp_path):
    (tmp_path / "t.CR6").write_text(SRC.replace("X = D(1)", "X = Nope"))
    run = _crbc(tmp_path, "--strict", "--report", "r.json", "t.CR6")
    report = json.loads((tmp_path / "r.json").read_text())
    assert run.returncode == 1 and run.stderr == ""
    assert report["reference"]["ok"] is False and report["findings"] == []
    assert report["reference"]["diagnostics"][0]["severity"] == "error"


def test_report_of_a_missing_program(tmp_path):
    run = _crbc(tmp_path, "--strict", "--report", "r.json", "nope.CR6")
    assert run.returncode == 1 and run.stderr == ""
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["reference"] == {"ok": False, "mode": None, "diagnostics": [
        {"severity": "error", "file": "nope.CR6", "line": None, "message": "No Program File"}]}
    assert report["findings"] == [] and report["strict"] is True


def test_warning_checks_keep_exit_zero(tmp_path):
    (tmp_path / "t.CR6").write_text("Public L As Long, S As String * 8\nBeginProg\n  Scan(1,Sec,0,0)\n"
                                    "    L = 2000 + Mid(S,5,2)\n  NextScan\nEndProg\n")
    plain = _crbc(tmp_path, "t.CR6")
    strict = _crbc(tmp_path, "--strict", "t.CR6")
    assert (plain.returncode, strict.returncode, strict.stdout) == (0, 0, plain.stdout)
    assert strict.stderr.startswith("t.CR6 line 4: warning OCB002: ")
    assert _crbc(tmp_path, "--strict", "--check", "OCB002=error", "t.CR6").returncode == 3
