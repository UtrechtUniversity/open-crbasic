"""Sequential / Pipeline mode decision (CR6, CR1000X). Rules: docs/modes.md."""

from __future__ import annotations

from typing import Callable

from . import syntax as S

ALWAYS_SEQUENTIAL = {"SDMCAN", "SDMCD16AC", "SDMAO4", "SDMAO4A", "SDMCVO4", "SDMIO16", "WRITEIO",
                     "TDR100", "TDR200"}

# Measurement-task instructions that force SequentialMode when they occur inside a conditional
# or loop, or outside any Scan (e.g. in a Sub).
TASK_INSTRUCTIONS = {
    "VOLTSE", "VOLTDIFF", "BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL6W", "BRFULL", "PANELTEMP", "TCSE",
    "TCDIFF", "EXCITEV", "BATTERY", "PERIODAVG", "CS616", "SETDAC", "AM25T", "CALIBRATE", "PORTGET",
    "PORTSET", "SDMINT8", "SDMTRIGGER", "SW12", "CS7500", "CSAT3", "SDMSW8A", "DELAY", "THERM107",
    "LI7200", "LI7700", "THERM108", "THERM109", "PULSEPORT", "SDMX50", "VIBRATINGWIRE", "TGA", "EC100",
    "EXCITEI", "RESISTANCE", "PULSECOUNT", "MUXSELECT", "SERIALINRECORD", "SERIALOUTBLOCK",
    "CDM_VOLTSE", "CDM_VOLTDIFF", "CDM_BRHALF", "CDM_BRHALF3W", "CDM_MUXSELECT", "CDM_PULSEPORT",
    "CDM_BRHALF4W", "CDM_BRFULL6W", "CDM_BRFULL", "CDM_PANELTEMP", "CDM_TCSE", "CDM_TCDIFF",
    "CDM_EXCITEV", "CDM_BATTERY", "CDM_PERIODAVG", "CDM_SW12", "CDM_SW5", "CDM_THERM107",
    "CDM_THERM108", "CDM_THERM109", "CDM_CURRENTDIFF", "CDM_SWPOWER",
}

# Instruction -> 0-based argument(s) that force SequentialMode when not constant.
NONCONSTANT_ARG = {
    "EXCITEV": (1,), "EXCITEI": (1,), "PORTSET": (1,), "MUXSELECT": (3,), "VIBRATINGWIRE": (3, 4),
    "CDM_EXCITEV": (3,), "CDM_EXCITEI": (3,), "CDM_SW12": (3,), "CDM_SW5": (3,),
    "CDM_MUXSELECT": (5,), "CDM_SWPOWER": (2,),
}

# Instruction -> 0-based argument that forces SequentialMode anywhere when it is a variable.
VARIABLE_ARG = {"CDM_SW12": 4, "CDM_SW5": 4, "CDM_SWPOWER": 3, "SW12": 2}


def _const_value(expr, value_of: Callable) -> float | None:
    try:
        return value_of(expr)
    except Exception:
        return None


def decide(statements: list[S.Stmt], is_constant: Callable, value_of: Callable) -> str:
    """Return "Sequential" or "Pipeline".

    `is_constant(expr)` tells whether an argument is a compile-time constant; `value_of(expr)`
    returns its numeric value (raises when it has none).
    """
    sequential = False
    pipeline_forced = False
    depth = 0           # open If/Select/For/While/Do since the last Scan
    in_scan = 0         # Scan nesting (Scan ... NextScan)
    in_slow = 0         # SlowSequence count

    def visit(stmt: S.Stmt) -> None:
        nonlocal sequential, pipeline_forced, depth, in_scan, in_slow
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        if kw == "SEQUENTIALMODE":
            sequential = True
        elif kw == "PIPELINEMODE":
            pipeline_forced = True
        if not sequential and not pipeline_forced:
            if kw in ALWAYS_SEQUENTIAL:
                sequential = True
            elif kw in TASK_INSTRUCTIONS and (depth or not in_scan):
                if kw == "SW12":
                    sequential = _const_value(_arg(args, 2), value_of) == 1
                elif kw == "DELAY":
                    sequential = _const_value(_arg(args, 0), value_of) in (0, 2)
                elif kw in ("SERIALOUTBLOCK", "SERIALINRECORD"):
                    sequential = not in_slow
                else:
                    sequential = True
            elif kw in NONCONSTANT_ARG:
                sequential = any(_arg(args, i) is not None and not is_constant(_arg(args, i))
                                 for i in NONCONSTANT_ARG[kw])
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
        if kw in VARIABLE_ARG and not pipeline_forced:
            arg = _arg(args, VARIABLE_ARG[kw])
            if arg is not None and not is_constant(arg):
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
