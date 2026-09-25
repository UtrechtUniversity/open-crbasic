"""Post-table-build warnings that need the whole (post-`#If`-resolution) statement list, not just
what `tables.py`'s own per-table pass sees. Currently just the "declared but never called" check.
Rules and oracle evidence: `docs/checker.md`, `docs/tdf-rules.md` section 13.
"""

from __future__ import annotations

from .. import syntax as S
from ..diagnostics import Diagnostics


def _table_arg_name(stmt: S.Instruction) -> str | None:
    if stmt.args and isinstance(stmt.args[0], S.Name):
        return stmt.args[0].name
    return None


def check(program: S.Program, result, diags: Diagnostics) -> None:
    # User `DataTable(...)...EndTable` blocks only -- deliberately *not* `result.tables` (which
    # also carries the logger's own system tables, `Status`/`DataTableInfo`/`GlobalConfigTable`/
    # the `Public` table, none of which are ever `CallTable`d and must not get this warning).
    declared: dict[str, str] = {}
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE":
            name = _table_arg_name(stmt)
            if name:
                declared[name.upper()] = name

    # "Called" is deliberately not just `CallTable`: `AcceptDataRecords`' own TableName argument
    # also counts (oracle-confirmed regression, `acceptdatarecords_1.CR6`: `AcceptDataRecords
    # (3000,32769,CR1000XTable)` with no `CallTable` at all still gets no warning). Rather than
    # enumerate every instruction with a table-name argument, any bare-name argument anywhere
    # that matches a declared table counts as a use -- a table name is not reused as an ordinary
    # variable, so this is a safe superset of the real "called" set.
    called = {
        arg.name.upper()
        for stmt in program.statements
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() != "DATATABLE"
        for arg in stmt.args
        if isinstance(arg, S.Name) and arg.name.upper() in declared
    }
    for key, name in declared.items():
        if key not in called:
            # oracle-confirmed (docs/tdf-rules.md #13, `fixeduncalled`): fires whenever no
            # `CallTable`/`CallTable(...)` for this table survives `#If` resolution, regardless
            # of `DataInterval`/fixed size; `Main.CR6`'s `Shunt_Data` is the repo's own example
            # (its only `CallTable` is inside a false-by-default `#If`).
            diags.warning(0, f"Table {name}, is declared but never called.")
