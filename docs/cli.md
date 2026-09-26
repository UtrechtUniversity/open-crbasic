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
| `-u` | none | Sets a flag (`DAT_0066d5b8`) that gates a real, oracle-confirmed warning: `%s declared but not used.` for a `Public`/`Dim` variable (including an array) never referenced anywhere in the program -- printed only when `-u` is given, which is why an earlier session's search for this message's trigger (`PROGRESS.md` "Investigated, not implemented") found nothing (it never passed `-u`). Decompiled to `FUN_004ba180`, gated on a symbol-table flag bit (`0x8000`) plus two offset checks not fully decoded. **Not implemented**: the check needs the checker's own symbol/usage table (`checker/symbols.py`, a concurrently-owned module) to do properly; implementing a second, independent usage-scanner here would duplicate that logic and likely drift from it. `Const`/`Alias`/`Sub`/`Function` were tested and are *not* flagged by `-u`, only `Public`/`Dim` variables. |
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
- `Delay(RepDelay, Value, Units)` with `Units` spelled exactly `mSec`: `Value*1000 + 100`. The
  match is case-sensitive by one negative data point (`sdmsio4_1.CR6`'s `Delay (1,10,MSEC)`,
  all-upper-case, produces nothing despite an otherwise identical shape) -- documented as an
  uncertain inference, not a confirmed rule.
- `TCDiff(..., Reps, ..., SettlingTime, ...)` with `Reps=1` and `SettlingTime=15000` (the only
  shape exercised): `2900`.
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

Investigated, not implemented: `VoltSe`'s own cost also depends on its `Reps` argument (1500,
3000, 4400, 5800, 11500 for `Reps` 1/2/3/4/8, `SettlingTime=15000` fixed) but the successive
deltas (1500/1400/1400/~1425) rule out a simple linear-in-`Reps` formula, and this was not
pursued further (absent from every program in this repository). 91/100 CHM-sample files matched
exactly after excluding the pre-existing checker verdict gaps below; the residual misses are the
two `VoltSe` cases above plus five files where this project's own checker currently false-rejects
a real instruction/function (`SWVX`, `UDPSocketOpen`/`Send`/`Recv`/`Close`, `Base64Encode`) that
the real exe accepts -- a pre-existing checker gap unrelated to `-t` itself, not a timing bug.
