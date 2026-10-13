"""Re-run every case of an observation fixture through the reference compiler.

usage: uv run tools/fixture_audit.py [--no-cache] [--jobs N] tests/fixtures/<name>.json [...]
Fixture cases need `logger`, `source`, `expected_exit`, `expected_stdout`. Exits 1 if any case's
recorded expectation differs from the real compiler. The program file name is the one the
recorded output names (`t.CR6`, `t.CR1X`, ...). By default results come from the content-keyed
oracle cache; `--no-cache` ignores it (and stores nothing), so every case is a fresh compiler run.
`--jobs N` runs N cases in parallel, each in its own temp dir. A runner failure (page fault,
timeout) is retried once, then reported as NO-VERDICT (exit 3 if nothing differs but some case
has no verdict).
"""
import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import oracle  # noqa: E402
from opencrbasic import profiles  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("fixtures", nargs="+", help="fixture JSON files")
    parser.add_argument("--no-cache", action="store_true", help="bypass the oracle cache")
    parser.add_argument("--jobs", type=int, default=1, help="parallel compiler runs (default 1)")
    return parser.parse_args(argv)


def audit_case(case, use_cache=True):
    """Return the real result dict for one case, or None when the runner gave no verdict."""
    for _attempt in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / case["expected_stdout"].split(" -- ", 1)[0].strip()
            for name, text in case.get("siblings", {}).items():
                (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / name).write_text(text, encoding="utf-8")
            program.write_text(case["source"], encoding="utf-8")
            exe = oracle.REPO_ROOT / "compiler" / profiles.get(case["logger"]).exe_name
            try:
                return oracle.run_oracle(
                    program, siblings_dir=Path(tmp), compiler_path=exe, use_cache=use_cache
                )
            except oracle.OracleError:
                continue
    return None


def differs(case, real):
    return real["exit"] != case["expected_exit"] or real["stdout"].rstrip("\n") != case["expected_stdout"].rstrip("\n")


def main(argv=None):
    args = parse_args(argv)
    bad = 0
    novert = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for fixture in args.fixtures:
            cases = json.loads(Path(fixture).read_text(encoding="utf-8"))["cases"]
            results = list(pool.map(lambda c: audit_case(c, not args.no_cache), cases))
            fbad = fnov = 0
            for case, real in zip(cases, results):
                if real is None:
                    fnov += 1
                    print(f"NO-VERDICT {fixture} {case['id']}")
                elif differs(case, real):
                    fbad += 1
                    print(f"DIFF {fixture} {case['id']}\n  fixture: {case['expected_stdout']!r}\n  real:    {real['stdout']!r}")
            print(f"FIXTURE {fixture}: cases={len(cases)} differing={fbad} no-verdict={fnov}", flush=True)
            bad += fbad
            novert += fnov
    print(f"{bad} differing case(s), {novert} without verdict")
    return 1 if bad else (3 if novert else 0)


if __name__ == "__main__":
    sys.exit(main())
