"""Re-run every case of an observation fixture through the reference compiler.

usage: uv run tools/fixture_audit.py tests/fixtures/<name>.json [...]
Fixture cases need `logger`, `source`, `expected_exit`, `expected_stdout`. Exits 1 if any case's
recorded expectation differs from the real compiler. The program file name is the one the
recorded output names (`t.CR6`, `t.CR1X`, ...); results come from the content-keyed oracle cache.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import oracle  # noqa: E402
from opencrbasic import profiles  # noqa: E402

bad = 0
for fixture in sys.argv[1:]:
    for case in json.loads(Path(fixture).read_text(encoding="utf-8"))["cases"]:
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / case["expected_stdout"].split(" -- ", 1)[0].strip()
            for name, text in case.get("siblings", {}).items():
                (Path(tmp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / name).write_text(text, encoding="utf-8")
            program.write_text(case["source"], encoding="utf-8")
            exe = oracle.REPO_ROOT / "compiler" / profiles.get(case["logger"]).exe_name
            real = oracle.run_oracle(program, siblings_dir=Path(tmp), compiler_path=exe)
        if real["exit"] != case["expected_exit"] or real["stdout"].rstrip("\n") != case["expected_stdout"].rstrip("\n"):
            bad += 1
            print(f"DIFF {fixture} {case['id']}\n  fixture: {case['expected_stdout']!r}\n  real:    {real['stdout']!r}")
print(f"{bad} differing case(s)")
sys.exit(1 if bad else 0)
