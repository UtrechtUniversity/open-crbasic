"""Sequential / Pipeline mode decision (CR6, CR1000X). Rules: docs/modes.md.

The trigger lists themselves are per-logger data (`data/<logger>/mode.json`, `Profile.mode_rules`
-- `docs/loggers.md`), not module constants: CR1000X's compiled mode-decision function
(`docs/loggers.md`'s Ghidra evidence) is structurally identical to CR6's but three
`task_instructions` names and two `nonconstant_arg` names are absent because those instructions
(`ExciteI`, `VibratingWire`, and -- `task_instructions` only -- `Resistance`) do not exist on
CR1000X at all, so the two loggers' *observable* behaviour is identical: a name missing from a
logger's list can never occur as a real instruction in a program that logger accepts anyway.
CR300/CR350 never print a mode line (`Profile.reports_mode`), so their `mode.json` is an unverified
copy of CR6's, kept only so every profile has one; `mode.decide`'s result for them is computed but
discarded by `compiler.py`.
"""

from collections.abc import Callable

from . import syntax as S
from .profiles import Profile


def _const_value(expr, value_of: Callable) -> float | None:
    try:
        return value_of(expr)
    except Exception:
        return None


def _numeric_constant(expr, is_constant: Callable, value_of: Callable) -> bool:
    """A `nonconstant_arg` argument that keeps PipelineMode: numbers and numeric `Const` names
    joined by operators. A string value or a function call counts as variable (docs/modes.md)."""
    if isinstance(expr, S.Number):
        return True
    if isinstance(expr, S.Name):
        return is_constant(expr) and not isinstance(_const_value(expr, value_of), str)
    if isinstance(expr, S.Unary):
        return _numeric_constant(expr.operand, is_constant, value_of)
    if isinstance(expr, S.Binary):
        return _numeric_constant(expr.left, is_constant, value_of) and _numeric_constant(
            expr.right, is_constant, value_of
        )
    return False


def decide(statements: list[S.Stmt], profile: Profile, is_constant: Callable, value_of: Callable) -> str:
    """Return "Sequential" or "Pipeline".

    `is_constant(expr)` tells whether an argument is a compile-time constant; `value_of(expr)`
    returns its numeric value (raises when it has none). Trigger lists come from
    `profile.mode_rules` (`data/<logger>/mode.json`).
    """
    rules = profile.mode_rules
    always_sequential = set(rules["always_sequential"])
    task_instructions = set(rules["task_instructions"])
    nonconstant_arg = {k: tuple(v) for k, v in rules["nonconstant_arg"].items()}
    variable_arg = dict(rules["variable_arg"])

    sequential = False
    pipeline_forced = False
    depth = 0  # open If/Select/For/While/Do since the last Scan
    in_scan = 0  # Scan nesting (Scan ... NextScan)
    in_slow = 0  # SlowSequence count

    def visit(stmt: S.Stmt) -> None:
        nonlocal sequential, pipeline_forced, depth, in_scan, in_slow
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        if kw == "SEQUENTIALMODE":
            sequential = True
        elif kw == "PIPELINEMODE":
            pipeline_forced = True
        if not sequential and not pipeline_forced:
            if kw in always_sequential:
                sequential = True
            elif kw in task_instructions and (depth or not in_scan):
                if kw == "SW12":
                    sequential = _const_value(_arg(args, 2), value_of) == 1
                elif kw == "DELAY":
                    sequential = _const_value(_arg(args, 0), value_of) in (0, 2)
                elif kw in ("SERIALOUTBLOCK", "SERIALINRECORD"):
                    sequential = not in_slow or depth > 0
                else:
                    sequential = True
            elif kw in nonconstant_arg:
                sequential = any(
                    _arg(args, i) is not None and not _numeric_constant(_arg(args, i), is_constant, value_of)
                    for i in nonconstant_arg[kw]
                )
            elif kw in ("FOR", "IF", "SELECT", "WHILE", "DO"):
                depth += 1
            elif kw in ("NEXT", "ENDIF", "END", "WEND", "LOOP"):
                target = _block_word(args)
                if kw == "NEXT" and target == "SCAN":
                    in_scan -= 1
                elif kw == "NEXT" and target == "SUBSCAN":
                    pass
                elif kw == "END" and target not in ("IF", "SELECT"):
                    pass
                else:
                    depth -= 1
        if kw == "SLOWSEQUENCE":
            in_slow += 1
        elif kw == "SCAN":
            depth = 0
            in_scan += 1
        elif kw == "NEXTSCAN":
            in_scan -= 1
        if kw in variable_arg and not pipeline_forced:
            arg = _arg(args, variable_arg[kw])
            if arg is None or not is_constant(arg):
                sequential = True
        # single-line If: the inline statements are inside a conditional
        if isinstance(stmt, S.If) and stmt.inline is not None:
            for inner in (stmt.inline or []) + (stmt.inline_else or []):
                visit(inner)
            if not sequential and not pipeline_forced:
                depth -= 1

    for stmt in statements:
        visit(stmt)
    return "Sequential" if sequential else "Pipeline"


def _keyword(stmt: S.Stmt) -> str:
    if isinstance(stmt, S.Instruction):
        return stmt.keyword.upper()
    if isinstance(stmt, S.If):
        return "IF"
    if isinstance(stmt, S.For):
        return "FOR"
    if isinstance(stmt, S.DoLoop):
        return stmt.keyword.upper()
    return type(stmt).__name__.upper()


def _arg(args: list, i: int):
    return args[i] if i < len(args) else None


def _block_word(args: list) -> str | None:
    """`Next Scan`, `End If`: the word after a two-word block terminator."""
    if args and isinstance(args[0], S.Name):
        return args[0].name.upper()
    return None
