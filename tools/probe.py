"""Scratch probe: run snippet variants through oracle and opencrbasic on chosen loggers.

usage: uv run --no-sync python tools/probe.py <template-file> <cases-file> [loggers...]
template contains `$BODY$`; cases file has one case per line (`\\n` escapes allowed).
"""
import subprocess
import sys
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
import oracle  # noqa: E402
from opencrbasic import profiles  # noqa: E402
from opencrbasic.compiler import compile_file  # noqa: E402

template = Path(sys.argv[1]).read_text()
cases = [c.replace("\\n", "\n") for c in Path(sys.argv[2]).read_text().splitlines() if c.strip()]
loggers = sys.argv[3:] or ["cr6"]
out = ROOT / "scratch" / "probe-out"
out.mkdir(exist_ok=True)
for case in cases:
    text = template.replace("$BODY$", case)
    name = hashlib.sha1(text.encode()).hexdigest()[:10] + ".CR6"
    path = out / name
    path.write_text(text.replace("\n", "\r\n"))
    for lg in loggers:
        prof = profiles.get(lg)
        theirs = oracle.run_oracle(path, compiler_path=ROOT.parent / "compiler" / prof.exe_name)
        ours = compile_file(path, prof)
        t = "\n".join(theirs["stdout"].splitlines()[1:]).strip() if isinstance(theirs, dict) else str(theirs)
        o = "\n".join(ours.result_lines()[1:]).strip()
        mark = "==" if t == o else "!="
        print(f"[{lg}] {mark} {case!r}\n   T: {t.replace(chr(10), ' | ')}")
        if mark == "!=":
            print(f"   O: {o.replace(chr(10), ' | ')}")
