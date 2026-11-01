"""Fixture-backed `Anonymous<n>` source names and non-variable String widths (docs/tdf-rules.md §9c);
fixture from tools/tdf_anonymous_fixture_gen.py (real compilers, all four loggers)."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from opencrbasic import parser, profiles, tdf  # noqa: E402
from opencrbasic.tables import build_tables  # noqa: E402

CASES = [
    (c["id"], lg, c["source"], exp)
    for c in json.loads((ROOT / "tests" / "fixtures" / "tdf_anonymous.json").read_text())["cases"]
    for lg, exp in c["expected"].items()
]


@pytest.mark.parametrize("cid,logger,source,expected", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_tdf_anonymous(cid, logger, source, expected, tmp_path):
    p = tmp_path / "t.CR6"
    p.write_text(source)
    program, _ = parser.parse_file(p)
    tables, _, _ = build_tables(program, profiles.get(logger), dict(program.consts))
    got = [
        [t.name, t.size, [[f.name, f.dimension, list(f.subdims), f.begin_index, f.processing] for f in t.fields]]
        for t in tdf.parse(tdf.serialize(tables))[1]
        if t.name not in ("Status", "DataTableInfo", "Public")
    ]
    assert got == expected
