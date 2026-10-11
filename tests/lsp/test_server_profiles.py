"""The stdio language server with the other loggers (cr1000x, cr300, cr350), LSP-06b.

Expected diagnostics come from `analysis.analyze` for the same logger. Program text is one the
real compiler accepted on that logger (fixtures `calltable_open_table-3` and the
`Include "CPU:inc.crb"` cases of `r5cr6_include_ext` / `r9_cr3xx_include_ext`); the compiler itself
is not run here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from opencrbasic.analysis import AnalysisRequest, analyze

from .lspclient import LspClient

pytestmark = pytest.mark.server

FAST = {"debounceMs": 50, "analysisTimeoutMs": 60000}
LOGGERS = ("cr1000x", "cr300", "cr350")
LIB = "Public lib_v As Long\n"
LIB_BAD = "Public lib_v As Long\nPublic lib_v As Long\n"

# Accepted on every logger under test: an include of `CPU:Lib.crb`, a table, and one scan line.
PROG = """Include "CPU:Lib.crb"
Public x
DataTable(T,1,1)
  Sample(1,x,FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    x = x + 1
    CallTable T
  NextScan
EndProg
"""
BAD_LINE = 8                        # 1-based line of `x = x + 1` in PROG
UNDEFINED = {                       # the same unresolved name, worded per profile
    "cr1000x": "Invalid symbol in expression:     x = y + 1.",
    "cr300": "y is not defined.",
    "cr350": "y is not defined.",
}
# What each logger does with `Include "CPU:Lib.<ext>"` for a loadable library file (`source.py`
# `_bad_extension` / `_unloadable`, oracle fixtures r5cr6_include_ext and r9_cr3xx_include_ext).
# `None`: the include loads; a message: the entry's own diagnostic at that include line.
INCLUDE_RULES = {
    "cr1000x": {"crb": None, "CR1X": None,
                "CR6": "Include file CPU:Lib.CR6 must have valid program file extension.",
                "CR300": "Include file CPU:Lib.CR300 must have valid program file extension.",
                "CR350": "Include file CPU:Lib.CR350 must have valid program file extension."},
    "cr300": {"crb": None, "CR300": None,
              "CR1X": "Cannot open include file  CPU:Lib.CR1X.",
              "CR6": "Include file CPU:Lib.CR6 must have valid program file extension.",
              "CR350": "Include file CPU:Lib.CR350 must have valid program file extension."},
    "cr350": {"crb": None, "CR350": None,
              "CR1X": "Cannot open include file  CPU:Lib.CR1X.",
              "CR6": "Include file CPU:Lib.CR6 must have valid program file extension.",
              "CR300": "Include file CPU:Lib.CR300 must have valid program file extension."},
}
INCLUDE_CASES = [(lg, ext) for lg in LOGGERS for ext in INCLUDE_RULES[lg]]


def bad(text: str, name: str = "y") -> str:
    return text.replace("x = x + 1", f"x = {name} + 1")


def severity_code(name: str) -> int:
    return {"error": 1, "warning": 2}[name]


def summary(params: dict) -> list[tuple]:
    return [(d["range"]["start"]["line"], d["severity"], d["source"], d["message"])
            for d in params["diagnostics"]]


def expected_by_path(result) -> dict[Path, list[tuple]]:
    grouped: dict[Path, list[tuple]] = {}
    for d in result.diagnostics:
        assert d.path is not None, d
        grouped.setdefault(d.path, []).append(
            (max(d.line - 1, 0), severity_code(d.severity), "opencrbasic", d.message))
    return grouped


@pytest.fixture
def make_client(request):
    clients: list[LspClient] = []

    def factory(**kwargs) -> LspClient:
        client = LspClient(**kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.kill()
        time.sleep(0.1)
        assert "Traceback" not in client.stderr_text, client.stderr_text


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def started(make_client, root: Path, logger: str, options=None, **kwargs) -> LspClient:
    client = make_client(**kwargs)
    client.initialize(root, {**FAST, "logger": logger, **(options or {})})
    return client


def wait_matches(client: LspClient, expected: dict[Path, list[tuple]]) -> None:
    """Every path `analyze` reports on carries exactly those diagnostics; every other published
    path is clean."""
    for path, items in expected.items():
        params = client.wait_diagnostics(path, lambda ds, n=len(items): len(ds) == n)
        assert sorted(summary(params)) == sorted(items), path
    uris = {path.as_uri() for path in expected}
    for params in client.publishes():
        if params["uri"] not in uris:
            assert params["diagnostics"] == [], params


# ---- clean program and invalid edit, per logger ---------------------------------------------

@pytest.mark.parametrize("logger", LOGGERS)
def test_clean_program_publishes_no_errors(make_client, tmp_path, logger):
    root = project(tmp_path, **{"Small.CR6": PROG, "Lib.crb": LIB})
    small = root / "Small.CR6"
    expected = analyze(AnalysisRequest(small, logger))
    assert expected.ok and not expected.diagnostics
    client = started(make_client, root, logger)
    client.open(small, PROG)
    params = client.wait_diagnostics(small)
    assert params["diagnostics"] == []
    assert client.shutdown() == 0


@pytest.mark.parametrize("logger", LOGGERS)
def test_invalid_edit_matches_analysis(make_client, tmp_path, logger):
    root = project(tmp_path, **{"Small.CR6": PROG, "Lib.crb": LIB})
    small = root / "Small.CR6"
    broken = bad(PROG)
    expected = analyze(AnalysisRequest(small, logger, overlays={small: broken.encode()}))
    assert not expected.ok and len(expected.diagnostics) == 1
    (only,) = expected.diagnostics
    assert only.message == UNDEFINED[logger]

    client = started(make_client, root, logger)
    client.open(small, PROG)
    client.wait_diagnostics(small)
    client.change(small, broken, 2)
    params = client.wait_diagnostics(small, lambda d: bool(d))
    (diag,) = params["diagnostics"]
    assert params["version"] == 2
    assert diag["severity"] == 1 and diag["source"] == "opencrbasic"
    assert diag["message"] == only.message == UNDEFINED[logger]
    assert diag["range"]["start"] == {"line": BAD_LINE - 1, "character": 0}
    assert diag["range"]["end"] == {"line": BAD_LINE - 1,
                                    "character": len(broken.splitlines()[BAD_LINE - 1])}
    assert client.shutdown() == 0


# ---- include extensions, per logger -----------------------------------------------------------

@pytest.mark.parametrize("logger,ext", INCLUDE_CASES)
def test_include_extension_rules(make_client, tmp_path, logger, ext):
    """A library with a duplicate declaration under each extension: the entry reports the rule
    outcome, and a library that loads reports its own error at its own path. Compared with
    `analyze` on the same overlay."""
    root = project(tmp_path, **{"Small.CR6": PROG, f"Lib.{ext}": LIB_BAD})
    small = root / "Small.CR6"
    text = PROG.replace("Lib.crb", f"Lib.{ext}")
    expected = analyze(AnalysisRequest(small, logger, overlays={small: text.encode()}))
    grouped = expected_by_path(expected)
    rule = INCLUDE_RULES[logger][ext]
    entry_messages = [d.message for d in expected.diagnostics if d.path == small]
    if rule is None:
        assert not any("Include" in m or "Cannot open" in m for m in entry_messages)
        assert (root / f"Lib.{ext}") in grouped, "the loaded library reports its own error"
    else:
        assert rule in entry_messages, entry_messages

    # only Small.CR6 is an entry: a library named `.CR6` would otherwise be discovered too
    client = started(make_client, root, logger, {"entries": ["Small.CR6"]})
    client.open(small, text)
    wait_matches(client, grouped)
    assert client.shutdown() == 0


# ---- entry discovery under a non-CR6 logger ---------------------------------------------------

def test_discovered_cr6_entry_is_analysed_as_configured_logger(make_client, tmp_path):
    """Default `entries` finds `**/*.CR6` on disk; under cr300 the discovered entry is analysed
    with the cr300 rules and the cr300 wording, without being opened."""
    root = project(tmp_path, **{"Good.CR6": PROG, "Bad.CR6": bad(PROG), "Lib.crb": LIB})
    good, broken = root / "Good.CR6", root / "Bad.CR6"
    client = started(make_client, root, "cr300")
    params = client.wait_diagnostics(broken, lambda d: bool(d))
    time.sleep(1.0)                 # a clean entry that was never reported gets no publish
    assert all(not p["diagnostics"] for p in client.publishes(good))
    assert summary(params) == [(BAD_LINE - 1, 1, "opencrbasic", UNDEFINED["cr300"])]
    assert sorted(summary(params)) == sorted(
        (max(d.line - 1, 0), 1, "opencrbasic", d.message)
        for d in analyze(AnalysisRequest(broken, "cr300")).diagnostics)
    assert client.shutdown() == 0


def test_open_extension_of_configured_logger_is_an_entry(make_client, tmp_path):
    """An opened `.CR300` is an entry for itself under logger cr300 (not discovered, not
    configured)."""
    root = project(tmp_path, **{"Lib.crb": LIB})
    own = root / "Own.CR300"
    client = started(make_client, root, "cr300")
    client.open(own, bad(PROG))
    params = client.wait_diagnostics(own, lambda d: bool(d))
    assert [m["message"] for m in params["diagnostics"]] == [UNDEFINED["cr300"]]
    assert client.shutdown() == 0


def test_cr6_extension_is_still_an_entry_under_cr1000x(make_client, tmp_path):
    root = project(tmp_path, **{"Lib.crb": LIB})
    own = root / "Own.CR6"
    client = started(make_client, root, "cr1000x")
    client.open(own, bad(PROG))
    params = client.wait_diagnostics(own, lambda d: bool(d))
    assert [m["message"] for m in params["diagnostics"]] == [UNDEFINED["cr1000x"]]
    assert client.shutdown() == 0
