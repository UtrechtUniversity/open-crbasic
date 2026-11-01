#!/usr/bin/env python3
"""Regenerate tests/fixtures/tdf_rules.json from cases in tests/fixtures/tdf_rules_cases.json
({id, source}) using the real CR6Comp.exe: `python3 tools/tdf_fixture_gen.py`."""

import base64
import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tdftest  # noqa: E402

FIX = HERE.parent / "tests" / "fixtures"
cases = json.loads((FIX / "tdf_rules_cases.json").read_text())
out = {"compiler_sha256": hashlib.sha256(tdftest.DEFAULT_COMPILER.read_bytes()).hexdigest(), "cases": []}
for c in cases:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.CR6"
        p.write_text(c["source"])
        r = tdftest.run_oracle_tdf(p, Path(d))
    tdf_blob, lst_blob = r["tdf"], r["lst"]
    if not r["ok"] or tdf_blob is None or lst_blob is None:
        sys.exit(f"{c['id']}: real compiler rejected: {r['stdout']}")
    out["cases"].append(
        {
            "id": c["id"],
            "source": c["source"],
            "tdf": base64.b64encode(tdf_blob).decode(),
            "lst": base64.b64encode(lst_blob).decode(),
        }
    )
(FIX / "tdf_rules.json").write_text(json.dumps(out, indent=1))
print(len(out["cases"]), "cases")
