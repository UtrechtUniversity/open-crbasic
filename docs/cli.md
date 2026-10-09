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
| `-u` | none | Sets a flag (`DAT_0066d5b8`) that gates a real warning: `%s declared but not used.`. **Implemented** (`src/opencrbasic/checker/unused.py`, wired through `compiler.compile_file`'s `warn_unused` parameter and `cli.py`'s `-u`; a standalone module, not routed through `checker.check`, so it needs no changes to the concurrently-owned `checker/__init__.py`). Oracle-confirmed (`tools/tdf_probe.py <snippet> --extra=-u`, cross-checked identical on `cr1000xcomp.exe`/`cr300comp.exe`/`cr350comp.exe` too -- no per-logger differences found): flags a `Public`/`Dim` variable (whole array/`String`, any one element's use marks it used) and a `Sub`/`Function` parameter never referenced elsewhere, as `line N: Warning: %s declared but not used.`; also flags a `Sub` **or** `Function` never referenced elsewhere (called, or its name assigned to as its own return value) as a bare `Warning: %s declared but not used.` (no `line N:`, positioned after every numbered line but *before* the pre-existing "table declared but never called" warning -- both oracle-confirmed and decompiled: `FUN_004eab30` calls the `-u` check, `thunk_FUN_004ba180`, once early, well before the later per-table finalisation loop that detects an uncalled table). `Const`/`ConstTable` fields are never flagged; an `Alias` name is never itself flagged, but merges into the aliased variable's usage identity (a reference to the alias name elsewhere counts as using the original); `Alias`/`Units`/`ReadOnly`'s own mention of a name is never itself a use. A variable referenced only as a `DataTable` output source counts as used. Gated on `not diags.errors` (any error anywhere in the file suppresses every `-u` warning, oracle-confirmed). An earlier reading (before this session) that `Function`s are never flagged was a false inference from a probe whose `Function` body happened to self-assign its return value (`MyFunc = value`, itself a reference) -- a real corpus case (`ShuntLib.crb`'s `SampleShunt`, called only behind a false `#If` in `Main.CR6`) and a `Return`-based probe both confirm an uncalled `Function` is flagged identically to an uncalled `Sub`. `compiler.CompileResult.result_lines`'s diagnostic sort also gained a `file_order` primary key (`Include`/`IncludeSection` give each file independent line numbering, so a raw global line sort scrambles multi-file `-u` output; a single-file program's `file_order` is trivial and this is a no-op for every previously-verified single-file case). Verified byte-identical against the real exe on all 16 `crbasic/` programs with `-u` (`tests/test_unused.py`, fixtures in `tests/fixtures/cli/`), including a synthetic multi-`Include` case for the file-order fix. |
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
`_message_bytes` implements this -- an earlier version of this file assumed a uniform CRLF join).

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
  in our checker. Any other error positioned before `BeginProg` (inside a `Sub`, a `DataTable`)
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
  is "numeric". `integ` is the raw value (`_60Hz` is 60). Not modelled, so no entry: AutoRange
  (810/830), non-constant reps/range/flag/settle/integ. Real constant K is anywhere in [140, 141)
  (integer sums); 141 in the earlier fit was wrong at an exact boundary.
- `[Times]` entries are gated on the same pass-1/pass-2 boundary the checker itself already uses
  (`checker/__init__.py`), not on the overall compile result: a program whose only error is a
  pass-2 check (e.g. a channel-range error) still gets a full `[Times]` section, including the
  errored instruction's own entry (`dhcprenew_1.CR6`); a program with a pass-1 (parse-level)
  error gets none at all even though it has statements that would otherwise be timed
  (`readonly_1.CR6`). `src/opencrbasic/cli.py`'s `_pass1_ok` re-derives this by calling the
  checker's own pass-1 functions directly (not reimplementing them), with a safe fallback to
  `result.ok` if that internal, concurrently-owned API changes shape again.
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
- `PeriodAvg(.., Reps, .., Timeout, ..)`: `int32(Reps*int(Timeout)*1000+200)`.
- `MuxSelect(Clk, Reset, PW, Chan, Mode)`: PipelineMode only (no entry in SequentialMode);
  `floor100(base + Chan*(2000*PW+50))`, base 13000, or 100 when `Mode` is 0.
- `ACPower(.., Cfg, LineFrq, ChanV, .., ChanI, .., RepsI)`, LineFrq 50 or 60 only (55 prints other
  values, not modelled): `1000`, then pairs `(head, x)`, head 61200 (50) / 51200 (60); `x` per
  channel: `Un` 41100 / 34500, numeric 278300 / 231900 (50 / 60). Channel sequence: Cfg 1 `V` then
  `I` x max(RepsI,0); Cfg 2 `V,I,I`; Cfg 3 `V,I,V,I,V,I`; any other Cfg `V,I`. ChanV supplies the
  `V` term, ChanI the `I` terms.

- `Therm107/108/109(Dest, Reps, SEChan, VxChan, Settle, Integ, Mult, Offset)`: the VoltSe cost
  with `n = 2*Reps` on a plain range, no channel dependence (any SEChan/VxChan form; 2,400 real
  points over Reps 1..200, Settle 0..400, Integ 0..300000, 0 residual). Reps < 1 and negative
  Settle/Integ are not modelled.

Timed but not probed: BrHalf, BrHalf3W/4W, BrFull, BrFull6W, AM25T,
Resistance, AutoRange for VoltSe & co (AutoRange adds ~1300 us at n=1, not fitted).
