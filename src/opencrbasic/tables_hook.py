"""Wires `tables.build_tables` into a `CompileResult` (`compiler.CompileResult.tables` /
`.table_list`), for `checker.py` to call after a successful compile and for `cli.py`'s `-z`/`-l`.
"""

from __future__ import annotations

from . import syntax as S
from .tables import build_tables


def attach(program: S.Program, profile, result) -> None:
    tables, table_list_text, diags = build_tables(program, profile, dict(program.consts))
    result.tables = tables
    result.table_list = table_list_text
    for d in diags or ():
        result.diagnostics.warning(0, d)
