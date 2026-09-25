# opencrbasic

An open-source, native re-implementation of Campbell Scientific's CRBasic pre-compilers for the
CR6, CR1000X, CR300 and CR350 dataloggers (`CR6Comp.exe`, `cr1000xcomp.exe`, `cr300comp.exe`,
`cr350comp.exe`). It runs on Linux, macOS and Windows with only Python 3.10+, no Wine and no
Windows binaries. It checks a program the way the logger's own compiler does and writes the same
messages and artefacts, so it can replace the exe in build scripts and CI, and it gives future
tooling (linters, simulators, automated tests) a parsed, checked program to work from.

Status, measured against the real compilers: `PROGRESS.md`. Design and plan: `PLAN.md`.

## Use

```bash
cd opencrbasic
python3 -m pip install -e .          # optional: installs the `crbc` command
crbc Program.CR6                     # or: PYTHONPATH=src python3 -m opencrbasic Program.CR6
```

`crbc` is a drop-in for `CR6Comp.exe` (`docs/cli.md`):

```
crbc [options] program [messagefile]
  -s            also print ProgSignature
  -z FILE       write the binary table definition file (TDF)
  -l FILE       write the text table list (also -c)
  -v / -d       print version (then compile / and exit)
  --logger L    cr6 | cr1000x | cr300 | cr350 (default: from the file extension
                .CR6/.CR1X/.CR300/.CR350, else cr6)
  --no-message-file   do not write CR6cmple.xxx
```

Output matches the exe: `Program.CR6 -- Compiled in SequentialMode.` (CR300/CR350:
`-- Compiled OK.`), `Warning: ...` lines, or `-- Compile Failed!` followed by `line N: message`
lines; the same text goes to `CR6cmple.xxx` with CRLF line endings; exit status 0 or 1.

As a library:

```python
from pathlib import Path
from opencrbasic import profiles
from opencrbasic.compiler import compile_file

result = compile_file(Path("Main.CR6"), profiles.get("cr6"))
result.ok, result.mode, result.signature
[d.cr6comp() for d in result.diagnostics.items]
result.tables          # list of tdf.TdfTable (Status, DataTables, DataTableInfo, ConstTable, Public)
```

`parser.parse_file()` gives the syntax tree (`syntax.py`) for other tools.

In this repository, `CR6COMP=opencrbasic uv run verify.py` (from `scripts/`) compiles every
program with opencrbasic instead of the exe.

## How it was built

The exe is the logger's own compiler built for Windows. Its instruction, function and
predefined-symbol tables were extracted from the binary (`tools/extract_tables.py`), its
behaviour was studied with Ghidra and with thousands of runs of the real exe under Wine, and
everything is checked differentially:

| Tool | Checks |
|---|---|
| `tools/oracle.py` | runs the real exe under Wine (cached) |
| `tools/difftest.py --logger L` | our output vs the exe over the repo programs, examples, and 507 help-file examples |
| `tools/tdftest.py` | `-z` TDF and `-l` table list, byte for byte |
| `tools/fuzz.py`, `tools/fuzz_replay.py` | mutated programs, differential; reproducers in `tests/corpus/fuzz/` |
| `python3 -m pytest -q` | unit tests, corpus parse tests, TDF fixtures |

Reverse-engineering notes: `docs/binary.md` (exe layout), `docs/cli.md`, `docs/type-codes.md`
(parameter kinds), `docs/frontend.md` (language quirks), `docs/checker.md` and
`docs/instructions.md` (diagnostic rules), `docs/modes.md` (Sequential/Pipeline),
`docs/tdf-rules.md` (table definitions), `docs/loggers.md` (per-logger differences),
`docs/fuzz-findings.md`. Provenance and licensing questions: `docs/legal.md`.

## Layout

```
src/opencrbasic/
  lexer.py source.py consteval.py parser.py syntax.py   front end
  checker/                                               semantic checks and diagnostics
  mode.py tables.py tdf.py signature.py                  outputs
  profiles.py data/<logger>/*.json                       per-logger tables and rules
  compiler.py cli.py                                     driver and crbc
tools/        oracle, difftest, tdftest, fuzzing, table extraction, Ghidra script
tests/        unit tests, corpora, fixtures
docs/         behaviour of the original compilers, with evidence
```
