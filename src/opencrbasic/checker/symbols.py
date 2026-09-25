"""Declared-name collection: `Sub`/`Function` names, used by `structure.py` to decide whether a
bare (no-`Call`-keyword) name is a legal statement. Rules and oracle evidence: `docs/checker.md`.
"""

from __future__ import annotations

from .. import syntax as S


def declared_subs(program: S.Program) -> set[str]:
    """Upper-case names of every `Sub`/`Function` declared anywhere in the program.

    A bare identifier (or `Name(args)` with no `Call` keyword) is only a legal CRBasic statement
    when it names an already-declared `Sub`/`Function` (oracle-confirmed, `docs/frontend.md`
    "Statements"; re-confirmed for the parenthesised-argument form in `docs/checker.md`). Building
    this set from the whole program (not just statements seen so far) is deliberately lenient
    about forward references: we would rather under-report ("verdict", not "exact") than reject a
    program the real compiler accepts (AGENTS.md priority order).
    """
    return {s.name.upper() for s in program.statements if isinstance(s, S.SubDef)}


# Instructions whose first argument is an identifier this checker treats as declaring a name a
# bare `Name` may later reference: a `DataTable`'s own name (referenced by `CallTable`/`Read`/...),
# or a `StructureType`/`ObjectType`'s own name (referenced as a `Sample`/`Average`/... `DataType`
# argument for a structured measurement, oracle-confirmed regression this session found,
# `structuretype_1.CR6`/`structuretype_2.CR6`: `Sample(3,Piezometer,VWSensor)` where `VWSensor` is
# the `StructureType` declared just above, not a fixed `data/symbols.json` `DataType` constant).
_NAME_DECLARING_INSTRUCTIONS = ("DATATABLE", "STRUCTURETYPE", "OBJECTTYPE")


def declared_names(program: S.Program) -> set[str]:
    """Every name a bare `Name` expression may legally reference: declared `Public`/`Dim`/`Const`/
    `ConstTable` variables, `Sub`/`Function` names and their parameters, `DataTable` and
    `StructureType`/`ObjectType` names, `Alias` targets, `For`-loop variables, and `Label`s (goto
    targets). Deliberately global (not scope- or order-checked): a name declared anywhere in the
    program, including inside a `Sub` it is not used in, is accepted everywhere, matching this
    checker's general stance of preferring under-reporting (AGENTS.md's "never reject an accepted
    program" priority) over a full scope analysis this project has not verified against the real
    compiler.
    """
    names: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Declaration):
            for v in stmt.vars:
                names.add(v.name.upper())
        elif isinstance(stmt, S.ConstDecl):
            names.add(stmt.name.upper())
        elif isinstance(stmt, S.SubDef):
            names.add(stmt.name.upper())
            for p in stmt.params:
                names.add(p.name.upper())
        elif isinstance(stmt, S.Label):
            names.add(stmt.name.upper())
        elif isinstance(stmt, S.For) and isinstance(stmt.var, S.Name):
            names.add(stmt.var.name.upper())
        elif isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            if kw in _NAME_DECLARING_INSTRUCTIONS and stmt.args and isinstance(stmt.args[0], S.Name):
                names.add(stmt.args[0].name.upper())
            elif kw == "ALIAS":
                for arg in stmt.args[1:]:
                    if isinstance(arg, S.Name):
                        names.add(arg.name.upper())
    return names
