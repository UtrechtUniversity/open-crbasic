#!/usr/bin/env python3
"""Benchmarks for the language-server core and its stdio transport (LSP-08a).

    uv run --no-sync python tools/lsp_bench.py --json scratch/lsp/bench.json \
        --label "cloud container under concurrent load" > scratch/lsp/bench.md

The Markdown report goes to stdout, progress to stderr, the full data to `--json`. Measured:

- cold analysis: `analysis.analyze` on each `../crbasic/*.CR6`, first call in a fresh child process
  (imports are excluded; catalog and table loads inside the first call are included);
- warm analysis: p50/p95 over `--warm-runs` calls per program after one untimed call;
- warm editor operations at deterministic positions of `Main.CR6`: completion, hover, signature
  help (positions inside calls), definition, document symbols and folding ranges (the last two
  repeated), each with its first-call cost (one-time indexes and help parsing included);
- repeated-edit memory: `--memory-iterations` analyses of `Main.CR6` alternating between its
  disk bytes and a copy with one injected error, in a child process, with peak RSS (`ru_maxrss`)
  and current RSS (Linux `VmRSS`) sampled along the way;
- a generated large program of about `--stress-lines` lines (routines and a scan, written to a
  temporary directory, never committed): cold and warm analysis;
- the stdio server: `initialize` -> first publish for `Main.CR6`, and `didChange` -> publish over
  `--edits` alternating edits (the configured debounce is reported and subtracted).

Nothing is asserted against the targets in LSP_PLAN.md; the report only compares. Timings are
wall-clock on whatever host runs the tool and are recorded with the host's load average and CPU
count. Percentiles are nearest-rank. Procedure: `git show 2612abad:opencrbasic/docs/lsp-rounds/LSP-08.md`.
"""

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # opencrbasic/
REPO = ROOT                       # the worktree (or checkout) root
CRBASIC = REPO / "tests" / "corpus" / "firnmelt"
MAIN = (CRBASIC / "Main.CR6").absolute()
HELP_DIR = REPO / "extracted_chm"
PROFILE = "cr6"
TARGETS = {"hover_p95_ms": 100.0, "completion_p95_ms": 100.0, "diagnostics_after_debounce_p95_ms": 500.0}

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "lsp"))

from opencrbasic import editing, navigation  # noqa: E402
from opencrbasic import hover as H
from opencrbasic.analysis import AnalysisRequest, analyze  # noqa: E402
from opencrbasic.helpdocs import HelpProvider  # noqa: E402

LABEL_DEFAULT = "unlabelled"
SERVER_FAST = {"analysisTimeoutMs": 60000}


@dataclass(frozen=True)
class Config:
    programs: tuple[Path, ...] = ()        # empty: every ../crbasic/*.CR6
    cold_repeats: int = 3
    warm_runs: int = 10
    positions: int = 50
    memory_iterations: int = 200
    stress_lines: int = 5000
    edits: int = 20
    debounce_ms: int = 300
    help_dir: Path | None = HELP_DIR     # None: no help directory (hover without help)
    server: bool = True
    label: str = LABEL_DEFAULT


# ---- statistics and host ----

def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile: the smallest value with at least q of the values at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q * len(ordered))) - 1]


def summary(values_ms: list[float]) -> dict:
    r = (lambda x: None if x is None else round(x, 3))
    return {"n": len(values_ms), "p50_ms": r(percentile(values_ms, 0.5)),
            "p95_ms": r(percentile(values_ms, 0.95)),
            "min_ms": r(min(values_ms)) if values_ms else None,
            "max_ms": r(max(values_ms)) if values_ms else None}


def loadavg() -> list[float] | None:
    return [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else None


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                             timeout=20, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def host_info(cfg: Config) -> dict:
    import opencrbasic
    tracked = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "system": platform.system(), "release": platform.release(), "machine": platform.machine(),
        "platform": platform.platform(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "loadavg_start": loadavg(),
        "revision": _git("rev-parse", "--short", "HEAD"),
        "tracked_changes": None if tracked is None else bool(tracked),
        "package": str(Path(opencrbasic.__file__).resolve()),
        "help_directory": str(cfg.help_dir) if cfg.help_dir else None,
        "label": cfg.label,
    }


def _progress(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


# ---- inputs ----

def main_variants() -> tuple[bytes, bytes]:
    """Main.CR6 bytes, and a copy with one undefined-name assignment after BeginProg (an error
    that replaces the program's one warning, so consecutive analyses differ)."""
    base = MAIN.read_bytes()
    lines = base.split(b"\n")
    at = next(i for i, line in enumerate(lines) if line.strip().lower() == b"beginprog")
    edit = b"  BenchInjected = BenchUndefined + 1"
    return base, b"\n".join(lines[:at + 1] + [edit] + lines[at + 1:])


def write_stress(directory: Path, lines_target: int) -> Path:
    """A deterministic program of 11 lines per routine (its Public, the Sub with a Dim, an If and a
    For, and its call in the Scan) plus 4 footer lines: about lines_target lines."""
    count = max(1, round((lines_target - 4) / 11))
    out: list[str] = [f"Public Stress{k}_v As Long" for k in range(count)]
    for k in range(count):
        out += [f"Sub Stress{k}", "  Dim t As Float", "  Dim j As Long", f"  t = Stress{k}_v * 2 + 1",
                f"  If t > 10 Then Stress{k}_v = 0", "  For j = 1 To 3", f"    Stress{k}_v = Stress{k}_v + j",
                "  Next", "EndSub"]
    out += ["BeginProg", "  Scan(1,Sec,0,0)"] + [f"    Stress{k}" for k in range(count)]
    out += ["  NextScan", "EndProg"]
    path = directory / "Stress.CR6"
    path.write_text("\n".join(out) + "\n", encoding="ascii")
    return path


def pick_positions(text: str, count: int) -> list[tuple[int, int]]:
    """Up to `count` identifier positions, evenly spaced over all of them (deterministic)."""
    spots = []
    for line_no, line in enumerate(text.split("\n")):
        for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", line):
            spots.append((line_no, m.start() + len(m.group()) // 2))
    return _spread(spots, count)


def _spread(items: list, count: int) -> list:
    if len(items) <= count:
        return list(items)
    return [items[i * len(items) // count] for i in range(count)]


# ---- child processes (cold start and memory need a fresh process) ----

def _rss_kib() -> dict:
    out = {"vmrss_kib": None, "maxrss_kib": None}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                out["vmrss_kib"] = int(line.split()[1])
    except OSError:
        pass
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["maxrss_kib"] = peak // 1024 if sys.platform == "darwin" else peak
    except ImportError:
        pass
    return out


def _child(args: argparse.Namespace) -> dict:
    if args.child == "cold":
        request = AnalysisRequest(Path(args.entry).absolute())
        start = time.perf_counter()
        result = analyze(request)
        ms = (time.perf_counter() - start) * 1000
        return {"ms": round(ms, 3), "diagnostics": len(result.diagnostics), "ok": result.ok}
    base, variant = main_variants()
    entry = MAIN
    analyze(AnalysisRequest(entry, overlays={entry: base}))       # baseline: the first analysis
    samples = [{"iteration": 0, **_rss_kib()}]
    step = max(1, args.iterations // 4)
    times: list[float] = []
    last = None
    for i in range(1, args.iterations + 1):
        data = variant if i % 2 else base
        start = time.perf_counter()
        last = analyze(AnalysisRequest(entry, overlays={entry: data}))
        times.append((time.perf_counter() - start) * 1000)
        if i % step == 0 or i == args.iterations:
            samples.append({"iteration": i, **_rss_kib()})
    return {"iterations": args.iterations, "samples": samples, "ms": summary(times),
            "last_ok": last.ok if last else None,
            "last_diagnostics": len(last.diagnostics) if last else None}


def run_child(*args: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("OPENCRBASIC_STRICT", None)
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args],
                          capture_output=True, text=True, env=env, timeout=3600)
    if proc.returncode:
        raise RuntimeError(f"child {args} failed ({proc.returncode}):\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---- measurements ----

def measure_cold(programs: list[Path], repeats: int) -> dict:
    out = {}
    for path in programs:
        runs = [run_child("--child", "cold", "--entry", str(path)) for _ in range(repeats)]
        out[path.name] = {**summary([r["ms"] for r in runs]), "diagnostics": runs[-1]["diagnostics"]}
        _progress(f"cold {path.name}: p50 {out[path.name]['p50_ms']} ms")
    return out


def measure_warm(programs: list[Path], runs: int) -> dict:
    out = {}
    for path in programs:
        entry = path.absolute()
        analyze(AnalysisRequest(entry))                             # untimed warm-up
        times, last = [], None
        for _ in range(runs):
            start = time.perf_counter()
            last = analyze(AnalysisRequest(entry))
            times.append((time.perf_counter() - start) * 1000)
        assert last is not None, "runs must be at least 1"
        out[path.name] = {**summary(times), "diagnostics": len(last.diagnostics), "ok": last.ok}
        _progress(f"warm {path.name}: p50 {out[path.name]['p50_ms']} ms")
    return out


def measure_positions(count: int, help_dir: Path | None) -> dict:
    base = MAIN.read_bytes()
    text = base.decode("latin-1")
    result = analyze(AnalysisRequest(MAIN))
    provider = HelpProvider(help_dir) if help_dir else None
    spots = pick_positions(text, count)
    call_spots = []
    for line, col in pick_positions(text, 10 ** 9):
        ctx = H.call_context(text, line, col)
        if ctx is not None:
            call_spots.append((ctx.name, ctx.active))
    sig_args = _spread(call_spots, count)
    whole = [()] * count

    ops = {
        "completion": (lambda line, col: editing.completion_candidates(result, text, line, col, PROFILE),
                       spots, lambda r: len(r.items) > 0),
        "hover": (lambda line, col: H.hover(result, text, line, col, PROFILE, help_provider=provider),
                  spots, lambda r: r is not None),
        "signature_help": (lambda name, active: H.signature_help(name, active, PROFILE, help_provider=provider),
                           sig_args, lambda r: r is not None),
        "definition": (lambda line, col: navigation.definition(result, MAIN, line, col),
                       spots, lambda r: len(r) > 0),
        "document_symbols": (lambda: editing.document_symbols(result, MAIN, PROFILE), whole,
                             lambda r: len(r) > 0),
        "folding_ranges": (lambda: editing.folding_ranges(text, PROFILE), whole, lambda r: len(r) > 0),
    }
    out: dict[str, object] = {"positions": len(spots), "signature_positions": len(sig_args),
           "call_positions_available": len(call_spots),
           "help_directory": str(help_dir) if help_dir else None}
    if provider is not None:
        start = time.perf_counter()
        provider.lookup("VoltSe")
        out["help_index_first_lookup_ms"] = round((time.perf_counter() - start) * 1000, 3)
    for name, (fn, args_list, hit) in ops.items():
        if not args_list:
            out[name] = {**summary([]), "hits": 0, "first_call_ms": None}
            continue
        start = time.perf_counter()
        first = fn(*args_list[0])
        first_ms = (time.perf_counter() - start) * 1000
        times, hits = [], 0
        for args in args_list:
            start = time.perf_counter()
            res = fn(*args)
            times.append((time.perf_counter() - start) * 1000)
            hits += bool(hit(res))
        stats = {**summary(times), "hits": hits, "first_call_ms": round(first_ms, 3),
                 "first_call_hit": bool(hit(first))}
        out[name] = stats
        _progress(f"position {name}: p50 {stats['p50_ms']} ms, hits {hits}/{len(args_list)}")
    return out


def measure_server(cfg: Config) -> dict:
    import lspclient
    base, variant = main_variants()
    base_text, variant_text = base.decode("latin-1"), variant.decode("latin-1")
    client = lspclient.LspClient()
    try:
        start = time.perf_counter()
        client.initialize(CRBASIC, {"entries": ["Main.CR6"], "debounceMs": cfg.debounce_ms,
                                    **SERVER_FAST})
        init_ms = (time.perf_counter() - start) * 1000
        # A clean closed entry intentionally has no initial empty publication. Open the
        # error variant so the first measured publish proves that analysis completed.
        client.open(MAIN, text=variant_text, version=1)
        client.wait_diagnostics(MAIN, predicate=lambda ds: any(d["severity"] == 1 for d in ds),
                                timeout=180)
        first_ms = (time.perf_counter() - start) * 1000
        raw, version = [], 1
        for k in range(cfg.edits):
            want_error = k % 2 == 1
            version += 1
            before = len(client.publishes(MAIN))
            start = time.perf_counter()
            client.change(MAIN, variant_text if want_error else base_text, version)
            client.wait_diagnostics(
                MAIN, predicate=lambda ds, w=want_error: any(d["severity"] == 1 for d in ds) == w,
                timeout=120, after=before)
            raw.append((time.perf_counter() - start) * 1000)
        _progress(f"server: first publish {first_ms:.0f} ms, edits {len(raw)}")
        exit_status = client.shutdown(timeout=60)
    finally:
        client.kill()
    time.sleep(0.2)                  # let the stderr reader thread drain after exit
    stderr = client.stderr_text
    banner = next((line for line in stderr.splitlines() if line.startswith("opencrbasic-lsp:")), None)
    after_debounce = [max(0.0, ms - cfg.debounce_ms) for ms in raw]
    return {"debounce_ms": cfg.debounce_ms, "initialize_request_ms": round(init_ms, 3),
            "initialize_to_first_publish_ms": round(first_ms, 3),
            "edits": len(raw), "exit_status": exit_status, "banner": banner,
            "traceback_in_stderr": "Traceback" in stderr,
            "didchange_to_publish": summary(raw),
            "didchange_to_publish_after_debounce": summary(after_debounce)}


def run_benchmark(cfg: Config) -> dict:
    programs = list(cfg.programs) or sorted(CRBASIC.glob("*.CR6"))
    report: dict = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "host": host_info(cfg),
                    "parameters": {k: str(v) if isinstance(v, Path) else v
                                   for k, v in cfg.__dict__.items() if k != "programs"},
                    "programs": [str(p) for p in programs]}
    report["cold"] = measure_cold(programs, cfg.cold_repeats)
    report["warm"] = measure_warm(programs, cfg.warm_runs)
    report["positions"] = measure_positions(cfg.positions, cfg.help_dir)
    mem = run_child("--child", "memory", "--iterations", str(cfg.memory_iterations))
    report["memory"] = mem
    _progress(f"memory: {mem['iterations']} analyses, maxrss {mem['samples'][-1]['maxrss_kib']} KiB")
    tmp = Path(tempfile.mkdtemp(prefix="lsp_bench_"))
    try:
        stress = write_stress(tmp, cfg.stress_lines)
        line_count = len(stress.read_text(encoding="ascii").splitlines())
        cold = measure_cold([stress], cfg.cold_repeats)[stress.name]
        warm = measure_warm([stress], cfg.warm_runs)[stress.name]
        report["stress"] = {"lines": line_count, "cold": cold, "warm": warm}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if cfg.server:
        report["server"] = measure_server(cfg)
        _progress(f"server: exit {report['server']['exit_status']}")
    report["loadavg_end"] = loadavg()
    return report


# ---- report ----

def _ms(value) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def render_markdown(report: dict) -> str:
    host, par = report["host"], report["parameters"]
    lines = [
        "# LSP-08a benchmark results", "",
        f"Label: **{host['label']}**. Generated {report['generated_at']} by `tools/lsp_bench.py`.", "",
        "Host and revision. Timings are wall-clock: compare them only under the load recorded here.", "",
        "| Item | Value |", "|---|---|",
        f"| Platform | {host['platform']} ({host['machine']}) |",
        f"| Python | {host['python']} |",
        f"| CPU count | {host['cpu_count']} |",
        f"| Load average at start (1, 5, 15 min) | {host['loadavg_start']} |",
        f"| Load average at end | {report['loadavg_end']} |",
        f"| Revision | {host['revision']} (tracked changes: {host['tracked_changes']}) |",
        f"| Package | `{host['package']}` |",
        f"| Help directory | {host['help_directory'] or 'none'} |",
        f"| Parameters | {', '.join(f'{k}={v}' for k, v in par.items())} |", "",
        "Percentiles are nearest-rank over the listed samples. Cold runs use fresh child processes.", "",
        "## Cold and warm analysis (`analysis.analyze`)", "",
        "| Program | Cold n | Cold p50 ms | Cold p95 ms | Warm n | Warm p50 ms | Warm p95 ms | Diagnostics |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in report["cold"]:
        c, w = report["cold"][name], report["warm"][name]
        lines.append(f"| {name} | {c['n']} | {_ms(c['p50_ms'])} | {_ms(c['p95_ms'])} | {w['n']} | "
                     f"{_ms(w['p50_ms'])} | {_ms(w['p95_ms'])} | {w['diagnostics']} |")
    s = report["stress"]
    lines += ["", f"Generated large program (`Stress.CR6`, {s['lines']} lines, temporary directory, "
              f"not committed): diagnostics ok={s['warm']['ok']}, {s['warm']['diagnostics']} reported.", "",
              "| Case | n | p50 ms | p95 ms | max ms |", "|---|---|---|---|---|",
              f"| stress cold | {s['cold']['n']} | {_ms(s['cold']['p50_ms'])} | {_ms(s['cold']['p95_ms'])} | "
              f"{_ms(s['cold']['max_ms'])} |",
              f"| stress warm | {s['warm']['n']} | {_ms(s['warm']['p50_ms'])} | {_ms(s['warm']['p95_ms'])} | "
              f"{_ms(s['warm']['max_ms'])} |", ""]
    pos = report["positions"]
    lines += ["## Warm editor operations (`Main.CR6`, deterministic positions)", "",
              f"{pos['positions']} identifier positions (signature help: {pos['signature_positions']} "
              f"positions inside calls, of {pos['call_positions_available']} available). "
              "Document symbols and folding ranges are whole-file calls, repeated the same number of "
              "times. A hit is a non-empty result.", "",
              "| Operation | n | p50 ms | p95 ms | max ms | Hits | First call ms |",
              "|---|---|---|---|---|---|---|"]
    for name in ("completion", "hover", "signature_help", "definition", "document_symbols", "folding_ranges"):
        o = pos[name]
        lines.append(f"| {name} | {o['n']} | {_ms(o['p50_ms'])} | {_ms(o['p95_ms'])} | {_ms(o['max_ms'])} | "
                     f"{o['hits']} | {_ms(o['first_call_ms'])} |")
    if "help_index_first_lookup_ms" in pos:
        lines += ["", f"Help index: first lookup {pos['help_index_first_lookup_ms']} ms (one-time parse)."]
    mem = report["memory"]
    first, last = mem["samples"][0], mem["samples"][-1]
    lines += ["", "## Repeated-edit memory", "",
              f"{mem['iterations']} analyses of `Main.CR6`, alternating disk bytes and an edited copy "
              "(child process). RSS in KiB.", "",
              "| Iteration | VmRSS KiB | Peak RSS KiB (ru_maxrss) |", "|---|---|---|"]
    lines += [f"| {x['iteration']} | {x['vmrss_kib']} | {x['maxrss_kib']} |" for x in mem["samples"]]
    lines += ["", f"Per-analysis wall time over the {mem['ms']['n']} timed analyses (iterations 1 to "
              f"{mem['iterations']}): p50 {_ms(mem['ms']['p50_ms'])} ms, p95 {_ms(mem['ms']['p95_ms'])} ms. "
              f"Peak RSS at iteration 0 and {last['iteration']}: {first['maxrss_kib']} and "
              f"{last['maxrss_kib']} KiB."]
    srv = report.get("server")
    if srv:
        lines += ["", "## Stdio server (end to end, `Main.CR6`)", "",
                  f"Banner: `{srv['banner']}`. Exit status after shutdown: {srv['exit_status']}. "
                  f"Traceback in stderr: {srv['traceback_in_stderr']}. Debounce {srv['debounce_ms']} ms "
                  "(configured; subtracted in the second table).", "",
                  "| Measure | n | p50 ms | p95 ms | max ms |", "|---|---|---|---|---|",
                  f"| initialize -> first publish | 1 | {_ms(srv['initialize_to_first_publish_ms'])} | - | - |",
                  f"| initialize request | 1 | {_ms(srv['initialize_request_ms'])} | - | - |"]
        d, a = srv["didchange_to_publish"], srv["didchange_to_publish_after_debounce"]
        lines += [f"| didChange -> publish | {d['n']} | {_ms(d['p50_ms'])} | {_ms(d['p95_ms'])} | "
                  f"{_ms(d['max_ms'])} |",
                  f"| didChange -> publish, minus debounce | {a['n']} | {_ms(a['p50_ms'])} | "
                  f"{_ms(a['p95_ms'])} | {_ms(a['max_ms'])} |"]
    lines += ["", "## Targets (compared, not asserted; LSP_PLAN.md proposed local goals)", "",
              "| Target | Observed p95 ms | Target ms | Within target |", "|---|---|---|---|"]
    comparisons = [
        ("hover", pos["hover"]["p95_ms"], TARGETS["hover_p95_ms"]),
        ("completion", pos["completion"]["p95_ms"], TARGETS["completion_p95_ms"]),
    ]
    if srv:
        comparisons.append(("diagnostics after debounce", srv["didchange_to_publish_after_debounce"]["p95_ms"],
                            TARGETS["diagnostics_after_debounce_p95_ms"]))
    for name, observed, target in comparisons:
        within = "not measured" if observed is None else ("yes" if observed < target else "no")
        lines.append(f"| {name} | {_ms(observed)} | {target:g} | {within} |")
    lines += ["", "Observed on this host and load only; a different host or load can change every row.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    os.environ.pop("OPENCRBASIC_STRICT", None)  # as a program only: importing must not change the caller's mode
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--child", choices=("cold", "memory"), help=argparse.SUPPRESS)
    parser.add_argument("--entry", help=argparse.SUPPRESS)
    parser.add_argument("--iterations", type=int, default=200, help=argparse.SUPPRESS)
    parser.add_argument("--json", type=Path, help="write the full data to this path")
    parser.add_argument("--label", default=LABEL_DEFAULT, help="host label written into the report")
    parser.add_argument("--cold-repeats", type=int, default=3)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument("--positions", type=int, default=50)
    parser.add_argument("--memory-iterations", type=int, default=200)
    parser.add_argument("--stress-lines", type=int, default=5000)
    parser.add_argument("--edits", type=int, default=20)
    parser.add_argument("--debounce-ms", type=int, default=300)
    parser.add_argument("--help-dir", type=Path, default=HELP_DIR,
                        help="directory with Instructions/ and parameters/ ('none' disables)")
    parser.add_argument("--no-server", action="store_true", help="skip the stdio server section")
    args = parser.parse_args(argv)
    if args.child:
        print(json.dumps(_child(args)))
        return 0
    help_dir = None if str(args.help_dir) == "none" or not args.help_dir.is_dir() else args.help_dir
    cfg = Config(cold_repeats=args.cold_repeats, warm_runs=args.warm_runs, positions=args.positions,
                 memory_iterations=args.memory_iterations, stress_lines=args.stress_lines,
                 edits=args.edits, debounce_ms=args.debounce_ms, help_dir=help_dir,
                 server=not args.no_server, label=args.label)
    report = run_benchmark(cfg)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
