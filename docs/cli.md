# CR6Comp.exe command line (CR6.14.2.0, 2025-03-04)

Source: decompiled `main` (Ghidra, function at 0x424f60) and runs under Wine on 2026-09-25.

```
CR6Comp.exe [options] program.CR6 [messagefile]
```

- Options are parsed before the positionals. Each option is one `-` followed by letters; several
  flags may share one dash (`-st`). An option that takes a file name takes either the rest of the
  same argument (`-zout.tdf`) or the next argument (`-z out.tdf`). **An option expecting a file
  consumes the next argument even if it is the program name**, and will overwrite it.
- No positional: prints ` -- Compile Failed!` / `No Program File`, exit 1.
- One positional: messages go to `CR6cmple.xxx` in the working directory (and stdout).
- Two positionals: messages go to the named file instead of `CR6cmple.xxx`.
- Three or more positionals: silent exit 1.
- Unknown letter: prints `illegal option!` and continues.

| Option | Argument | Effect (observed / decompiled) |
|---|---|---|
| `-b` | file | Write the CDM (CPI device) binary image. Prints `No CDM devices used in this program ` (to stdout and the message file, before the result line, on both success and failure) when none. **Implemented**: detects CDM usage (any `CDM*`/`CPIAddModule2`/`CPISpeed2`/`CPIFileSend` instruction) and prints that message when absent (true of every program in this repository); the binary image format itself is not implemented -- the file is always written empty. |
| `-c` | file | Write the table list (same writer as `-l`; identical output observed). |
| `-d` | none | Print `<exe path> VERSION:CR6.14.2.0  DATE:03/04/2025` and exit 0 without compiling. |
| `-i` | file | As `-p`, but also expands `Include(...)`/`IncludeSection(...)` recursively (splicing in each target file's own preprocessed text); `-p` leaves both as literal, unexpanded text. Decompiled: the option sets one internal flag (`DAT_0066d5b0`) that an `Include`-handling branch inside the same preprocessing code path reads; oracle-confirmed on all 16 repository programs and a 100-file CHM sample (below). |
| `-l` | file | Write the table list: per table name, `Interval`, and fields with `Units`. Includes system tables (`DataTableInfo`, ConstTable, `Public`). |
| `-m` | none | Sets a flag (`DAT_0066d5b4`) and allocates/grows a text buffer that several internal functions (a memory allocator wrapper, `FUN_00508be0`, and per-table allocation code) append `line %d: %d`/`Table '%s'` entries to. **Decompiled and oracle-tested, has no observable CLI effect**: nothing in the reachable code ever writes that buffer to any file or stream (`fwrite`/`fprintf` on it was searched for and not found), and byte-for-byte comparison of `-t`/`-z` output and the message file with and without `-m` combined found no difference beyond the echoed program name. Read as a vestigial internal memory-usage trace, not a documented feature. |
| `-p` | file | Write the preprocessed source. **Implemented** (`src/opencrbasic/preprocess.py`); see "Preprocessed output" below -- this replaces an earlier draft of this row that assumed comments were stripped and includes always expanded, neither of which is true. |
| `-s` | none | After success, also print `ProgSignature =  <n>.` (two spaces). The value is the PakBus signature (seed 0xAAAA) of the main file's bytes, continued over each included file's bytes in expansion order (`src/opencrbasic/signature.py`; matches the exe on 5 repo programs). |
| `-t` | none | Write a `[Times]` section to the message file with per-instruction timing estimates (`line 205: Scan: 0`, `line 212: PanelTemp = 1100`). **Implemented for a narrow, oracle-verified instruction set** (`src/opencrbasic/timing.py`); see "Timing output (-t)" below. |
| `-u` | none | Sets a flag (`DAT_0066d5b8`) that gates a real warning: `%s declared but not used.`. **Implemented** (`src/opencrbasic/checker/unused.py`, wired through `compiler.compile_file`'s `warn_unused` parameter and `cli.py`'s `-u`; a standalone module, not routed through `checker.check`). Oracle-confirmed (`tools/tdf_probe.py <snippet> --extra=-u`, cross-checked identical on `cr1000xcomp.exe`/`cr300comp.exe`/`cr350comp.exe` too -- no per-logger differences found): flags a `Public`/`Dim` variable (whole array/`String`, any one element's use marks it used) and a `Sub`/`Function` parameter never referenced elsewhere, as `line N: Warning: %s declared but not used.`; also flags a `Sub` **or** `Function` never referenced elsewhere (called, or its name assigned to as its own return value) as a bare `Warning: %s declared but not used.` (no `line N:`, positioned after every numbered line but *before* the pre-existing "table declared but never called" warning -- both oracle-confirmed and decompiled: `FUN_004eab30` calls the `-u` check, `thunk_FUN_004ba180`, once early, well before the later per-table finalisation loop that detects an uncalled table). `Const`/`ConstTable` fields are never flagged; an `Alias` name is never itself flagged, but merges into the aliased variable's usage identity (a reference to the alias name elsewhere counts as using the original); `Alias`/`Units`/`ReadOnly`'s own mention of a name is never itself a use. A variable referenced only as a `DataTable` output source counts as used. Gated on `not diags.errors` (any error anywhere in the file suppresses every `-u` warning, oracle-confirmed). A corpus case (`ShuntLib.crb`'s `SampleShunt`, called only behind a false `#If` in `Main.CR6`) and a `Return`-based probe confirm an uncalled `Function` is flagged identically to an uncalled `Sub`; a body that assigns the function's own name (`MyFunc = value`) is itself a reference. `compiler.CompileResult.result_lines` sorts diagnostics with a `file_order` primary key (`Include`/`IncludeSection` give each file independent line numbering, so a raw global line sort scrambles multi-file `-u` output; for a single-file program `file_order` is trivial). Verified byte-identical against the real exe on all 16 `crbasic/` programs with `-u` (`tests/test_unused.py`, fixtures in `tests/fixtures/cli/`), including a synthetic multi-`Include` case for the file order. |
| `-z` | file | Write the binary table definition file (TDF): version byte `0x01`, then the PakBus table-definition structure (Status table first). |

Message file layout for a success with `-s -t`:

```
[Times]
line 205: Scan: 0
line 212: PanelTemp = 1100
p.cr6 -- Compiled in SequentialMode.
ProgSignature =  53316.
```

stdout carries only the result line(s), not `[Times]`/`-b`'s CDM message... except `-b`'s own "No
CDM devices used in this program " line, which **does** also go to stdout (oracle-confirmed).

Result line: `<program name as given> -- Compiled in SequentialMode.` or `PipelineMode.`; on failure
`<name> -- Compile Failed!`, a blank line, then one line per error `line N: <message>`; warnings
after a success are `Warning: <message>`. The message file uses CRLF throughout, **except** the
blank line immediately after a `-- Compile Failed!` header, which is a bare LF (oracle-confirmed
on both a plain failing compile and one combined with `-t -b`; `src/opencrbasic/cli.py`'s
`_message_bytes` implements this).

## Preprocessed output (-p / -i)

Verified byte-for-byte against the real exe on all 16 `crbasic/*.CR6` programs (with their `.crb`
siblings) and a 100-file random sample of `tests/corpus/chm/`, for both `-p` and `-i`
(`src/opencrbasic/preprocess.py`). The transformation, established entirely by direct oracle
testing (scratch probes under Wine, not decompilation) since it is a plain text-in/text-out
feature:

- Comments (both a whole `'...` line and a trailing one) are kept **verbatim** -- not stripped.
- Blank lines are kept.
- A line continuation -- an unquoted, uncommented `_` whose only preceding characters on the
  physical line are whitespace -- is resolved by deleting just the underscore and directly
  concatenating the following physical line's raw text: no separator is inserted, and the
  continuing line's own leading whitespace is kept (so a chain of `_`-continued lines, each
  indented for readability in the source, comes out as one long line with the original tabs
  embedded in the middle of it). A comment trailing the `_` marker on the same physical line is
  dropped along with the marker (it does not survive into the joined text, unlike an ordinary
  trailing comment on a non-continued line).
- `#If`/`#ElseIf`/`#Else`/`#EndIf`/`#If_no_remove`/`#UnDef` directive lines are removed entirely;
  only the lines of the taken branch remain, evaluated with the same Const/`#If` engine used for
  compilation (`parser._Parser`'s own directive handling and expression parser, reused, not
  reimplemented) -- so a Const defined via an `IncludeSection()` still resolves correctly even
  though `-p` itself never expands that `IncludeSection`.
- `-p`: a bare `Include(...)` or `IncludeSection(...)` statement is left as literal, unexpanded
  text.
- `-i`: both are expanded recursively, splicing in the target file's own preprocessed text in
  place.
- The whole file is read straight through, `latin-1`; the compiler stops at the first top-level
  `EndProg` exactly as it does for compilation (a handful of CHM examples concatenate a second,
  unrelated program or bare prose after the first program's `EndProg`; none of it appears in
  `-p`/`-i` output).
- Output is CRLF throughout, one trailing CRLF after the last line.

98/100 CHM-sample files matched exactly; the two exceptions
(`cdmvw300dynamic2_1.CR6`/`cdmvw300static2_1.CR6`, both deliberately-invalid `{ }` array-literal
probes with well over a hundred repeated "Invalid, or out of place expression" errors) have the
exe's own `-p` output **truncate partway through the file** at a point that lines up with roughly
where `compiler.py`'s documented 4096-byte message buffer would overflow from the parse errors
alone -- suggesting `-p`'s text emission is tied to the same pass-1 tokenizer loop that also
generates diagnostics, not a wholly separate text pass. Reproducing that exact truncation point
would require coupling this module to the checker's own error-accumulation, which is out of scope
here; this is a known, narrow, documented gap, not silently absorbed into the "98/100" figure.

## Timing output (-t)

Coverage is intentionally narrow, established by extensive oracle testing (16 repository
programs, a 100-file CHM sample, and targeted scratch probes to isolate each instruction's
argument dependence) rather than by reading the decompiled cost-computation code, which turned
out to be a per-instruction, per-call-site formula (`thunk_FUN_00522e50`'s ~20 call sites, no
single shared table) -- reimplementing all ~20 was not attempted; only the instructions that
actually appear in this repository's programs or the CHM sample were pursued.

- Gating: entries are printed only if pass 1 is clean (`cli._pass1_ok`) **and** no error is one of
  `Invalid symbol in expression`, `Could not find variables`, `Include file ... must have valid
  program file extension`, or `Expr illegally occurs before BeginProg.` (prose lines ahead of
  `BeginProg`). The real exe prints a bare `[Times]` for these although they are pass-2 findings
  in our checker. Round 9 (fixture `timing_symbol_gate.json`): `Invalid symbol in expression` hides
  the entries only when it is ahead of `BeginProg` and not inside a `Sub`/`Function` body or an
  assignment (a table instruction argument such as `Minimum(1,Nope,..)`, prose lines); `X = Nope`
  and `Randomize(Nope)` in the scan, in a `Sub` or `X = Nope` in a table body still print
  `Scan: 0` and the rest (`cli._hides_times`). Any other error positioned before `BeginProg` (inside a `Sub`, a `DataTable`)
  does not hide entries (`avgrun_4`, `cdmbridgefilt_1`, `fieldnames_2`; CHM, CR6: 503/507 ->
  506/507 exact; repo 16/16). Not explained: `pointervariable_2` (real prints no `[Times]` at
  all).
- A top-level `Scan(...)` statement (one that does not immediately follow a bare `SlowSequence`)
  opens a timed span, prints `Scan: N`, and closes at its matching `NextScan`; `N` is a 0-based
  count of such spans, **not** a time value (`sdmsio4_1.CR6`'s two sequential top-level `Scan`
  blocks give `Scan: 0` then `Scan: 1`). A `SlowSequence`'s own `Scan(...)` does not open a span
  at all (`Main.CR6`'s three `SlowSequence`s contribute nothing). Nothing outside any span is
  timed (a call inside a `Sub`/`Function` defined and only called outside `BeginProg`'s own `Scan`
  contributes nothing either, `PT1000_tester.CR6`).
- Inside an open span: `PanelTemp(Dest, Timeout)` -> `(10 + round(10000 / Timeout)) * 100`,
  printed twice per call -- a formula fitted from seven `Timeout` values via a scratch probe and
  confirmed exact on all seven (every repository/CHM-sample call itself uses `Timeout=15000`,
  giving `1100`).
- `PortSet(Port, State)` (exactly 2 arguments) and `Sw12`: always `= 100`. A 3-argument `PortSet`
  call (`spiwrite_1.CR6`) produces no entry.
- `Delay(RepDelay, Value, Units)`: timed only for `RepDelay == 0`, a non-zero `Value` and PipelineMode
  (`mode.decide`); `Units` is case-insensitive (`uSec`/`mSec`/`Sec`/`Min`). Cost
  `((Value*unit_us + 100) mod 2^32) // 100 * 100` (100 Min gives 1705032800). The earlier "literal
  `mSec`" rule came from a `RepDelay=1` sample. Fixture-backed in `tests/test_timing.py`.
- A blank entry precedes every `Scan` line but the first; a `Scan` inside a `Sub`/`Function` is
  labelled `Scan: N (Subroutine)` (same counter; its body is timed like any span). Both branches of
  a single-line `If c Then a Else b` are timed (inline bodies are walked in order).
- `VoltSe`, `VoltDiff`, `TCSe`, `TCDiff` (Range 800/802/804 plain, 820/822/824 "C"; fitted on ~9,300
  real points, 0 residual): with `ks = (24*s' + 127)//256` (`s' = settle`, 500 when 0),
  `kx = max(1, (24e6/integ + 128)//256)` (24 when `integ` is 0; the floor of 1 applies from `integ` 187501 up, oracle-confirmed to 1e8), `n` = measurements (`reps + flag` for
  VoltSe/TCSe, `reps*(1+flag)` for VoltDiff/TCDiff, flag = MeasOff/RevDiff, any non-zero counts
  as 1), the cost is `floor((140 + extra + n*(816 + ceil((32*ks + u)/3) + floor(32*kx/3)))/100)*100`.
  `extra = 50*reps` for a C range, else 0; `u = 160` for a C range with a valid `U` channel
  (`Un`; for differential instructions only odd `n` -- an even `Un` behaves as a numeric channel),
  else 0. A plain range has no channel dependence. Any constant numeric channel (also out of range)
  is "numeric". `integ` is the raw value (`_60Hz` is 60). Not modelled, so no entry: non-constant
  reps/range/flag/settle/integ. Real constant K is anywhere in [140, 141)
  (integer sums); 141 in the earlier fit was wrong at an exact boundary.
- `[Times]` entries are gated on the same pass-1/pass-2 boundary the checker itself already uses
  (`checker/__init__.py`), not on the overall compile result: a program whose only error is a
  pass-2 check (e.g. a channel-range error) still gets a full `[Times]` section, including the
  errored instruction's own entry (`dhcprenew_1.CR6`); a program with a pass-1 (parse-level)
  error gets none at all even though it has statements that would otherwise be timed
  (`readonly_1.CR6`). `src/opencrbasic/cli.py`'s `_pass1_ok` re-derives this by calling the
  checker's own pass-1 functions directly (not reimplementing them), with a safe fallback to
  `result.ok` if that internal API changes shape.
- The `[Times]` header itself is written unconditionally whenever `-t` is given, but has **no
  trailing newline of its own**: with zero entries, it runs directly into the following text
  (`[Times]in.CR6 -- Compile Failed!`, oracle-confirmed) -- `src/opencrbasic/cli.py` reproduces
  this exactly, rather than always inserting a separator.

The residual CHM misses are checker verdict gaps (programs whose pass-1 error the checker does
not find, so `-t` prints entries where the exe prints none), not timing rules.

### Closed forms (CR6, 2026-10-08, oracle-fitted; fixtures in `tests/test_timing.py`)

All need constant arguments (a variable gives no entry). Arithmetic is unsigned 32-bit with the
result shown signed (`Delay(0,36,Min)` = -2134967196, `Delay(0,-5,mSec)` = -4996); `floor100(x)` is
`x//100*100` in that arithmetic. Negative `d` is clamped to 0 for PulsePort/ExciteV.

- `PulsePort(Port, d)`: `floor100(2d+100)`. `ExciteV(Chan, mV, d)`: `floor100(d+100)`.
- `PulseCount`: 100 (no argument dependence). `Calibrate`: six entries, 2000 and 7700 three times
  (any arguments). `CS616(.., Reps, ..)`: `200+500*Reps`.
- `PeriodAvg(.., Reps, .., Timeout, ..)`: `int32(Reps*int(Timeout)*1000+200)`. Timeout is argument 8 on CR6/CR1000X and
  argument 6 on CR300/CR350 (eight-argument layout; fixtures `periodavg_cr*_timeout_arg*`).
- `MuxSelect(Clk, Reset, PW, Chan, Mode)`: PipelineMode only (no entry in SequentialMode);
  `floor100(base + Chan*(2000*PW+50))`, base 13000, or 100 when `Mode` is 0.
- `ACPower(.., Cfg, LineFrq, ChanV, .., ChanI, .., RepsI)`, LineFrq 50 or 60: `1000`, then pairs `(head, x)`, head 61200 (50) / 51200 (60); `x` per
  channel: `Un` 41100 / 34500, numeric 278300 / 231900 (50 / 60). Channel sequence: Cfg 1 `V` then
  `I` x max(RepsI,0); Cfg 2 `V,I,I`; Cfg 3 `V,I,V,I,V,I`; any other Cfg `V,I`. ChanV supplies the
  `V` term, ChanI the `I` terms.
  Any other whole LineFrq `f >= 1` (a fraction is truncated, `f < 1` errors in the real exe; 1..400
  dense plus 500..100000 and 900 random calls, 0 residual) keeps the entry layout but uses
  `head = 1200 + 1000*(2000//f)` and, with `q = 1000//f`, `x = floor100(1003*q + 2171)` for a `Un`
  channel, `floor100(2021*q + 2160)` for a numeric one (the numeric constant is only bounded to
  2160..2163 by the data; integer `x` boundaries do not distinguish them).

- AutoRange / AutoRangeC (810/830) for the four instructions above (1,200 randomised real points,
  0 residual): C-ness as for 820/822/824 (AutoRangeC is the C form), plus one settle-only term per
  measurement pass, `auto_reps*(815 + ceil((32*ks + u)/3))` inside the same `floor100`, with
  `auto_reps = reps` for VoltSe/TCSe (a MeasOff flag does not add one) and `n` for
  VoltDiff/TCDiff (RevDiff doubles it).
- `Therm107/108/109(Dest, Reps, SEChan, VxChan, Settle, Integ, Mult, Offset)`: the VoltSe cost
  with `n = 2*Reps` on a plain range, no channel dependence (any SEChan/VxChan form; ~4,000 real
  points over Reps 1..200, Settle 0..400, Integ 0..300000, 0 residual). Reps < 1 and negative
  Settle/Integ are not modelled. The measurement count `2*Reps` wraps modulo 512 (Reps 300 costs
  what Reps 44 does; Reps 256, 512 crash the real compiler, no entry); all four loggers.

- Bridge instructions (BrHalf, BrHalf3W, BrHalf4W, BrFull, BrFull6W, Resistance; ~2,400 randomised
  real points each over reps, both ranges, channel form, MeasPEx, ExmV 100..2500, RevDiff, RevEx,
  settle, integ; 0 residual). Reps are split into groups of `max(MeasPEx, 1)`; each group prints
  one entry `floor100(140 + extra + n*(816 + ceil((32*ks + u)/3) + floor(32*kx/3)))` (the VoltSe
  form) with `n = g * base * (1+RevDiff) * (1+RevEx)` (`g` reps in the group, any non-zero flag is
  1; `base` 1 for BrHalf/BrFull/Resistance, 2 for BrHalf3W/BrHalf4W/BrFull6W; no RevDiff for the
  Half forms). A C range (either range of the 4W/6W forms) adds `extra = 50*g*base` and, for a
  `Un` channel (any n for the single-ended BrHalf forms, odd n only for differential), `u = 160`.
  Resistance has no range dependence and prints two entries per group, first the cost with
  `g = 1`. Resistance is only modelled for `MeasPEx >= 1` (negative/zero values behave
  irregularly). Reps < 1 and non-constant arguments give no entry (reps > 1000 are linear and timed, round 16). An out-of-range
  `ExmV` (below 20 or above 2500, `Minimum Excitation is 20mV.` / `Excitation exceeds 2500.`) hides
  all entries in the real exe but our checker does not raise it (verdict gap, not a timing rule).

- `AM25T(Dest, Reps, Range, DiffChan, MuxChan, TCType, Tref, ClkPort, ResPort, VxChan, RevDiff,
  Settle, Integ, Mult, Offset)` (1,500 randomised real points, 0 residual): with `P = 816 +
  ceil(32*ks/3) + floor(32*kx/3)` (the VoltSe `ks`/`kx`), `n = 1 + RevDiff` and `c = 50` for a C
  range (C or AutoRangeC): a `Un` VxChan prints `floor100(140 + n*P + c)` three times and then
  `300` (a numeric VxChan prints neither). Then `floor100(140 + Reps*(n*(P + 80) + c +
  auto*n*(815 + ceil(32*ks/3) + 80)))` (`auto` for AutoRange/AutoRangeC), and finally `400 +
  200*N` for DiffChan number `N` (numeric or `UN`; `+40000` for the `UN` form, even N included).
  MuxChan, TCType, Tref, ClkPort, ResPort and VxChan's number do not matter. Reps < 1 and > 1000
  give no entry.

All instructions seen in this repository and the CHM sample with a cost are now modelled when
their arguments are constant; anything else prints no entry.

### `-t` on the CR1000X, CR300 and CR350 (round 16, oracle-fitted; fixtures `tests/fixtures/timing_r16.json`)

`tools/timingtest.py` compares the `[Times]` section of the real `-t` run with ours
(`--logger`, `--group repo|chm|all`; exact / mismatch / oracle_error, cached under
`.oracle-cache/times/`). 2026-10-13, repo 16/16 and CHM 506/507 (CR6), 505/507 (CR1000X),
505/507 (CR300), 505/507 (CR350). The oracle_error rows are real-compiler page faults
(`pointervariable_2`, and `strcomp_3` on CR300/CR350); the one CR1000X mismatch is `covspa_1`
(negative SE channels, below).

Gate (CR300/CR350, single pass): every error hides the entries except an instruction's own
argument checks (`cli._SINGLE_PASS_KEEPS`: FileManage/file drive, semaphore, settings-field,
dimension, out-of-bounds, channel-count, table-lookup errors, and the settling-time
range, which the CR300/CR350 settling check raises in `timing_r16` `cr300_measure` and
`reps_large`, whose real `-t` runs print the full section beside it, oracle 2026-10-24).

Instruction set per logger (everything not listed prints nothing):

| | CR6 | CR1000X | CR300/CR350 |
|---|---|---|---|
| `PanelTemp` | `(10+round(10000/T))*100` x2 | `(12+round(10000/T))*100` x2 | none |
| `Battery` | none | 800 | none |
| `SWVX` | not an instruction | 100 | 100 |
| `SW12`, `Delay`, `MuxSelect` | as above | same as CR6 | none |
| `PortSet` (2 args), `PulseCount`, `PulsePort`, `ExciteV` | | same on all four | |
| `Calibrate` | 2000,7700 x3 | 1500,5700 x3 | none |
| `CS616`, `PeriodAvg` | | same as CR6 | not instructions / unchanged |

Open: `Calibrate`'s first two entries grow when a slow-integration measurement (or
`PanelTemp` with a short timeout) is in the scan (CR1000X, integ 250: 5200,20500; CR6: 5000,19700);
not modelled, the six entries above are only exact without such a measurement.

CR1000X measurement (`VoltSe`, `VoltDiff`, `TCSe`, `TCDiff`, 0 residual on 400 random points each;
bridges 300 each; AM25T 300; Therm107/109 300): `floor100(180 + n*(185 + 64u + S + X) + 50c*reps
+ auto*(5 + S + 64u))`, `S = 32*ks` (`ks = (settle+16)//32`, 14 when settle is 0), `X = 32*m`
(`m = round(31250/integ)`, 8 when integ is 0, minimum 1), `c` a C range (incl. AutoRangeC),
`u` = `c` on a valid channel (single-ended 1..16, differential 1..8; otherwise no 64), `auto`
the AutoRange passes (`reps` for VoltSe/TCSe, `n` for the others). `n` as on the CR6 (`reps+MeasOff`,
`reps*(1+RevDiff)`, bridge `g*base*(1+RevDiff)*(1+RevEx)` with MeasPEx groups). AutoRange applies
to single-range bridges only; the two-range 4W/6W forms ignore it. Therm: `n = 2*reps`, plain range.
AM25T entries: `f,f,f,300,total,400+200*chan` with `f = floor100(180 + m*(185+S+X) + 50c)`,
`total = floor100(180 + reps*(m*(265+S+X) + 50c + auto*m*(85+S)))`, `m = 1+RevDiff`.
ACPower: first entry 400 (not 1000), then `(head, x)` pairs as on the CR6; LineFrq 50/60 give
`x` 40500 / 33800, 2..20 give `floor100(992*(1000//f) + c)` with c in [1536,1600) (1550 used); no
`Un` channels. Negative single-ended channels (`-n`, settle once) and channel 0 of a differential
instruction have another cost shape: not modelled (no entry).

CR300/CR350 measurement (`VoltSe`, `VoltDiff`, `TCSe`, `TCDiff`, `Therm107/108/109`, bridges, AM25T;
0 residual on 200-240 random points per family): range and integration time (fnotch) have no
effect, the channel number has none either (negative and out-of-range included). A pass costs
`51136 + settle` (settle 0 is 500); `m` passes per rep add a fixed `50 + 60*m`:
`VoltSe/TCSe = floor100(110 + reps*(51136+s) + MeasOff*(51021+s))`; `VoltDiff/TCDiff` with
`m = 1+RevDiff`: `floor100(50+60m + reps*m*(51136+s))`; Therm: `m = 2`; bridges: `m =
base*(1+RevDiff)*(1+RevEx)`, entries per MeasPEx group as on the CR6 but **at most three**;
AM25T: `f,f,f,500,total,400+200*chan` with `f = floor100(50+60m + m*(51136+s))` and `total =
floor100(50+60m + reps*m*(51136+80+s))`. The `Resistance`/`ACPower`/`CS616` instructions do not
exist on these loggers.

Round 16 additions (all four loggers, oracle-confirmed): costs are 32-bit values printed signed and
truncated toward zero to a multiple of 100 (a 1,000,000-rep CR1000X `VoltSe` prints -109967100);
Therm107/108/109 count `2*Reps` measurements modulo 512; bridge and AM25T entries are linear for
any Reps (the old "Reps > 1000: no entry" rule was a CR6 probing artefact). Variable arguments in
the cost-relevant positions (Reps, Range, Settle, Integ, Timeout, delay) are compile errors in the
real exe ("Variable illegal in Parameter", "Measurement delay must have constant interval"), so
"timing for variable arguments" is not a timing rule: state arguments (`PortSet(C1,var)`,
`ExciteV(Vx1,var,10)`, `SW12(.., var)`) may be variables and cost the same.
