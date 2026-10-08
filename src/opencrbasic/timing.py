"""`-t`: the `[Times]` section CR6Comp writes to the message file -- one `line N: <label>` per
timed statement (`docs/cli.md`).

Coverage is intentionally narrow, by evidence, not by guesswork: oracle testing (16 repository
programs, a 100-file random sample of `tests/corpus/chm/`, plus targeted scratch probes to
isolate each instruction's own argument dependence) found only a handful of instructions ever
produce a `[Times]` entry at all -- most (assignments, `If`, `Battery`, `Serial*`, `Call`, ...)
contribute nothing, confirmed absent from every sample despite heavy use. Implemented, each
oracle-verified on every call shape actually observed in those two samples:

- A top-level `Scan(...)` statement (one that does not immediately follow a bare `SlowSequence`)
  opens a timed span, prints `Scan: N`, and closes at its matching `NextScan`; `N` is a 0-based
  count of such spans seen so far. Despite the label this is **not** a time value -- confirmed
  with `sdmsio4_1.CR6`'s two sequential top-level `Scan` blocks (`Scan: 0`, then later `Scan: 1`).
  A `SlowSequence`'s own `Scan(...)` does not open a span at all (confirmed with `Main.CR6`'s
  three `SlowSequence`s, none of which contribute anything to `-t`'s output, including the
  `Delay`s inside them -- though those happen to use `Sec`, not the one implemented `Delay`
  unit, so that second part is inferred, not independently confirmed). Nothing outside any span
  is timed either, which is also why a call inside a `Sub`/`Function` never appears (`PT1000_
  tester.CR6`'s `Sub RunPanelCompare`, defined and only ever called before/outside `BeginProg`'s
  own `Scan`, contributes nothing).
- Inside an open span, `PanelTemp(Dest, Timeout)`: `PanelTemp = (10 + round(10000 / Timeout)) *
  100`, printed **twice** per call (both prints identical). Fitted from seven `Timeout` values
  (60, 100, 200, 1000, 5000, 15000, 30000 -- a scratch probe, not corpus text) and confirmed exact
  on all seven; every repository and sampled-corpus call uses `Timeout=15000` (giving `1100`),
  which is also where this was first noticed.
- Inside an open span, `PortSet(Port, State)` (exactly 2 arguments): always `= 100`, confirmed
  with both a high and a low state argument. A 3-argument `PortSet(Port, State, X)` (`spiwrite_1.
  CR6`) produces no entry at all, so this is gated on the argument count rather than guessed to
  extend to it.
- Inside an open span, `Sw12`: always `= 100`, confirmed with one repository call.
- Inside an open span, `Delay(RepDelay, Value, Units)` with `Units` spelled exactly `mSec`:
  `Delay = Value*1000 + 100` (confirmed against two `Value`s, 100 and 150, both microsecond-
  converted plus a fixed 100us overhead). The match is deliberately case-sensitive -- the one
  available negative sample, `sdmsio4_1.CR6`'s `Delay (1,10,MSEC)` (all upper-case), produces no
  entry at all despite an otherwise-identical shape, suggestive of a literal string compare in the
  exe rather than CRBasic's usual case-insensitive symbol lookup; this is one negative sample, so
  it is a documented uncertainty, not a confirmed rule. `Units` of `Sec`/`uSec`/`Min` is not
  exercised by either sample and not implemented.
- Inside an open span, `TCDiff(..., Reps, ..., SettlingTime, ...)` with `Reps = 1` and
  `SettlingTime = 15000` (the only shape either sample exercises): `TCDiff = 2900`. Any other
  `Reps`/`SettlingTime` is not implemented (see below -- the one additional data point available,
  `VoltSe`'s own `Reps` dependence, rules out a simple linear-in-`Reps` guess, so a fixed constant
  is not extended past the one confirmed shape).

A statement's own file does not matter, only whether it is textually inside an open span: a bare
`Include(...)` spliced into a `Scan(...)` body is timed exactly like native code (a repository-
style scratch fixture, `tests/fixtures/cli/sample.CR6`/`sample_inc.crb` -- the included file's own
`PortSet` call is timed, at *its own* line number within `sample_inc.crb`). This appears to be
different for `IncludeSection`, whose pulled-in content is oracle-confirmed absent from `Main.
CR6`'s own `-t` output (`Main.CR6`'s included `*Lib.crb` files each have their own `Delay`/... );
the simplest explanation consistent with both is that `IncludeSection`'s own pulled-in content
there is declarations (`Const`/`DataTable`/...), never itself inside a `Scan`/`NextScan` span, so
no separate `IncludeSection`-specific rule was needed or is implemented -- the span check alone
explains every sample. `-t`'s line-number format has no room for a file name (unlike an ordinary
diagnostic's `<file> line N: ...`); a real ambiguity this leaves unresolved, not exercised by
either sample: two different files each defining line N inside the same open span.

Investigated, not implemented: `VoltSe`'s cost also varies with its `Reps` argument (1500, 3000,
4400, 5800, 11500 for `Reps` 1/2/3/4/8, `SettlingTime=15000` fixed throughout) but is not a fixed
per-`Reps` increment (successive deltas 1500/1400/1400/~1425), so a formula was not fitted with
confidence; guessing one risks a confident wrong answer more than it's worth for an instruction
absent from this project's own programs. No other instruction was found to produce a `[Times]`
entry in either sample. The `-m`/`-u` flags were investigated too (`docs/cli.md`) but neither
alters `[Times]`.
"""

from __future__ import annotations

from . import consteval, mode
from . import syntax as S
from .profiles import Profile


def _const(expr: S.Expr | None, env: consteval.Env) -> object | None:
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None


def _num(v: object | None) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _round_half_up(x: float) -> int:
    # C's usual `(int)(x + 0.5)` for a non-negative `x` -- avoids Python's round-half-to-even,
    # which would silently disagree with the exe at an exact `.5` (docs/cli.md "-t").
    return int(x + 0.5)


def _flatten(stmts):
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, S.If):
            yield from _flatten(stmt.inline or [])
            yield from _flatten(stmt.inline_else or [])


def times_lines(program: S.Program, profile: Profile, main_file: str) -> list[str]:
    """The `[Times]` section's entries (without the `[Times]` header itself), in program order.

    `main_file` is accepted for backwards compatibility but no longer used to filter statements
    (see the module docstring's last paragraph); kept so callers do not need to change.
    """
    del main_file
    env = consteval.Env(profile, dict(program.consts))
    try:
        pipeline = mode.decide(program.statements, profile, lambda e: consteval.is_constant(e, env),
                               lambda e: consteval.value_of(e, env)) == "Pipeline"
    except Exception:
        pipeline = False
    lines: list[str] = []
    scan_count = 0
    inside = False       # within a Scan(...)'s own span (see module docstring)
    in_sub = False
    prev_kw = ""
    for stmt in _flatten(program.statements):
        if isinstance(stmt, S.SubDef):
            in_sub = True
            continue
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword
        if kw in ("ENDSUB", "ENDFUNCTION"):
            in_sub = False
        if kw == "SCAN":
            if prev_kw != "SLOWSEQUENCE":
                if scan_count:
                    lines.append("")
                lines.append(f"line {stmt.line}: Scan: {scan_count}" + (" (Subroutine)" if in_sub else ""))
                scan_count += 1
                inside = True
            else:
                inside = False
            prev_kw = kw
            continue
        if kw == "NEXTSCAN":
            inside = False
            prev_kw = kw
            continue
        prev_kw = kw
        if not inside:
            continue
        if kw == "PORTSET" and len(stmt.args) == 2:
            lines.append(f"line {stmt.line}: PortSet = 100")
        elif kw == "SW12":
            lines.append(f"line {stmt.line}: Sw12 = 100")
        elif kw == "PANELTEMP":
            _paneltemp(stmt, env, lines)
        elif kw == "DELAY":
            _delay(stmt, env, lines, pipeline)
        elif kw == "TCDIFF":
            _tcdiff(stmt, env, lines)
    return lines


def _paneltemp(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    if len(stmt.args) < 2:
        return
    timeout = _num(_const(stmt.args[1], env))
    if not timeout or timeout <= 0:
        return
    cost = (10 + _round_half_up(10000.0 / timeout)) * 100
    lines.append(f"line {stmt.line}: PanelTemp = {cost}")
    lines.append(f"line {stmt.line}: PanelTemp = {cost}")


_UNIT_US = {"USEC": 1, "MSEC": 1000, "SEC": 1000000, "MIN": 60000000}


def _delay(stmt: S.Instruction, env: consteval.Env, lines: list[str], pipeline: bool) -> None:
    # Timed only for RepDelay == 0 in PipelineMode; units case-insensitive (docs/cli.md).
    if len(stmt.args) < 3 or not pipeline:
        return
    value = _num(_const(stmt.args[1], env))
    units = stmt.args[2]
    if _num(_const(stmt.args[0], env)) != 0 or not value:
        return
    if not isinstance(units, S.Name) or units.name.upper() not in _UNIT_US:
        return
    cost = ((int(value * _UNIT_US[units.name.upper()]) + 100) & 0xFFFFFFFF) // 100 * 100
    lines.append(f"line {stmt.line}: Delay = {cost}")


def _tcdiff(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    if len(stmt.args) < 9:
        return
    reps = _num(_const(stmt.args[1], env))
    settling = _num(_const(stmt.args[8], env))
    if reps == 1 and settling == 15000:
        lines.append(f"line {stmt.line}: TCDiff = 2900")
