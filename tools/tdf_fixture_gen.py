#!/usr/bin/env python3
"""Regenerate tests/fixtures/tdf_rules.json from cases in tests/fixtures/tdf_rules_cases.json
({id, source}) using the real CR6Comp.exe: `python3 tools/tdf_fixture_gen.py`."""
import base64, hashlib, json, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tdftest  # noqa: E402

FIX = HERE.parent / "tests" / "fixtures"
cases = json.loads((FIX / "tdf_rules_cases.json").read_text())
out = {"compiler_sha256": hashlib.sha256(tdftest.DEFAULT_COMPILER.read_bytes()).hexdigest(),
       "cases": []}
for c in cases:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.CR6"
        p.write_text(c["source"])
        r = tdftest.run_oracle_tdf(p, Path(d))
    if not r["ok"]:
        sys.exit(f"{c['id']}: real compiler rejected: {r['stdout']}")
    out["cases"].append({"id": c["id"], "source": c["source"],
                         "tdf": base64.b64encode(r["tdf"]).decode(),
                         "lst": base64.b64encode(r["lst"]).decode()})
(FIX / "tdf_rules.json").write_text(json.dumps(out, indent=1))
print(len(out["cases"]), "cases")
