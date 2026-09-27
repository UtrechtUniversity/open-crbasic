"""Logger profiles: the per-model data and conventions the compiler is parameterised by.

Each profile names the directory of tables extracted from that model's compiler
(`tools/extract_tables.py`) and the few output conventions that differ between models.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class Profile:
    name: str               # "cr6", "cr1000x", ...
    display: str            # logger name as the exe prints it
    version: str            # OS version string of the reference compiler
    date: str               # build date string of the reference compiler
    data_dir: Path
    reports_mode: bool      # True: "Compiled in <X>Mode."; False: "Compiled OK."
    exe_name: str           # reference compiler file name in compiler/
    # An unresolved predefined-symbol name at a checked expression position (`checker/expressions.py`):
    # CR6/CR1000X word it "Invalid symbol in expression: {name}."; CR300/CR350 use "{name} is not
    # defined." for the same case (oracle evidence: `docs/loggers.md`). `{name}` = the identifier
    # as written in source (not case-normalised).
    unresolved_symbol_message: str = "Invalid symbol in expression: {name}."
    # `checker/__init__.py`'s call to `warnings.check_early` (reachability/fnotch/background-
    # calibration): CR6/CR1000X run it unconditionally, oracle-confirmed genuinely ungated even
    # when a later, unrelated tier-B error exists in the same file (`docs/instructions.md` "Two
    # ungated call sites"; the `caf5c15cd16b.CR6` fuzz reproducer explicitly shows a tier-B
    # placement/fallback error *and* a reachability warning together on CR6). CR300/CR350 do
    # **not** share this: an undefined-symbol-class error anywhere in the file (tier A's
    # `check_consts`, or tier B's `expressions.check`) suppresses every `check_early` warning in
    # the *whole* file, including ones on lines textually before the error and on statements that
    # are themselves otherwise clean (oracle, this round, `docs/loggers.md`: `PanelTemp(PTemp,
    # 15000)` on its own line 4 loses its own fnotch warning merely because a *different*,
    # unrelated `TCDiff` two lines later references an undefined name; reachability shows the same
    # suppression). A same-file error that is *not* itself an undefined-symbol-class finding (e.g.
    # `FileManage`'s own drive-string validation, not implemented by this project but visible in
    # the corpus's own oracle transcripts) does **not** suppress it (`tablefile_1.CR6` keeps its
    # own fnotch warning alongside an unrelated `Invalid drive` error) -- consistent with gating
    # `check_early` on CR300/CR350 behind the *same* "tier A and tier B both clean" condition tier
    # C already uses, rather than a blanket "any error anywhere" rule.
    early_warnings_gated: bool = False
    # CR300/CR350 only (`checker/structure.py`'s `_check_call_pass2`, `docs/loggers.md`): a bare
    # call whose head name resolves to nothing (not a declared `Sub`, built-in function, or
    # recognised instruction -- most often an instruction name this logger's own smaller table
    # lacks entirely, e.g. `DisplayMenu`/`CardOut`/`ACPower`) gets its own `{name} is not
    # defined.` line immediately before the existing generic `Invalid, or out of place
    # expression: %s.` fallback line, and this shape's before-`BeginProg` placement follow-up
    # (`Expr illegally occurs before BeginProg.`) and pass-2 stop do **not** fire (oracle-
    # confirmed, `docs/loggers.md`) -- unlike CR6/CR1000X, which show only the fallback line and
    # do apply the placement follow-up/stop there (unchanged). The *other* two branches of the
    # same check (a bare call resolving to a declared `Sub` or a built-in function) are
    # unaffected either way -- oracle-confirmed identical on all four loggers, `docs/loggers.md`.
    undefined_call_message: bool = False
    # CR300/CR350: the generic `Invalid, or out of place expression: %s.` echo keeps the raw line's
    # terminator, so the closing `.` is printed on the next line (oracle: 144 help examples,
    # e.g. acpower_1.CR6 on cr300comp.exe; `checker/structure.py` `_out_of_place`).
    fallback_echo_newline: bool = False
    # CR6/CR1000X: a statement whose first token is a known name is syntax-checked in pass 1, so an
    # unclosed `(` there is a pass-1 `No Matching, or out of place ) in expression: %s.`
    # (`checker/structure.py`, `docs/checker.md` "Pass-1 fallback: statements that start with a
    # known word"). CR300/CR350 report that message on every such line regardless (not modelled).
    known_first_word_pass1: bool = True

    def _load(self, name: str) -> list[dict]:
        return json.loads((self.data_dir / f"{name}.json").read_text())

    @cached_property
    def instructions(self) -> dict[str, dict]:
        """Upper-case name -> record (with `id` = table index + 1)."""
        rows = self._load("instructions")
        return {r["name"].upper(): {**r, "id": r["index"] + 1} for r in rows}

    @cached_property
    def functions(self) -> dict[str, dict]:
        return {r["name"].upper(): r for r in self._load("functions")}

    @cached_property
    def symbols(self) -> dict[str, dict]:
        return {r["name"].upper(): r for r in self._load("symbols")}

    @cached_property
    def mode_rules(self) -> dict:
        """Sequential/PipelineMode trigger lists (`mode.py`, `docs/modes.md`)."""
        return json.loads((self.data_dir / "mode.json").read_text())

    @cached_property
    def terminals(self) -> dict:
        """Channel-argument (`U1..U12`-style) range checks (`checker/terminals.py`)."""
        path = self.data_dir / "terminals.json"
        return json.loads(path.read_text()) if path.is_file() else {"channels": {}, "instructions": {}}

    @cached_property
    def fnotch(self) -> dict:
        """`fN1`/fnotch argument value-set check (CR300/CR350 only, `checker/warnings.py`,
        `docs/instructions.md`). Absent (`{}`) for CR6/CR1000X, which allow any value in range
        instead of a fixed set (evidence: `docs/instructions.md`)."""
        path = self.data_dir / "fnotch.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    @cached_property
    def measure(self) -> dict:
        """Value-set/value-range checks for `Range` codes, `SW12`, `Delay`, `CS616`,
        `TimerInput` and `ExciteV`/`ExciteI` (`checker/instr_measure.py`, `docs/instructions.md`).
        Absent (`{}`) means none of these are oracle-confirmed for this logger yet."""
        path = self.data_dir / "measure.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    @cached_property
    def background_calibration(self) -> dict:
        """`Scan` interval threshold, in whole milliseconds, below which the "background
        calibration will be disabled" warning fires (CR6/CR1000X only, `checker/warnings.py`,
        `docs/instructions.md`). Absent (`{}`) for CR300/CR350, which were oracle-probed down to a
        100us scan interval with no such warning (`docs/instructions.md`)."""
        path = self.data_dir / "background_calibration.json"
        return json.loads(path.read_text()) if path.is_file() else {}


PROFILES = {
    "cr6": Profile("cr6", "CR6", "CR6.14.2.0", "03/04/2025", DATA, True, "CR6Comp.exe"),
    "cr1000x": Profile("cr1000x", "CR1000X", "CR1000X.8.2.0", "03/04/2025", DATA / "cr1000x", True,
                       "cr1000xcomp.exe"),
    "cr300": Profile("cr300", "CR300", "CR300.Std.11.02", "02/04/2025", DATA / "cr300", False,
                     "cr300comp.exe", unresolved_symbol_message="{name} is not defined.",
                     early_warnings_gated=True, undefined_call_message=True,
                     fallback_echo_newline=True, known_first_word_pass1=False),
    "cr350": Profile("cr350", "CR350", "CR350.Std.1.07", "02/04/2025", DATA / "cr350", False,
                     "cr350comp.exe", unresolved_symbol_message="{name} is not defined.",
                     early_warnings_gated=True, undefined_call_message=True,
                     fallback_echo_newline=True, known_first_word_pass1=False),
}

EXTENSIONS = {".cr6": "cr6", ".cr1x": "cr1000x", ".cr300": "cr300", ".cr350": "cr350"}


def get(name: str) -> Profile:
    try:
        return PROFILES[name.lower()]
    except KeyError:
        raise ValueError(f"unknown logger {name!r}; choose from {', '.join(PROFILES)}") from None


def for_file(path: Path, default: str = "cr6") -> Profile:
    """Profile implied by a program's extension (`.CR6`, `.CR1X`, `.CR300`, `.CR350`)."""
    return get(EXTENSIONS.get(path.suffix.lower(), default))
