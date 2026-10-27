# SequentialMode / PipelineMode decision (CR6, CR1000X)

Decompiled from the per-statement hook called in pass 1 (CR6Comp 0x5239a0) and pass 1's
handling of the mode instructions; implemented in `src/opencrbasic/mode.py`. Oracle
probes 2026-09-25: `Select Case`/`EndSelect` before `Battery` in a Scan -> Sequential, the same
with `End Select` -> Pipeline; `If..EndIf`, `For..Next`, single-line `If x Then x = 2` before
`Battery` -> Pipeline; `If x Then Battery(bv)` -> Sequential; `Battery` in a `Sub` -> Sequential;
`PortSet(C1, x)` with a variable state -> Sequential. All agree with the rules below.

State: `sequential` (starts false), `pipeline_forced` (set by a `PipeLineMode` statement),
`depth` (open conditional/loop blocks since the last `Scan`), `in_scan` (Scan nesting),
`in_slow` (count of `SlowSequence` statements).

- `SequentialMode` sets `sequential`; `PipeLineMode` sets `pipeline_forced`. Once either is set
  the rules below stop applying (except that `SequentialMode` still wins).
- Always sequential: `SDMCAN SDMCD16AC SDMAO4 SDMAO4A SDMCVO4 SDMIO16 WriteIO TDR100 TDR200`.
- Measurement-task instructions (64 names, list in `mode.py`) force sequential when `depth > 0`
  or when not inside a Scan (for example in a `Sub` or before `BeginProg`), with exceptions:
  `SW12` only when its option argument equals 1; `Delay` only when its option is 0 or 2;
  `SerialOutBlock`/`SerialInRecord` only outside slow sequences, or inside a conditional in one
  (`SerialInRecord` in an `If` in a slow `Scan`: SequentialMode, round 49).
- A non-constant argument forces sequential: `ExciteV`/`ExciteI`/`PortSet` 2nd argument,
  `MuxSelect` 4th, `VibratingWire` 4th and 5th, `CDM_ExciteV`/`CDM_ExciteI`/`CDM_SW12`/`CDM_SW5`
  4th, `CDM_MuxSelect` 6th, `CDM_SWPower` 3rd. Only numbers and numeric `Const` names joined by
  operators count as constant here: a string literal, string `Const` or string expression, and
  any function call (`ABS(1)`, `LEN("ab")-1`) force sequential, while a `Const` whose value came
  from a function (`Const L = LEN("ab")`) does not. Oracle, round 48, identical on CR6 and
  CR1000X; fixture `tests/fixtures/r48_mode_string_arguments.json`.
- A variable (not constant) **or omitted** `SW12` 3rd, `CDM_SW12`/`CDM_SW5` 5th or
  `CDM_SWPower` 4th argument forces sequential anywhere: omitted optional arguments are stored as
  -0.0 and the exe tests the sign bit. Oracle: `SW12(SW12_1, 0)` inside a Scan -> Sequential;
  `SW12(SW12_1, 0, 0)` / `(..., 1)` inside a Scan -> Pipeline; `SW12(SW12_1, 0, 1)` before the
  Scan -> Sequential, `(..., 0)` there -> Pipeline. Matches the help page for SW12.
- `For If Select While Do` increment `depth`; `Next EndIf End WEnd Loop` decrement it, except
  `Next Scan` (decrements `in_scan`), `Next SubScan` (no effect), and `End` followed by anything
  but `If`/`Select`. **Quirk:** the one-word `EndSelect` is not in the closing list, so after a
  `Select Case ... EndSelect` block the rest of that scan counts as conditional.
- `Scan` resets `depth` to 0 and increments `in_scan`; `NextScan` decrements `in_scan`;
  `SlowSequence` increments `in_slow`.
