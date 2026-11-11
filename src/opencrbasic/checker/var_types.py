"""Variable-data-type argument checks (pass 2, tier C). Rules, fixtures and oracle evidence:
`docs/instructions.md` "Variable data types" (`tests/fixtures/var_types.json`).

Not a uniform per-type-code rule: each instruction applies its own requirement, so this module is
a small table of them. Unknown names, functions and operand mixes the oracle was not probed on
have no type (`None`), and a check that needs a type it does not have stays silent.
"""

import json
import math
import re
from pathlib import Path

from .. import consteval, tables
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import symbols

_NUMERIC = ("FLOAT", "LONG")
# built-in functions returning a String (`kind` 312, docs/binary.md), the same on all four loggers;
# `CType` returns the type its second argument names (oracle, round 67)
_STRING_FUNCTIONS = frozenset(
    f["name"]
    for f in json.loads((Path(__file__).resolve().parents[1] / "data" / "functions.json").read_text())
    if f.get("kind") == 312
) - {"CTYPE"}
_STATUS_STRING_FIELDS = frozenset(
    field["name"].upper() for field in tables._STATUS_JSON["fields"] if field["type"] == 11
)


def _types(program: S.Program, *, scoped: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    """Declared variable types and `Alias` name -> target variable name (upper case)."""
    types = symbols.declared_variable_types(program, prefer_last=scoped)
    alias: dict[str, str] = {}
    for st in program.statements:
        if scoped and isinstance(st, S.SubDef):
            for p in st.params:
                name = p.name.upper()
                types[name] = (p.type or "FLOAT").upper()
                alias.pop(name, None)
        if (
            isinstance(st, S.Instruction)
            and st.keyword.upper() == "ALIAS"
            and st.args
            and isinstance(st.args[0], (S.Name, S.Index))
        ):
            for a in st.args[1:]:
                if isinstance(a, S.Name):
                    alias[a.name.upper()] = st.args[0].name.upper()
    return types, alias


class _Typer:
    def __init__(self, program: S.Program, scope: S.Program | None = None, *, profile: Profile | None = None):
        visible = scope if scope is not None else program
        self.types, self.alias = _types(visible, scoped=scope is not None)
        self.consts = {st.name.upper(): st for st in visible.statements if isinstance(st, S.ConstDecl)}
        self.function_types = {
            st.name.upper(): st.return_type.split("*")[0].strip().upper()
            for st in program.statements
            if isinstance(st, S.SubDef) and st.keyword.upper() == "FUNCTION" and st.return_type
        }
        self.table_types: dict[str, dict[str, str]] = {}
        self.structure_types: dict[str, dict[str, str]] = {}
        if profile is not None:
            from . import structs

            self.structure_types = structs._structure_members(visible)
            self.table_types["STATUS"] = {n: "STRING" for n in _STATUS_STRING_FIELDS & profile.status_members}
            if profile.single_pass:
                public: list[S.Stmt] = [
                    st for st in visible.statements if isinstance(st, S.Declaration) and st.kind == "PUBLIC"
                ]
                self.table_types["PUBLIC"] = symbols.declared_variable_types(S.Program(visible.name, public))

    def base(self, name: str) -> str:
        return self.alias.get(name.upper(), name.upper())

    def variable(self, expr: S.Expr | None) -> str | None:
        """Upper-case declared-variable name an expression refers to, or None."""
        if isinstance(expr, (S.Name, S.Index)):
            name = self.base(expr.name)
            return name if name in self.types else None
        return None

    def of(self, expr: S.Expr | None, depth: int = 0) -> str | None:
        """FLOAT / LONG / STRING / BOOLEAN / DOUBLE, or None when not known."""
        if isinstance(expr, S.Number):
            text = expr.text.upper()
            if text.startswith("&"):
                return "LONG"
            return "FLOAT" if "." in text or "E" in text else "LONG"
        if isinstance(expr, S.String):
            return "STRING"
        var = self.variable(expr)
        if var is not None:
            return self.types[var]
        if isinstance(expr, S.Member):
            member = expr.member.upper()
            if isinstance(expr.base, S.Name) and expr.base.name.upper() in self.table_types:
                return self.table_types[expr.base.name.upper()].get(member)
            if self.structure_types:
                kind = self.of(expr.base, depth + 1)
                return self.structure_types.get(kind or "", {}).get(member)
        if isinstance(expr, S.Index):
            # a String-returning built-in or Function, or an `IIF` with a String arm (oracle, all
            # four loggers, round 65, docs/instructions.md#string-operands)
            name = expr.name.upper()
            if (
                name in _STRING_FUNCTIONS
                or self.function_types.get(name) == "STRING"
                or (name == "IIF" and len(expr.args) > 1 and self.of(expr.args[1], depth + 1) == "STRING")
                or (
                    name == "CTYPE"
                    and len(expr.args) > 1
                    and isinstance(expr.args[1], S.Name)
                    and expr.args[1].name.upper() == "STRING"
                )
            ):
                return "STRING"
        if isinstance(expr, S.Name):
            if expr.name.upper() in ("TRUE", "FALSE"):
                return "LONG"
            if expr.name.upper() == "NAN":
                return "FLOAT"
            const = self.consts.get(expr.name.upper())
            if const is None or depth > 8:
                return None
            if const.type:
                return const.type.upper()
            return self.of(const.value, depth + 1)
        if isinstance(expr, S.Unary) and expr.op in ("-", "+"):
            return self.of(expr.operand, depth + 1) if self.of(expr.operand, depth + 1) in _NUMERIC else None
        if isinstance(expr, S.Binary) and expr.op in ("+", "-", "*", "/", "&"):
            left, right = self.of(expr.left, depth + 1), self.of(expr.right, depth + 1)
            if "STRING" in (left, right) and expr.op in ("+", "&"):
                return "STRING"  # `2000 + Mid(...)` concatenates (round 65)
            if left in _NUMERIC and right in _NUMERIC:
                return "LONG" if left == right == "LONG" and expr.op != "/" else "FLOAT"
        return None


# SNMPVariable: Type keywords a declared variable type may not be given (oracle, CR6/CR1000X).
# A Float variable takes only Float, a String one String or Float, the rest anything but String.
_SNMP_TYPES = {"INTEGER", "COUNTER", "STRING", "TIMETICKS", "OPAQUE", "GAUGE", "FLOAT"}
_SNMP_RE = re.compile(r"\s*SNMPVARIABLE\s*\(\s*(\w+)\s*,\s*[\d.]+\s*,\s*(\w+)", re.I)
_SNMP_ALLOWED = {"FLOAT": {"FLOAT"}, "STRING": {"STRING", "FLOAT"}}


# Operators that reject a String operand (oracle, all four loggers): the logical and shift
# operators with one shared message, `* / MOD` with one message per String side.
_STRING_ILLEGAL_OPS = {"AND", "OR", "XOR", "IMP", "EQV", "<<", ">>"}
_STRING_ARITH_OPS = {"*", "/", "\\", "MOD"}  # `\` is also `INTDV` (round 36)


def _subexpressions(expr: S.Expr | None):
    if expr is None:
        return
    yield expr
    if isinstance(expr, S.Binary):
        yield from _subexpressions(expr.left)
        yield from _subexpressions(expr.right)
    elif isinstance(expr, S.Unary):
        yield from _subexpressions(expr.operand)
    elif isinstance(expr, (S.Index, S.Member)):
        for a in expr.args or []:
            yield from _subexpressions(a)
    elif isinstance(expr, S.ArrayLiteral):
        for a in expr.items:
            yield from _subexpressions(a)


def _statement_expressions(stmt: S.Stmt):
    if isinstance(stmt, S.Assignment):
        yield stmt.value
    elif isinstance(stmt, (S.Instruction, S.Call)):
        yield from stmt.args
    elif isinstance(stmt, (S.If, S.ElseIf)):
        yield stmt.cond
        for inner in (getattr(stmt, "inline", None) or []) + (getattr(stmt, "inline_else", None) or []):
            yield from _statement_expressions(inner)


def _string_typed(typer: _Typer, node: S.Expr | None) -> bool:
    """A String operand, or a logical word whose right operand is one (`X AND "a" AND X`: the
    first `AND` keeps its string operand's type, so the second also reports; `"a" AND X AND X`
    reports once; oracle, all four loggers, 2026-10-12)."""
    while isinstance(node, S.Binary) and node.op in _STRING_ILLEGAL_OPS:
        node = node.right
    return typer.of(node) == "STRING"


def _in_source_order(expr: S.Expr | None):
    """The subexpressions of `expr` with each operator after its left operand and before its
    right one (the order the reference reports String-operand errors in)."""
    if isinstance(expr, S.Binary):
        yield from _in_source_order(expr.left)
        yield expr
        yield from _in_source_order(expr.right)
    elif isinstance(expr, S.Unary):
        yield from _in_source_order(expr.operand)
    elif isinstance(expr, (S.Index, S.Member)):
        for a in expr.args or []:
            yield from _in_source_order(a)
    elif isinstance(expr, S.ArrayLiteral):
        for a in expr.items:
            yield from _in_source_order(a)


def _string_operand_messages(typer: _Typer, stmt: S.Stmt) -> list[str]:
    """The String-operand errors of a statement, one entry per offending operator in source order
    (oracle, all four loggers: `"a" and "b" and "c" .`, 2026-10-10; `(S * 2) + (S AND 1)` and
    `(S * 2) * S`, round 65, docs/instructions.md#string-operands)."""
    found: list[str] = []
    for top in _statement_expressions(stmt):
        for node in _in_source_order(top):
            if not isinstance(node, S.Binary):
                continue
            left, right = _string_typed(typer, node.left), _string_typed(typer, node.right)
            if node.op == "TO":  # a String on the right only (`S To 3` compiles); round 9
                if right:
                    found.append("String is illegal here")
            elif node.op in _STRING_ILLEGAL_OPS and (left or right):
                found.append("String is illegal here")
            elif node.op in _STRING_ARITH_OPS and (left or right):
                found += (["Illegal String operand"] if left else []) + (
                    ["Illegal operand of type STRING"] if right else []
                )
    return found


def _snmp_mismatch(typer: _Typer, name: S.Expr | None, snmp_type: str) -> bool:
    var = typer.variable(name)
    return (
        var is not None
        and snmp_type.upper() in _SNMP_TYPES
        and snmp_type.upper() not in _SNMP_ALLOWED.get(typer.types[var], _SNMP_TYPES - {"STRING"})
    )


def _same_type(dest: str, source: str) -> bool:
    return dest == source or (dest == "STRING" and source == "FLOAT")


def _indexed_past_first(typer: _Typer, dest: S.Expr | None, env: consteval.Env) -> bool:
    """`RoutersNeighbors` takes the whole array: a subscript that is a variable, a binary
    expression or a literal of 2 or more is `Destination variable cannot be indexed` (oracle,
    2026-10-08: `L(1)`, `L(1.5)`, `L(-1)` and `(1,1)` compile, `L(0)` adds an out-of-bounds line
    and is not modelled). A subscript that is a `Const` name, or a parenthesised literal, is not
    probed (the parser drops the parentheses, so `(1)` reads as `1`). Also (oracle, all four
    loggers, 2026-10-12, r15_routers_neighbors_index): a string literal and a
    `Const` name whose value is 2 or more (`Const One=1.5` compiles; `Const Zero=0` adds the
    out-of-bounds line, not modelled)."""
    if not isinstance(dest, S.Index):
        return False
    for a in dest.args:
        if (
            isinstance(a, (S.Binary, S.String))
            or (isinstance(a, S.Name) and typer.variable(a) is not None)
            or (isinstance(a, S.Number) and a.value >= 2)
        ):
            return True
        if isinstance(a, S.Name):
            try:
                value = consteval.value_of(a, env)
            except consteval.NotConstant:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 2:
                return True
    return False


def check_string_operands(program: S.Program, profile, diags: Diagnostics) -> None:
    """The String-operand errors are reported while the statement is parsed: pass 1, whatever came
    before and after, on all four loggers (oracle, 2026-10-19, round 21: `Abs(1,2) : X = "a" * 2`
    and `X = "a" * 2 : Abs(1,2)` both report both; `"a" * 2 : Nope + 1` reports only the String
    error on CR6/CR1000X, the String error then the name on CR300/CR350)."""
    typer = _Typer(program)
    for stmt in program.statements:
        for text in _string_operand_messages(typer, stmt):
            diags.error(stmt.line, text, stmt.file)


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    typer = _Typer(program)
    tables = symbols.scope_tables(program, env)
    in_prog = False
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "BEGINPROG":
            in_prog = True
        if isinstance(stmt, S.Unparsed):  # a dotted OID is not an expression
            m = _SNMP_RE.match(stmt.text)
            if (
                m
                and "SNMPVARIABLE" in profile.instructions
                and _snmp_mismatch(typer, S.Name(stmt.line, name=m.group(1)), m.group(2))
            ):
                diags.error(stmt.line, "Miss match of types", stmt.file)
            continue
        if not isinstance(stmt, S.Instruction):
            continue
        kw, args = stmt.keyword.upper(), stmt.args
        if kw not in profile.instructions:
            continue
        message = None
        extra = None
        if kw == "ROUTERSNEIGHBORS" and args:
            kind = "STRING" if isinstance(args[0], S.String) else typer.of(args[0])
            if typer.variable(args[0]) is not None or isinstance(args[0], S.String):
                if kind != "LONG":
                    message = "Destination variable must be type LONG"
                elif _indexed_past_first(typer, args[0], env):
                    message = "Destination variable cannot be indexed"
        elif kw == "INSTRUCTIONTIMES" and args and typer.variable(args[0]) is not None:
            # (oracle, all four loggers, 2026-10-12: a Float/String/Boolean destination anywhere;
            # a Long one is only accepted before `BeginProg`)
            kind = typer.of(args[0])
            if kind is not None and kind != "LONG":
                message = "Destination variable must be type LONG"
            elif kind == "LONG" and in_prog:
                message = "TimesOfInstructions() must be declared before BeginProg."
        elif kw == "ARGOSERROR" and args:
            if (
                typer.variable(args[0]) is not None
                and typer.of(args[0]) not in (None, "STRING")
                or isinstance(args[0], (S.String, S.Number, S.Binary, S.Unary))
            ):
                # (also a literal or an expression: `ArgosError("fuzz")`, `ArgosError((1+2))`,
                # oracle CR6/CR1000X 2026-10-12)
                message = "A string variable destination is required"
        elif kw == "SORTSPAINDEXED" and len(args) > 2:
            dest, source = typer.of(args[0]), typer.of(args[2])
            if typer.variable(args[0]) is not None and dest and source and not _same_type(dest, source):
                message = "Source and Dest types must be the same."
        elif kw == "SAMPLE" and len(args) > 2 and isinstance(args[2], S.Name) and args[2].name.upper() == "NSEC":
            source = typer.of(args[1])
            if source in ("STRING", "BOOLEAN", "DOUBLE"):
                message = "Source type must be either LONG or FLOAT or NSEC for data type NSEC"
            elif source == "FLOAT" and not any((d.line, d.file) == (stmt.line, stmt.file) for d in diags.errors):
                size = _array_size(typer, tables(stmt).dims, args[1])
                try:
                    reps = consteval.value_of(args[0], env)
                except consteval.NotConstant:
                    reps = None
                if size is not None and isinstance(reps, int) and reps >= 1 and size < 7 * reps:
                    message = "Float to NSEC expects source array size >= 7"
        elif kw in ("SDMCD16MASK", "SDMCD16AC") and args:
            if kw == "SDMCD16MASK" and any(typer.types.get(typer.variable(a) or "", "") == "BOOLEAN" for a in args[:2]):
                message = "Source and Mask type must be LONG."
            if isinstance(args[0], S.Number) or (isinstance(args[0], S.Name) and args[0].name.upper() in typer.consts):
                extra = "Invalid Source."
        elif (
            kw == "SNMPVARIABLE"
            and len(args) > 2
            and isinstance(args[2], S.Name)
            and _snmp_mismatch(typer, args[0], args[2].name)
        ):
            message = "Miss match of types"
        elif (
            kw == "MENURECOMPILE"
            and len(args) > 1
            and isinstance(args[1], (S.Name, S.Index))
            and typer.of(args[1]) == "STRING"
        ):
            message = "String type variable not allowed."
        elif (
            kw == "SDMGENERIC"
            and len(args) > 6
            and typer.of(args[0]) == typer.of(args[4]) == "STRING"
            and consteval.is_constant(args[6], env)
        ):
            try:
                bytes_per_value = consteval.value_of(args[6], env)
            except consteval.NotConstant:
                bytes_per_value = None
            if bytes_per_value != 1:
                message = "If source and destination are of type STRING, bytes per value must be 1"
        for text in (message, extra):
            if text:
                diags.error(stmt.line, text, stmt.file)


def _array_size(typer: _Typer, dims: dict[str, tuple[int, ...]], expr: S.Expr | None) -> int | None:
    """Element count of the variable behind `expr`; 1 for an expression that is no variable."""
    if isinstance(expr, (S.Name, S.Index)) and (variable := typer.variable(expr)) is not None:
        shape = dims.get(variable)
        return None if shape is None else math.prod(shape)
    return 1


_CTYPE_NAMES = frozenset({"LONG", "FLOAT", "DOUBLE", "STRING"})


def _ctype_illegal(node: S.Index, env, legal: frozenset, known: set | None) -> bool:
    """`CType(x)` (no type), or a type that is neither `Long`/`Float`/`Double`/`String` nor a
    constant equal to the logger's `UINT1`/`IEEE4`/`IEEE8` code: `Illegal type`, a pass-1 finding
    (oracle, all four loggers, round 41: `Boolean`, `FP2`, `2`, `1.5`, a variable, a string or an
    undefined name; with `known`, CR6/CR1000X, also an undefined first argument)."""
    args = node.args
    if None in args:
        return False
    if len(args) == 1:
        return True
    if len(args) != 2:
        return False
    kind = args[1]
    from . import expressions

    if known is not None and expressions._undefined_names(args[0], known):
        return True
    if isinstance(kind, S.Name) and kind.name.upper() in _CTYPE_NAMES:
        return False
    try:
        value = consteval.value_of(kind, env)
    except consteval.NotConstant:
        return True
    return isinstance(value, (str, bool)) or value != int(value) or int(value) not in legal


def check_ctype(program: S.Program, profile, diags: Diagnostics) -> None:
    if "CTYPE" not in profile.functions:
        return
    env = consteval.Env(profile, dict(program.consts))
    legal = frozenset(v for n in ("UINT1", "IEEE4", "IEEE8") if isinstance(v := _symbol_value(env, n), int))
    from . import expressions

    known = None if profile.single_pass else expressions._known_names(program, profile)
    for stmt in program.statements:
        for top in _statement_expressions(stmt):
            for node in _subexpressions(top):
                if (
                    isinstance(node, S.Index)
                    and node.name.upper() == "CTYPE"
                    and _ctype_illegal(node, env, legal, known)
                ):
                    diags.error(stmt.line, "Illegal type", stmt.file)


def _symbol_value(env, name: str):
    try:
        return consteval.value_of(S.Name(0, name=name), env)
    except consteval.NotConstant:
        return None


# Instructions whose arguments are plain expressions: the probed ones. `Scan`, `CallTable`,
# `Select`/`Case` and the table declarations report other errors for the same argument.
_POINTER_ARG_INSTRUCTIONS = frozenset({"DELAY", "MOVE", "PORTSET", "RANDOMIZE", "WHILE"})

POINTER_MESSAGE = "{name} must be declared as a pointer type, e.e., AS Float!, Long!, Double!, String!,..."


def _dereference_names(top: S.Expr | None):
    """Pre-order `!operand` nodes of an expression: the operand's name, or None when the operand is
    not a plain name / array element."""
    for node in _subexpressions(top):
        if isinstance(node, S.Unary) and node.op == "!":
            operand = node.operand
            yield operand.name if isinstance(operand, (S.Name, S.Index)) else None


def _routine_scopes(program: S.Program):
    """`stmt id -> {UPPER name: (declared name, type)}` for the variables visible at a statement:
    the globals, then the statement's own routine's locals and parameters (a local shadows)."""

    def declarations(stmts):
        out: dict[str, tuple[str, str]] = {}
        clash: set[str] = set()
        for st in stmts:
            if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
                for v in st.vars:
                    kind = "FLOAT" if v.paren_dim else (v.type or "FLOAT").upper()
                    key = v.name.upper()
                    if out.setdefault(key, (v.name, kind))[1] != kind:
                        clash.add(key)
        return {k: v for k, v in out.items() if k not in clash}

    groups: list[tuple[S.SubDef | None, list]] = [(None, [])]
    for st in program.statements:
        if isinstance(st, S.SubDef):
            groups.append((st, []))
        groups[-1][1].append(st)
        if S.ends_routine(st):
            groups.append((None, []))
    glob = declarations(st for routine, stmts in groups if routine is None for st in stmts)
    scopes: dict[int, dict] = {}
    for routine, stmts in groups:
        if routine is None:
            scope = glob
        else:
            scope = dict(glob)
            scope.update(declarations(stmts))
            for prm in routine.params:
                scope[prm.name.upper()] = (prm.name, (prm.type or "FLOAT").upper())
            if routine.keyword.upper() == "FUNCTION":
                scope.setdefault(routine.name.upper(), (routine.name, ""))
        for st in stmts:
            scopes[id(st)] = scope
    return scopes


def check_pointer_deref(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X: `!name` on a declared variable that is not of a pointer type (`As Long!`).
    Rules and oracle evidence: `docs/checker.md` "Round 14: `!name` on a non-pointer variable"."""
    if profile.single_pass:
        return
    from . import expressions, structure

    scopes = _routine_scopes(program)

    def offender(scope, tops):
        """Declared name of the first `!` operand without a pointer type, or None."""
        for top in tops:
            for operand in _dereference_names(top):
                entry = scope.get(operand.upper()) if operand else None
                if entry is None or not entry[1]:
                    return None
                if not entry[1].endswith("!"):
                    return entry[0]
        return None

    known = expressions._known_names(program, profile)
    for stmt in program.statements:
        scope = scopes[id(stmt)]
        derefs = [
            n if n and re.search(rf"!\s*{re.escape(n)}\b", stmt.text or "", re.I) else None
            for top in expressions._stmt_exprs(stmt)
            for n in _dereference_names(top)
        ]
        if any(n and n.upper() not in scope and n.upper() not in known for n in derefs):
            # `!` on an undeclared name, once per statement; an assignment to a non-pointer whose
            # first `!` operand is that name also gets the pointer message (oracle, CR6/CR1000X,
            # round 59, docs/checker.md#dereferencing-an-undeclared-name-round-59)
            diags.error(stmt.line, "Illegal ! operation", stmt.file)
            entry = (
                scope.get(stmt.target.name.upper())
                if isinstance(stmt, S.Assignment) and isinstance(stmt.target, (S.Name, S.Index))
                else None
            )
            if (
                entry
                and entry[1]
                and not entry[1].endswith("!")
                and derefs[0]
                and derefs[0].upper() not in scope
                and derefs[0].upper() not in known
            ):
                diags.error(stmt.line, POINTER_MESSAGE.format(name=entry[0]), stmt.file)
                diags.error(stmt.line, structure._out_of_place(profile, stmt.text, stmt.at_eol), stmt.file)
            continue
        name = echo = None
        if isinstance(stmt, S.Assignment):
            target = stmt.target
            base = target.operand if isinstance(target, S.Unary) and target.op == "!" else target
            if not isinstance(base, (S.Name, S.Index)) or base.name.upper() not in scope:
                continue
            name = offender(scope, [target, stmt.value])
            echo = structure._out_of_place(profile, stmt.text, stmt.at_eol)
        elif isinstance(stmt, S.Unparsed):
            if (
                (m := re.fullmatch(r"\s*!\s*(\w+)(?:\s+\w+)?\s*", stmt.text or ""))
                and (entry := scope.get(m.group(1).upper()))
                and entry[1]
                and not entry[1].endswith("!")
            ):
                name, echo = entry[0], structure._out_of_place(profile, stmt.text, stmt.at_eol)
        elif isinstance(stmt, (S.If, S.ElseIf)):
            text = expressions._if_condition_text(stmt)
            if text is None:
                continue
            name = offender(scope, [stmt.cond])
            echo = f"Invalid, or out of place expression: {text}."
        elif isinstance(stmt, S.Call) or (
            isinstance(stmt, S.Instruction) and stmt.keyword.upper() in _POINTER_ARG_INSTRUCTIONS
        ):
            for i, arg in enumerate(stmt.args):
                if (found := offender(scope, [arg])) is not None:
                    name = found
                    echo = (
                        "Invalid, or out of place expression: "
                        + (
                            expressions._arg_text(stmt, i)
                            if isinstance(stmt, S.Instruction)
                            else expressions._call_arg_text(stmt.text, i)
                        )
                        + "."
                    )
                    break
        if name is not None and echo is not None and re.search(rf"!\s*{re.escape(name)}\b", stmt.text or "", re.I):
            diags.error(stmt.line, POINTER_MESSAGE.format(name=name), stmt.file)
            diags.error(stmt.line, echo, stmt.file)
