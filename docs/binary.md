# CR6Comp.exe internals (CR6.14.2.0)

Findings from Ghidra 11.4.2 headless decompilation (`tools/ghidra/DumpAll.java`) and pefile.
Addresses are virtual addresses in the 2025-03-04 build (image base 0x400000). The decompiled
source is not committed (it is Campbell Scientific's code); regenerate it locally when needed.

## Build facts

- PE32 i386 console, MSVC with `/RTC` runtime checks (stack frames initialised to 0xCC, local
  variable names kept in RTC descriptors, which is why strings such as `tdf_fname`,
  `preproc_fname`, `cdm_fname` appear).
- It is the CR6 operating system's compiler compiled for Windows: it contains logger runtime
  strings (`Cmd_OnLine`, `charger_cmd`, status-table field names, `Card OK.`) and emits the
  Status table first in the TDF.
- No PDB; function names are Ghidra's `FUN_<addr>`.

## Key functions

| Address | Role |
|---|---|
| 0x424f60 | `main`: option parsing (see `docs/cli.md`), output files, result line |
| 0x424e80 | strips directory from the program path; `Program name exceeds maximum allowable characters by %d.` when the base name is 60 or more characters |
| 0x424160 | compile driver: pass 1, pass 2, sequence post-processing |
| 0x4b6840 | pass 1: read and parse source into the record stream (`docs/pass1.md`) |
| 0x4baf00 | generic parameter parser driven by the instruction table's type codes (`docs/type-codes.md`) |
| 0x4ba3a0 | splits a type-code string (`"1,39,1,1"`) into an int array |
| 0x5089c0 | report error (`printf`-style) |
| 0x508a00 | report warning |
| 0x50aef0 | shared error/warning formatter (adds `line N: `) |
| 0x458a80 | after pass 2: sequence/scan scheduling checks |
| 0x4eab30 | table finalisation: TDF/list output, `Table %s, is declared but never called.` |
| 0x424710 | writes the table list (`-l`, `-c`) |
| 0x424bb0 | prints `Compile Failed!` and the collected messages |

## Intermediate record stream (pass 1 output)

A growable int array at `DAT_0066d5d8`. Per statement: `[source line][id | (nwords << 10)]
[parameters...]`; `id & 0x3ff` indexes the instruction table; the driver advances by
`(word >> 10) & 0x3ff`. Pass 2 calls `fn_a` of each record's instruction.

Pass 2 placement rules in the driver (0x424160), keyed by instruction id:
- declaration-type instructions after executable ones in the same sequence:
  `%s is declared in the middle of an executing sequence`
- anything after `EndSequence`: `%s illegally occurs after EndSequence.`
- executable instruction outside a sequence before `BeginProg`: `%s illegally occurs before BeginProg.`
- code after an unconditional end (e.g. after `EndProg`-type flow): warning `%s never reached at run time.`

## Instruction table (0x60c310)

16-byte records `{char *name; char *type_codes; int (*pass2)(int *record); void *fn_b}`,
entry 0 blank, 410 named entries (ids 1-410). Extracted to `src/opencrbasic/data/instructions.json`
(`index` there is id-1). Names keep the binary's spelling (`WHILE`, `BEGINPROG`, `Pulsecount`);
matching is case-insensitive. Type codes: `docs/type-codes.md`.

## Built-in function and operator table (0x605188)

112-byte records `{char name[96]; int code; int kind; int argspec; void *fn}`, 134 entries,
extracted to `data/functions.json`.
- First 11 entries are word operators: `NOT MOD OR XOR AND IMP EQV IS TO >> <<` (kind = code).
- `kind` 295: numeric function returning float; `argspec` < 16 is the argument count, larger values
  use the packed form below (`IIF`: 963).
- `kind` 309: function returning a number; 312: returning a string; 311: `INTDV`.
- Packed `argspec`: low nibble = argument count; then 2 bits per argument, first argument in the
  lowest bits: 0 = float expression, 1 = string, 2 = integer expression, 3 = variable / any type.
  Checked against the CHM: `INSTR` 0x964 = 4 args (int, string, string, int); `MID` 0x293 = 3 args
  (string, int, int); `SERIALOUT` 0x2965 = 5 args (int, string, string, int, int);
  `SERIALINBLOCK` 0x2E3 = (int, variable, int).
- `code` is the function number used in the runtime expression code (`LOG` and `LN` share 8;
  `CHR$`/`CHR` share 229).

## Predefined symbol table (0x60dcd0)

52-byte records `{char name[40]; float fvalue; int value; int extra}`, 150 entries, extracted to
`data/symbols.json`. Includes block keywords (`While`, `Until`, `Select`, `Case`, `SUB`,
`FUNCTION`, `PROG`, `TABLE`, `SCAN`, `SUBSCAN`, `DO`, `FOR`), `TRUE` (-1) / `FALSE` (0),
thermocouple types (`TypeT`=0 ...), time units (`usec`=0, `msec`=1, `sec`=2, `min`=3, `hr`=4,
`day`=5, `MON`=6), data types (`UINT1`=1, `UINT2`=2, `UINT4`=3, `LONG`=6, `FP2`=7, `IEEE4`/`FLOAT`=9,
`STRING`=11, `NSEC`=14, `BOOL8`=17, `IEEE8`/`DOUBLE`=18, `BOOLEAN`=28), logger types (`CR6`=6 =
`LoggerType`), `SW12_1`=501, COM ports (`ComRS232`=1 ... `ComC1`=9, `ComC3`=10, `ComU1`=11 ...
`ComUSB`=17, `ComRF`=18), terminals (`C1`-`C4` = 101-104, `U1`-`U12` = 201-212, `RG` = 401), CPI/CDM
module types, input ranges (`mV5000`=800 ... `AutoRange`=810, `mV5000C`=820 ...), `X1`-`X4`,
`OSVERSION`.

## Error message catalogue

`docs/messages.tsv` (generated by `tools/message_catalog.py` from a decompile dump): every format
string passed to the error or warning reporter, with the emitting function; 782 distinct texts.

## Message tracer (`tools/trace_errors.py`)

`winedbg --file cmds.txt ./CR6Comp.exe prog.CR6` works on macOS Wine (batch `break *ADDR` /
`cont` / `bt` / `x/s *(int*)($ebp+8)`), but a breakpoint is disabled after its first hit
("Invalid address ... disabling it"), so it can trace one message per run only. The tracer
instead patches a COPY of the exe (cached as `scratch/re/<exe>.trace.exe`, never `compiler/`):
a `jmp` at the entry of the shared formatter to a ~110-byte cave placed in the zero slack at
the end of `.text`; the cave saves registers, writes one 48-byte record to stderr
(`GetStdHandle(-12)`/`WriteFile`), restores, re-executes the displaced prologue and returns.
Record: magic, kind (1 error, 0 warning), format-string VA, reporter return address, then 8
frames walked along the `ebp` chain (frame 0 = the code that called the reporter). Records come
in emission order, which can differ from the line-sorted order of the printed messages.

    uv run --no-sync python tools/trace_errors.py cr6|cr1000x|cr300|cr350 prog.CR6 [--json]

Formatter (the function behind the error/warning reporters) per compiler, displaced prologue
bytes in brackets:

| Compiler | Formatter | Error reporter | Warning reporter |
|---|---|---|---|
| CR6Comp | 0x50aef0 [6] | 0x5089c0 | 0x508a00 |
| cr1000xcomp | 0x50afb0 [6] | 0x508a80 | 0x508ac0 |
| cr300comp | 0x5c3b40 [9] | 0x5c3ad0 (plus a gated variant at 0x563c50) | 0x5c6d20 |
| cr350comp | 0x5c2e20 [9] | 0x5c2db0 | 0x5c6010 |

The cr300/cr350 builds lack the `/RTC` `0xCC` frame fill pattern at function entry, use
`sub esp, N` frames, and put the formatter behind a thunk at 0x4db262 (cr300) / 0x4da316 (cr350).

## Time tracer (`tools/trace_times.py`)

Same cave technique as the message tracer, hooked at `FUN_00522e50(units)` of CR6Comp, the function
every code generator calls to add its time (100 us units) to the open `Scan`
(`DAT_0066d76c + 0x44`; cr1000xcomp: `FUN_0051d570`). It prints each added amount and the
return address into the caller, in compile order. The first analog call of a scan also adds an
overhead directly (78 CR6 / 16 CR1000X, `FUN_00522ec0` / `FUN_0051d5e0`), invisible here.
`FUN_0046a4b0` (NextScan) holds the comparison and message; `FUN_0046a910` (NextSubScan) the SubScan
rules; `FUN_00524f90` (cr1000x `FUN_0051f300`) the shared analog time formula; the instruction
table at 0x60c310 (16 bytes per entry, name first) maps ids to code generators through the thunk
at entry+8. docs/instructions.md "Scan time budget" has the resulting model.

## Data table allocator (cr300comp / cr350comp, round 15)

Decompiled with Ghidra 12 (`scratch/re/cr300comp.decomp.c`, `cr350comp.decomp.c`, untracked;
regenerate with `tools/ghidra/DumpAll.java`). The tracer frames of `Unable to allocate enough
data table space ...` / `Not enough storage area for DataTables.` (0x5af9c3, 0x5afb17 on
cr300comp) land in `FUN_005ae6e0` (cr350comp `FUN_005ad9b0`, same code), the end-of-program
table allocator; the rules read from it are in `docs/tdf-rules.md` "Storage limit". Landmarks
(cr300comp): `FUN_005ab6b0` creates a `DataTable` (the size argument is an `int` already; below
-1 it is negated and flag `+0x186` set), `FUN_005ac830` closes it (size 0 becomes 1; size below
the DataEvent minimum is raised), `FUN_005b7d00` fixes a table's block (`+0x24` records per
block = `0x1f0 / bytes`, `+0x20 = k * bytes + 16`), `FUN_005b50c0` is the flash-wear minimum
record count per pass (floats `0x68d7d4` = 3.6e6, `0x68d7cc` = 24, `0x6a8800` = 1e5, `0x6903c0`
= 365, `0x6a880c` = 2.592e9, `0x6a8808` = 8.64e7), `FUN_005d4660` rounds up to 4096. The pool is
`local_f0 = 0xA00000` (cr350comp `0x3200000`), `local_84 = local_f0 - (n - 2) * 0x4000` with n the
internal table count (user tables + 3 with a `Public` variable), the table area starts at
`0x600000` (cr350comp `0x4780000`). The loop runs 26 passes (`local_cc < 0x1a`).
