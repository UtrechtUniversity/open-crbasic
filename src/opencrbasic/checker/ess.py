"""`ESSVariables` against variables the program declared itself (pass 2, all four loggers).

The real compiler walks its ESS table (`data/**/ess_variables.json`, `tools/extract_tables.py`) at
each `ESSVariables` statement. A name not yet declared is declared there; one already declared is
checked, every failing entry reported at that line in table order (oracle, 2026-10-01, decompiled
CR6 `DAT_005fd008` loop):

- `ESS variable %s must be declared as String` / `... as Long` when the type differs;
- `ESS variable of type string must be of size 255` for a string not declared `* 255`;
- `ESS variable %s must be dimensioned as %d` when the element count differs (a `LONG_ARRAY16`
  entry is declared `count x 16`, so a second `ESSVariables` reports each of them).
"""

import json
from functools import cache
from pathlib import Path

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics


@cache
def _table(data_dir: str) -> tuple[dict, ...]:
    path = Path(data_dir) / "ess_variables.json"
    return tuple(json.loads(path.read_text())) if path.exists() else ()


def _const_int(expr: S.Expr | None, env: consteval.Env) -> int | None:
    if expr is None or not consteval.is_constant(expr, env):
        return None
    try:
        value = consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    table = _table(str(profile.data_dir))
    if not table:
        return
    declared: dict[str, tuple[str, str, int | None, int | None]] = {}
    for stmt in program.statements:
        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            for v in stmt.vars:
                total: int | None = 1
                for d in v.dims:
                    n = _const_int(d, env)
                    total = None if n is None or total is None else total * n
                kind = (v.type or "FLOAT").upper()
                size = _const_int(v.string_size, env) if v.string_size is not None else 24
                declared.setdefault(v.name.upper(), (v.name, kind, total, size))
            continue
        if not (isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ESSVARIABLES"):
            continue
        for row in table:
            if not row["count"]:
                continue
            required = "STRING" if row["type"] == "STRING" else "LONG"
            have = declared.get(row["name"].upper())
            if have is None:
                total = row["count"] * (16 if row["type"] == "LONG_ARRAY16" else 1)
                declared[row["name"].upper()] = (row["name"], required, total, 255)
                continue
            spelling, kind, total, size = have
            if kind != required:
                diags.error(stmt.line, f"ESS variable {spelling} must be declared as "
                            f"{required.capitalize()}", stmt.file)
            elif required == "STRING" and size is not None and size != 255:
                diags.error(stmt.line, "ESS variable of type string must be of size 255", stmt.file)
            elif total is not None and total != row["count"]:
                diags.error(stmt.line, f"ESS variable {spelling} must be dimensioned as "
                            f"{row['count']}", stmt.file)
