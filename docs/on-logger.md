# On-logger compilation versus the PC compilers

The PC pre-compilers are what the fixtures record, but the logger's own compiler decides whether a
program runs. `tools/logger_oracle.py` compiles fixture cases on a logger attached by USB and puts
its verdict beside the PC compiler's and crbc's on the same bytes.

## 2026-11-02, bench CR6, CR1000X and CR300

Windows 11, LoggerNet's compilers in `C:\Campbellsci\Lib\Compilers`, byte-identical to the ones
the fixtures were recorded with:

| Compiler | SHA-256 | Logger | OS |
|---|---|---|---|
| `CR6Comp.exe` | `f6a6efca455f88225fe4f46732409ccf96d8cd4f9201b717746c4dc69ba14f55` | CR6, serial 27946 | `CR6.14.4.0` |
| `cr1000xcomp.exe` | `9c327f8bae2e55546e5c7a35c931d2f5c21516ed121612e869e1c9c8a6072175` | CR1000X, serial 59624 | `CR1000X.8.5.0` |
| `cr300comp.exe` | `755329ee63309bfe8299fc65523e5e231ce18138cfb59209a79b4eb2c04275a1` | CR300, serial 33590 | `CR300.11.3.0` |

80 cases per logger (40 recorded accepted, 40 rejected; seed 2026), skipping the instructions
listed in the tool. Every PC-compiler verdict equalled the recorded one.

| Logger | logger = PC compiler | crbc = PC compiler (verdict) | crbc = PC compiler (normalised output) |
|---|---|---|---|
| CR6 | 78/80 | 80/80 | 78/80 |
| CR1000X | 77/80 | 80/80 | 80/80 |
| CR300 | 79/80 | 79/80 | 76/80 |

Every crbc difference is a case already marked `not_modelled` (strict xfail): `continued_known-cr6`,
`cr6-arity2-010`, `cr300-destinations-182` (the one verdict: crbc accepts `PanelTemp(X,X)`,
CR300/CR350 best effort), `cr300-f9k`, `cr300-pc-C2-r12` and `leading-op-023-cr300`.

### Where the logger and the PC compiler disagree

| Case | PC | Logger | The logger's message |
|---|---|---|---|
| `cr6-r17_cr6_oom-89` | accepts | refuses | `FFEA045A Out of memory, allocating 67108864 bytes.` |
| `cr6-r18_oom_auto-179-r16` | accepts | refuses | `Warning: Insufficient SF memory for DataTables` / `Unable to create Table T1.` |
| `cr1000x-r18_inline_calltable-16-cr300_plain_N578446` | accepts | refuses | `DataTable size allocations too large. Reduce the number of records in one or more data tables` |
| `cr1000x-r7_sdm_float_typing-21` | accepts | refuses | `line 5: Invalid SDM Address: 255, must be 0 - 14.` |
| `cr1000x-avgrun_in_sub` | refuses | **compiles** | -- |
| `cr300-matrix-paren-neg` | accepts | refuses | `line 8: Illegal option.` (`Matrix(-(2147483648),A,B,C)`) |

The first three are memory the PC compiler does not size. The SDM address and the `Matrix` option
are range checks only the logger makes. `cr1000x-avgrun_in_sub` (`AvgRun` with an instance count
inside a `Sub`) is accepted by OS 8.5.0, newer than the OS the installed `cr1000xcomp.exe` serves.
None of these is a crbc defect against the PC compiler; they are candidates for `--strict` checks.

### Line endings

Both `CR6Comp.exe` and crbc reject a program whose lines end in CR CR LF, or in a bare CR, and
accept LF and CRLF. Their first messages differ for those two endings (`CR6Comp.exe`: the block
closer, e.g. `line 6: ... EndFunction`, or `No BeginProg in Program.`; crbc: the block opener, or
line 1). On Windows, `Path.write_text` turns a CRLF fixture source into CR CR LF; the test suite
writes untranslated there since this was found.

### A CR300 hard fault

During the CR300 run the logger wrote `CPU:WatchdogInfo.txt`: `Hard Fault` at logger time
21:11:21 (about 19:11:32 UTC), `Fast Scan : 7`, about 30 s after the USB link had failed while case
`accept-in-scan-auto-cr300` was loaded, whose line 7 is `AcceptDataRecords(3000,32769,F)` in the
scan. Running that program alone again for a minute caused no fault, so the trigger is not
established.
