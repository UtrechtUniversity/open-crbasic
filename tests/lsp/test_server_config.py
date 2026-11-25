"""Configuration parsing and entry discovery (no server process)."""

from opencrbasic.lsp.config import Config, discover, glob_regex, parse_config


def test_defaults():
    cfg, warnings = parse_config(None)
    assert cfg == Config() and warnings == []
    assert (cfg.entries, cfg.exclude, cfg.logger, cfg.encoding, cfg.debounce_ms, cfg.analysis_timeout_ms) == (
        ("**/*.{CR6,CR1X,CR300,CR350}",),
        (),
        "auto",
        "utf-8",
        300,
        10000,
    )


def test_valid_and_invalid_values():
    cfg, warnings = parse_config(
        {"entries": ["a/*.CR6"], "logger": "cr300", "debounceMs": 0, "analysisTimeoutMs": 5.0, "encoding": "latin-1"}
    )
    assert cfg.entries == ("a/*.CR6",) and cfg.logger == "cr300" and cfg.debounce_ms == 0
    assert cfg.analysis_timeout_ms == 5 and cfg.encoding == "latin-1" and warnings == []
    cfg2, warnings = parse_config(
        {"entries": "x", "logger": "nope", "encoding": "bogus", "debounceMs": -1, "analysisTimeoutMs": True}, cfg
    )
    assert cfg2 == cfg and len(warnings) == 5
    assert ".cr300" in cfg.entry_suffixes() and ".cr6" in cfg.entry_suffixes()
    assert ".cr300" not in Config(logger="cr6").entry_suffixes()
    assert Config().entry_suffixes() == {".cr6", ".cr1x", ".cr300", ".cr350"}  # auto: every logger's


def test_auto_logger_follows_the_extension():
    from pathlib import Path

    auto, fixed = Config(), Config(logger="cr300")
    assert [auto.logger_for(Path(f"P.{e}")) for e in ("CR6", "cr1x", "CR300", "CR350", "crb")] == [
        "cr6",
        "cr1000x",
        "cr300",
        "cr350",
        "cr6",
    ]
    assert fixed.logger_for(Path("P.CR6")) == "cr300"  # a configured logger wins, as `crbc --logger`
    assert parse_config({"logger": "auto"}, fixed)[0].logger == "auto"


def test_editor_feature_switches():
    cfg, warnings = parse_config({"unusedHints": False, "caseCorrection": False, "codeLens": {"references": False}})
    assert (cfg.unused_hints, cfg.case_correction, cfg.code_lens_references, warnings) == (False, False, False, [])
    cfg, warnings = parse_config({"codeLens.references": False, "unusedHints": "no"})
    assert cfg.code_lens_references is False and cfg.unused_hints is True and len(warnings) == 1


def test_help_directory_and_feature_wait():
    cfg, warnings = parse_config({"helpDirectory": " ../extracted_chm ", "featureWaitMs": 250})
    assert cfg.help_directory == "../extracted_chm" and cfg.feature_wait_ms == 250 and warnings == []
    assert (Config().help_directory, Config().feature_wait_ms) == ("", 1500)
    cleared, warnings = parse_config({"helpDirectory": None}, cfg)
    assert cleared.help_directory == "" and warnings == []
    kept, warnings = parse_config({"helpDirectory": 3, "featureWaitMs": -1}, cfg)
    assert kept == cfg and len(warnings) == 2


def test_glob_semantics(tmp_path):
    assert glob_regex("**/*.CR6").fullmatch("a/b/Main.cr6")  # case-insensitive
    assert glob_regex("**/*.CR6").fullmatch("Main.CR6")
    assert not glob_regex("*.CR6").fullmatch("a/Main.CR6")
    assert glob_regex("{a,b}/x?.CR6").fullmatch("b/xy.CR6")
    assert glob_regex("examples/**").fullmatch("examples/deep/f.CR6")
    for rel in ("Main.CR6", "sub/T.cr6", "examples/Old.CR6", ".hidden/H.CR6", "Lib.crb"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    names = lambda found: sorted(p.relative_to(tmp_path).as_posix() for p in found)
    assert names(discover([tmp_path], ("**/*.CR6",), ())) == ["Main.CR6", "examples/Old.CR6", "sub/T.cr6"]
    assert names(discover([tmp_path], ("**/*.CR6",), ("examples/**",))) == ["Main.CR6", "sub/T.cr6"]
