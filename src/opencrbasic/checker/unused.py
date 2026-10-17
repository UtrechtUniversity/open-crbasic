"""`-u`: `%s declared but not used.` (`docs/cli.md` "-u").

Oracle-confirmed this session (`tools/tdf_probe.py <snippet> --extra=-u`, real `CR6Comp.exe` under
Wine, cross-checked against `cr1000xcomp.exe`/`cr300comp.exe`/`cr350comp.exe` on the same snippet --
all four loggers print the identical messages in the identical format, so this module has no
per-logger branching):

- Flags a `Public`/`Dim` variable (the whole array/`String`, never a single element -- any
  reference to *any* element marks the whole declaration used) and a `Sub`/`Function` parameter
  that is never referenced anywhere else in the program text, each as `line N: Warning: %s declared
  but not used.`.
- Also flags a `Sub` **or** `Function` that is never referenced anywhere else in the program, as a
  *bare* `Warning: %s declared but not used.` -- no `line N:` prefix, the same "no line" shape as
  the pre-existing "table declared but never called" warning (`checker/warnings.py`). An initial
  reading of one oracle probe (an uncalled `Function` whose body sets its return value the usual
  way, `MyFunc = value`) looked like an uncalled `Function` is *never* flagged, unlike an uncalled
  `Sub` -- corrected once the real corpus (`ShuntLib.crb`'s `SampleShunt`, uncalled in
  `Main.CR6`/`PowerBudget_tester.CR6`/`Preflight_tester.CR6` because its only call site sits behind
  a false `#If`) turned out to *be* flagged: `MyFunc = value` inside the function's own body is
  itself a reference to `MyFunc` (an ordinary `Assignment.target`, walked like any other), which is
  what suppressed the warning in the first probe, not the `Function`/`Sub` distinction; a second
  probe with `Return value` instead of self-assignment confirmed an uncalled `Function` *is*
  flagged identically to an uncalled `Sub` once nothing else references its name.
- **Not** flagged: `Const`/`ConstTable` fields and an `Alias` name. Aliasing merges the alias name
  and the underlying declared variable into one usage identity: a reference to the alias name
  elsewhere in the program counts as using the original variable (oracle: `Public X` / `Alias X =
  Y` / `Y = 1` -- `X` is *not* flagged), but the `Alias`/`Units`/`ReadOnly` statement's own mention
  of a name is never itself a use (oracle: `Public X` / `Alias X = Y` alone, `Y` never referenced
  again -- `X` *is* flagged; likewise a bare `Units X = Label` or `ReadOnly X` with no other
  reference to `X` still flags it). A variable referenced only as a `DataTable` output source (a
  `Sample`/`Average`/... argument) counts as used.
- The whole check is suppressed by *any* compile error anywhere in the file, not just the ones
  after a given declaration (oracle: a single unrelated error anywhere removes every `-u` warning).
  `compiler.compile_file` only calls `check` after the ordinary checker has run and found no
  errors; this module does not re-check that itself.

Scope/limits: this project's syntax tree keeps block structure flat (`syntax.py`'s own docstring)
and does not model lexical scope -- like `checker/symbols.py`'s `declared_names`, a name here is
"used" if referenced *anywhere* in the program, including inside a different `Sub`/`Function` than
the one that declares it. Not independently oracle-tested against real per-scope shadowing (not
observed in this project's corpus). `Static` declarations were not investigated (rejected as
invalid syntax by the CR6 grammar in every oracle probe tried, and unused in `crbasic/`).
"""

from .. import syntax as S
from ..diagnostics import Diagnostics

# Instructions that mention a variable's name as metadata, not as a real reference (oracle-
# confirmed for all three: `Alias X = Y`, `Units X = Label`, `ReadOnly X` never count as "using"
# `X` on their own).
_METADATA_ONLY = frozenset({"ALIAS", "UNITS", "READONLY"})


def _base_name(expr: S.Expr | None) -> str | None:
    """The identifier a bare `Name` or single-element `Index` (`Arr(3)`) expression names, for
    resolving an `Alias` statement's own operands -- `None` for anything else (a swath/complex
    alias target, `docs/tdf-rules.md` §11 open question, is left unmerged rather than guessed at).
    """
    if isinstance(expr, S.Name):
        return expr.name
    if isinstance(expr, S.Index):
        return expr.name
    return None


def _walk_expr(expr: S.Expr | None, names: set[str]) -> None:
    if expr is None:
        return
    if isinstance(expr, S.Name):
        names.add(expr.name.upper())
    elif isinstance(expr, S.Index):
        names.add(expr.name.upper())
        for a in expr.args:
            _walk_expr(a, names)
    elif isinstance(expr, S.Member):
        _walk_expr(expr.base, names)
        for a in expr.args or ():
            _walk_expr(a, names)
    elif isinstance(expr, S.Unary):
        _walk_expr(expr.operand, names)
    elif isinstance(expr, S.Binary):
        _walk_expr(expr.left, names)
        _walk_expr(expr.right, names)
    elif isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            _walk_expr(item, names)
    # Number/String: no name to collect.


def _walk_stmt(stmt: S.Stmt, names: set[str]) -> None:
    """Every name a statement *references* (not declares) -- feeds the "used anywhere" set that
    every declared Public/Dim/param/Sub name is checked against. `Declaration`'s own `VarDecl.name`
    and `SubDef`'s own `name`/`Param.name` are never walked here (they are declarations, callers
    collect those separately); a `VarDecl`'s `dims`/`init`/`string_size` and a `Param`'s `default`/
    `string_size` genuinely can reference some *other* variable, so those are walked.
    """
    if isinstance(stmt, S.Declaration):
        for v in stmt.vars:
            for d in v.dims:
                _walk_expr(d, names)
            for e in v.init or ():
                _walk_expr(e, names)
            _walk_expr(v.string_size, names)
    elif isinstance(stmt, S.ConstDecl):
        _walk_expr(stmt.value, names)
    elif isinstance(stmt, S.Assignment):
        _walk_expr(stmt.target, names)
        _walk_expr(stmt.value, names)
    elif isinstance(stmt, S.Instruction):
        if stmt.keyword.upper() in _METADATA_ONLY:
            return
        for a in stmt.args:
            _walk_expr(a, names)
    elif isinstance(stmt, S.If):
        _walk_expr(stmt.cond, names)
        for s2 in stmt.inline or ():
            _walk_stmt(s2, names)
        for s2 in stmt.inline_else or ():
            _walk_stmt(s2, names)
    elif isinstance(stmt, S.ElseIf):
        _walk_expr(stmt.cond, names)
    elif isinstance(stmt, S.For):
        _walk_expr(stmt.var, names)
        _walk_expr(stmt.start, names)
        _walk_expr(stmt.end, names)
        _walk_expr(stmt.step, names)
    elif isinstance(stmt, S.DoLoop):
        _walk_expr(stmt.cond, names)
    elif isinstance(stmt, S.Case):
        for item in stmt.items:
            for part in item:
                if isinstance(part, S.Expr):
                    _walk_expr(part, names)
    elif isinstance(stmt, S.Call):
        names.add(stmt.name.upper())  # `Call MySub(...)` / bare `MySub(...)`: a real call.
        for a in stmt.args:
            _walk_expr(a, names)
    elif isinstance(stmt, S.SubDef):
        for p in stmt.params:
            _walk_expr(p.default, names)
            _walk_expr(p.string_size, names)
    # If/ElseIf/For/DoLoop/Case/Label/Unparsed themselves: nothing else to collect.


def check(program: S.Program, diags: Diagnostics) -> None:
    """Add every `-u` warning for `program` to `diags`. Caller's responsibility (`compiler.py`):
    only call this when `not diags.errors` (oracle-confirmed gate, see this module's docstring).
    """
    variables: list[tuple[str, int, str, str]] = []   # (NAME upper, line, display, file)
    params: list[tuple[str, int, str, str]] = []
    subs: list[tuple[str, str, str]] = []              # (NAME upper, display, file)
    alias_map: dict[str, str] = {}                     # ALIAS-TARGET upper -> ORIGINAL-BASE upper

    for stmt in program.statements:
        if isinstance(stmt, S.Declaration) and stmt.kind.upper() in ("PUBLIC", "DIM"):
            for v in stmt.vars:
                variables.append((v.name.upper(), stmt.line, v.name, stmt.file))
        elif isinstance(stmt, S.SubDef):
            for p in stmt.params:
                params.append((p.name.upper(), stmt.line, p.name, stmt.file))
            subs.append((stmt.name.upper(), stmt.name, stmt.file))
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS" and stmt.args:
            original = _base_name(stmt.args[0])
            if original:
                for target in stmt.args[1:]:
                    name = _base_name(target)
                    if name:
                        alias_map[name.upper()] = original.upper()

    used: set[str] = set()
    for stmt in program.statements:
        _walk_stmt(stmt, used)
    for name in list(used):
        mapped = alias_map.get(name)
        if mapped:
            used.add(mapped)

    for upper, line, display, file in variables:
        if upper not in used:
            diags.warning(line, f"{display} declared but not used.", file)
    for upper, line, display, file in params:
        if upper not in used:
            diags.warning(line, f"{display} declared but not used.", file)
    for upper, display, file in subs:
        if upper not in used:
            diags.warning(0, f"{display} declared but not used.", file)
