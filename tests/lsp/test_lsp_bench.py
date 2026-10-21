"""Smoke test for tools/lsp_bench.py: tiny counts, one program, the synthetic help fixture.

Checks structure, the report's internal consistency and the generated inputs. Startup opens
the error variant, so a clean closed Main need not publish an empty diagnostic list. It asserts no
timing: the targets in LSP_PLAN.md are compared by the report, not asserted here."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import lsp_bench as bench  # noqa: E402

from opencrbasic.analysis import AnalysisRequest, analyze  # noqa: E402

HELP = ROOT / "tests" / "lsp" / "fixtures" / "help"


def test_percentile_is_nearest_rank():
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert bench.percentile(values, 0.5) == 3.0
    assert bench.percentile(values, 0.95) == 5.0
    assert bench.percentile([7.0], 0.95) == 7.0
    assert bench.percentile([], 0.5) is None


def test_positions_are_deterministic_identifier_starts():
    text = "Public A\n  Call Foo(B, 1)\n"
    first = bench.pick_positions(text, 3)
    assert first == bench.pick_positions(text, 3)
    assert len(first) == 3
    for line, col in bench.pick_positions(text, 100):
        assert text.split("\n")[line][col].isalnum() or text.split("\n")[line][col] == "_"


def test_main_variants_differ_by_one_error_line():
    base, variant = bench.main_variants()
    assert base != variant
    assert variant.count(b"BenchInjected") == 1
    result = analyze(AnalysisRequest(bench.MAIN, overlays={bench.MAIN: variant}))
    assert any(d.severity == "error" for d in result.diagnostics)


def test_stress_program_is_generated_and_compiles(tmp_path):
    path = bench.write_stress(tmp_path, 120)
    lines = path.read_text(encoding="ascii").splitlines()
    assert len(lines) == 11 * round((120 - 4) / 11) + 4
    result = analyze(AnalysisRequest(path))
    assert result.ok and not result.diagnostics


@pytest.mark.slow
def test_smoke_report(tmp_path):
    cfg = bench.Config(programs=(bench.MAIN,), cold_repeats=1, warm_runs=2, positions=5,
                       memory_iterations=4, stress_lines=120, edits=2, debounce_ms=50,
                       help_dir=HELP, label="smoke")
    report = bench.run_benchmark(cfg)
    json.dumps(report)                                           # serialisable

    host = report["host"]
    assert host["cpu_count"] and host["python"] and host["package"].endswith("__init__.py")
    assert host["label"] == "smoke"
    assert host["loadavg_start"] is None or len(host["loadavg_start"]) == 3

    assert report["cold"]["Main.CR6"]["n"] == 1
    assert report["warm"]["Main.CR6"]["n"] == 2
    assert report["warm"]["Main.CR6"]["p95_ms"] >= report["warm"]["Main.CR6"]["p50_ms"]

    positions = report["positions"]
    assert positions["positions"] == 5
    for name in ("completion", "hover", "definition", "document_symbols", "folding_ranges"):
        assert positions[name]["n"] == 5
    assert positions["signature_help"]["n"] == positions["signature_positions"] <= 5
    for name in ("completion", "hover", "signature_help", "definition", "document_symbols",
                 "folding_ranges"):
        assert positions[name]["hits"] <= positions[name]["n"]

    memory = report["memory"]
    assert memory["iterations"] == 4 and memory["ms"]["n"] == 4
    assert [s["iteration"] for s in memory["samples"]][-1] == 4

    stress = report["stress"]
    assert stress["warm"]["ok"] is True and stress["warm"]["n"] == 2

    server = report["server"]
    assert server["edits"] == 2
    assert server["exit_status"] == 0
    assert server["traceback_in_stderr"] is False
    assert server["banner"].startswith("opencrbasic-lsp:")
    assert server["didchange_to_publish"]["n"] == 2

    markdown = bench.render_markdown(report)
    for heading in ("## Cold and warm analysis", "## Warm editor operations", "## Repeated-edit memory",
                    "## Stdio server", "## Targets"):
        assert heading in markdown
    assert "Label: **smoke**" in markdown
