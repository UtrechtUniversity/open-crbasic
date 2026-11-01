"""Structure-variable use in assignments: an unknown member (`inp.flagX`) and, on CR300/CR350,
a structure on one side of `=` only. Rules and oracle evidence: `docs/checker.md` "Structure
member and assignment checks" (`tests/fixtures/decl_structs.json`).

Deliberately narrow: only a plain (non-compound) `Assignment` whose every name is a declared
variable of a known type is judged; anything else (undefined names, aliases, pointers, nested
structure members, functions) is left silent, the safe side of AGENTS.md.
"""

from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from . import expressions, structure

_SCALAR_TYPES = frozenset({"FLOAT", "LONG", "BOOLEAN", "STRING", "DOUBLE"})
_WRONG_TYPE = "Structured Variable assignment illegal, wrong type"
_ILLEGAL = "Illegal structure type assignment"
_UNMODELLED = [("?", "", 0)]


def _structure_members(program: S.Program) -> dict[str, dict[str, str]]:
    """Upper-case `StructureType` name -> {upper-case member name -> upper-case type}."""
    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for st in program.statements:
        if (
            isinstance(st, S.Instruction)
            and st.keyword.upper() == "STRUCTURETYPE"
            and st.args
            and isinstance(st.args[0], S.Name)
        ):
            current = result.setdefault(st.args[0].name.upper(), {})
        elif isinstance(st, S.Instruction) and st.keyword.upper() == "ENDSTRUCTURETYPE":
            current = None
        elif current is not None and isinstance(st, S.Declaration) and st.kind == "STRUCTUREMEMBER":
            for v in st.vars:
                current[v.name.upper()] = (v.type or "FLOAT").upper()
    return result


def _variable_kinds(program: S.Program, structs: dict) -> dict[str, str]:
    """Upper-case variable name -> `"STRUCT:<type>"` or its scalar type. A name declared twice
    differently, or of any other type (pointer, `UInt2`, ...), is left out."""
    kinds: dict[str, str] = {}
    clash: set[str] = set()
    for st in program.statements:
        if not (isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC")):
            continue
        for v in st.vars:
            kind = (v.type or "FLOAT").upper()
            if v.paren_dim:  # a parenthesised dimension makes a Float array (docs/checker.md)
                kind = "FLOAT"
            if kind in structs:
                kind = f"STRUCT:{kind}"
            elif kind not in _SCALAR_TYPES:
                clash.add(v.name.upper())
                continue
            if kinds.setdefault(v.name.upper(), kind) != kind:
                clash.add(v.name.upper())
    return {k: v for k, v in kinds.items() if k not in clash}


def _names(expr: S.Expr | None) -> list[str]:
    """Every variable-like name inside `expr`, or `["?"]` for a node this module does not model."""
    if expr is None or isinstance(expr, (S.Number, S.String)):
        return []
    if isinstance(expr, S.Name):
        return [expr.name.upper()]
    if isinstance(expr, S.Index):
        return [expr.name.upper()] + [n for a in expr.args for n in _names(a)]
    if isinstance(expr, S.Unary):
        return _names(expr.operand)
    if isinstance(expr, S.Binary):
        return _names(expr.left) + _names(expr.right)
    return ["?"]


def _member_chain(expr: S.Member) -> tuple[S.Name | S.Index, list[str]] | None:
    """`(root, [member, ...])` for `a.b.c` / `a(i).b`, or `None` when an inner link has index
    arguments."""
    path: list[str] = []
    node: S.Expr | None = expr
    while isinstance(node, S.Member):
        if node is not expr and node.args:
            return None
        path.append(node.member)
        node = node.base
    return (node, path[::-1]) if isinstance(node, (S.Name, S.Index)) else None


def _member_errors(expr: S.Expr | None, kinds: dict[str, str], structs: dict) -> list[tuple]:
    """`(base spelling, path text, index)` for each member access in `expr` naming a member its
    structure lacks (or descending below a scalar member); `_UNMODELLED` for anything else."""
    if isinstance(expr, S.Member):
        chain = _member_chain(expr)
        root = chain[0] if chain else None
        kind = kinds.get(root.name.upper()) if root is not None else None
        if chain is None or (kind is not None and not kind.startswith("STRUCT:")):
            return _UNMODELLED  # (`G.x`, `Sample1.x` on a plain variable: other messages)
        if kind is None:
            return []  # a table or system-table reference (`Public.Record`)
        root, path = chain
        first = structs[kind[7:]].get(path[0].upper())
        index = 1
        if expr.args:
            a = expr.args[0] if len(expr.args) == 1 else None
            if not isinstance(a, S.Number) or not isinstance(a.value, int):
                return _UNMODELLED
            index = a.value
        if first is None or (len(path) > 1 and first in _SCALAR_TYPES):
            found = [(root.name, ".".join(path), index)]
        elif first in structs or len(path) > 1:
            return _UNMODELLED  # nested structure members are not modelled
        else:
            found = []
        for a in root.args if isinstance(root, S.Index) else []:
            found += _member_errors(a, kinds, structs)
        return found
    if isinstance(expr, S.Index):
        return [e for a in expr.args for e in _member_errors(a, kinds, structs)]
    if isinstance(expr, S.Unary):
        return _member_errors(expr.operand, kinds, structs)
    if isinstance(expr, S.Binary):
        return _member_errors(expr.left, kinds, structs) + _member_errors(expr.right, kinds, structs)
    return []


def check_members(program: S.Program, profile, diags: Diagnostics, flat: S.Program) -> None:
    """An unknown structure member in an assignment. CR6/CR1000X (pass 1): `In structure inp,
    could not find .flagX`, one line per access, left side first. CR300/CR350: `Could not find
    inp.flagX(1).` per access, and a bad left side ends the statement with `Invalid assignment in
    expression: <line>` (oracle, 2026-10-03)."""
    structs = _structure_members(program)
    if not structs:
        return
    kinds = _variable_kinds(program, structs)
    declared = {v.name.upper() for st in program.statements if isinstance(st, S.Declaration) for v in st.vars}
    for st in program.statements:  # (`Sub S(C As Pt)`: `C.y` is checked too, oracle 2026-10-12)
        if isinstance(st, S.SubDef):
            for p in st.params:
                if p.type and p.type.upper() in structs and p.name.upper() not in declared:
                    kinds.setdefault(p.name.upper(), f"STRUCT:{p.type.upper()}")
    known = expressions._known_names(program, profile)
    for st in flat.statements:
        if (
            not isinstance(st, S.Assignment)
            or st.compound
            or expressions._undefined_names(st.target, known)
            or expressions._undefined_names(st.value, known)
        ):
            continue
        left, right = _member_errors(st.target, kinds, structs), _member_errors(st.value, kinds, structs)
        if any(e[0] == "?" for e in left + right) or not (left or right):
            continue
        if not profile.single_pass:
            for base, path, _ in left + right:
                diags.error(st.line, f"In structure {base}, could not find .{path}", st.file)
        elif left:
            base, path, index = left[0]
            sep = "\n" if profile.fallback_echo_newline and st.at_eol else ""
            diags.items += [
                Diagnostic("error", st.line, f"Could not find {base}.{path}({index}).", st.file, cascade=False),
                Diagnostic(
                    "error", st.line, f"Invalid assignment in expression: {st.text}{sep}.", st.file, cascade=False
                ),
            ]
        else:
            diags.items += [
                Diagnostic("error", st.line, f"Could not find {base}.{path}({index}).", st.file, cascade=False)
                for base, path, index in right
            ]


def _operand_kind(expr: S.Expr | None, kinds: dict[str, str], structs: dict) -> str | None:
    """`"S"` (one structure), `"SA()"` (`name()` of a structure array), `"N"` (a non-structure
    value), `"M"` (a scalar structure member, which no target rejects), or `None` when not
    modelled."""
    if isinstance(expr, (S.Number, S.String)):
        return "N"
    if isinstance(expr, (S.Name, S.Index)):
        kind = kinds.get(expr.name.upper())
        if kind is None:
            return None
        if not kind.startswith("STRUCT:"):
            return "N"
        return "SA()" if isinstance(expr, S.Index) and not expr.args else "S"
    if isinstance(expr, S.Member):
        chain = _member_chain(expr)
        kind = kinds.get(chain[0].name.upper()) if chain else None
        members = structs.get(kind[7:], {}) if kind and kind.startswith("STRUCT:") else {}
        if chain and len(chain[1]) == 1 and members.get(chain[1][0].upper()) in _SCALAR_TYPES:
            return "M"  # a scalar member (`inp.value`): accepted on either kind of target
        return None
    if isinstance(expr, (S.Unary, S.Binary)):
        names = _names(expr)
        if "?" in names or any(n not in kinds for n in names):
            return None
        # `-inp` is a non-structure value; a binary expression over a structure is not modelled
        if isinstance(expr, S.Binary) and any(kinds[n].startswith("STRUCT:") for n in names):
            return None
        return "N"
    return None


def check_struct_assignment(program: S.Program, profile, diags: Diagnostics, flat: S.Program) -> None:
    """CR300/CR350: `=` with a structure on one side only (oracle, 2026-10-03). Structure target,
    other source: `Structured Variable assignment illegal, wrong type`; other target, structure
    source: `Illegal structure type assignment`; a `name()` structure array on either side adds
    `Invalid, or out of place expression: <line>`, and as target always gives the wrong-type line.
    CR6/CR1000X accept all of these."""
    if not profile.single_pass:
        return
    structs = _structure_members(program)
    if not structs:
        return
    kinds = _variable_kinds(program, structs)
    for st in flat.statements:
        if not isinstance(st, S.Assignment) or st.compound or not isinstance(st.target, (S.Name, S.Index)):
            continue
        left, right = _operand_kind(st.target, kinds, structs), _operand_kind(st.value, kinds, structs)
        if left is None or right is None:
            continue
        if left == "SA()":
            messages = [_WRONG_TYPE, None]
        elif left == "S" and right == "N":
            messages = [_WRONG_TYPE]
        elif left == "S" and right == "SA()":
            messages = [None]
        elif left == "N" and right == "S":
            messages = [_ILLEGAL]
        elif left == "N" and right == "SA()":
            messages = [_ILLEGAL, None]
        else:
            continue
        for m in messages:
            # (no `Too many parameters` cascade: oracle, 2026-10-03)
            diags.items.append(
                Diagnostic(
                    "error", st.line, m or structure._out_of_place(profile, st.text, st.at_eol), st.file, cascade=False
                )
            )
