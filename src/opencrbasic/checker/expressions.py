"""Name resolution: flags a bare identifier used as a direct instruction argument that resolves
nowhere. Deliberately narrow (see `check`'s docstring) to keep false positives at zero on the
corpus this project can check against. Rules and oracle evidence: `docs/checker.md`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

_TYPE_CODES_PATH = Path(__file__).resolve().parent.parent / "data" / "type_codes.json"

# `type_codes.json` `kind`s that route an argument through the real expression/named-constant
# resolver (`FUN_004b8700`/`FUN_004b5c50` and their `const`-only wrappers, `docs/type-codes.md`):
# an unresolved bare name at one of these positions is what produces `Invalid symbol in
# expression: %s.`. Every other kind either never runs general symbol resolution at all (`6`
# identifier/raw text such as `DataTable`'s or `ConstTable`'s own name, captured verbatim and
# validated -- or not -- by a later, different pass) or resolves a fixed keyword list through a
# separate function (`4`/`16`/`17`/`19` `FUN_004b5db0`, a different failure message). Restricting
# to this explicit include-list (rather than excluding the handful of kinds known *not* to
# resolve) is deliberate: the default for an unrecognised kind is "don't check", so a gap in this
# list only under-reports, never falsely rejects (AGENTS.md's priority order); the `ConstTable`
# regression this session found during development (`crbasic/Main.CR6`'s
# `ConstTable(GlobalConfigTable)`, kind `expr_marker`) is the concrete case that shape of bug
# looks like.
_RESOLVED_KINDS = frozenset({
    "const_expr", "expr", "const_expr_only", "bool_expr", "variable_or_array", "expr_or_name",
    "expr_typed", "expr_special", "expr_pair", "expr_list", "datatype_kw",
})


@lru_cache(maxsize=1)
def _type_code_kinds() -> dict[int, str]:
    data = json.loads(_TYPE_CODES_PATH.read_text())
    return {int(code): entry["kind"] for code, entry in data.items()}


def _resolved_positions(params: list[int]) -> set[int]:
    """0-based argument indices of `params` (an instruction's `data/instructions.json` `params`
    list) whose type code resolves through the general symbol/expression compiler."""
    kinds = _type_code_kinds()
    positions = set()
    for i, code in enumerate(params):
        base = code - 100 if code >= 100 else code
        if kinds.get(base) in _RESOLVED_KINDS:
            positions.add(i)
    return positions


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """Only checks a direct `Instruction` argument that parsed as a bare `Name`, at a position
    whose declared type code actually resolves through the general symbol/expression compiler
    (`_resolved_positions`) -- not one nested inside a larger expression, an array index,
    `Table.Field`, or a unary/binary operator, and not an identifier/raw-text-kind position such
    as a `DataTable`/`ConstTable` name. This project's evidence for the rule is all of this shape
    (a CR1000X-only terminal name such as `Vx1`/`P1`/`C5` used where CR6 does not define it --
    `docs/checker.md`); a full expression-tree name-resolution pass (scopes, structure members,
    aliasing, ...) is unverified and risks a false rejection (AGENTS.md's worst outcome), so it is
    left for later, more thoroughly oracle-tested work.
    """
    known = symbols.declared_names(program) | set(profile.symbols) | set(profile.functions) | set(profile.instructions)

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        for i in _resolved_positions(record["params"]):
            if i >= len(stmt.args):
                continue
            arg = stmt.args[i]
            if isinstance(arg, S.Name) and arg.name.upper() not in known:
                diags.error(stmt.line, f"Invalid symbol in expression: {arg.name}.", stmt.file)
