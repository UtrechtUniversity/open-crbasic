# opencrbasic

An incomplete open-source, native re-implementation of Campbell Scientific's CRBasic
pre-compilers for the CR6, CR1000X, CR300 and CR350 dataloggers (`CR6Comp.exe`,
`cr1000xcomp.exe`, `cr300comp.exe`, `cr350comp.exe`). It runs on Linux, macOS and Windows with
only Python 3.10+, no Wine and no Windows binaries. It aims to reproduce the logger compilers'
checks, diagnostics and output artefacts, but known differences remain. Current differential
results report normalized diagnostic output agreement; only outputs explicitly identified as
byte-identical in `PROGRESS.md` have full byte parity. It can already replace the exe for this
repository's supported build checks and gives future tooling (linters, simulators, automated
tests) a parsed, checked program to work from.

Status, measured against the real compilers: `PROGRESS.md`. Design, remaining-work strategy and
parity exit criteria: [`PLAN.md`](PLAN.md#remaining-work-strategy).

## Use

Python dependencies are managed with [uv](https://docs.astral.sh/uv/). The package itself has
no runtime dependencies; the `dev` group adds pytest and pefile (for the reverse-engineering
tools). Keep the environment outside the repository tree (`.envrc` does this with direnv):

```bash
cd opencrbasic
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"   # or: direnv allow
uv sync                              # environment from uv.lock
uv run crbc Program.CR6              # compile
uv run pytest -q                     # tests
uv tool install .                    # optional: `crbc` on PATH outside the project
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

Output follows the exe's format: `Program.CR6 -- Compiled in SequentialMode.` (CR300/CR350:
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

## Editor support

`crbc-lsp` (`python -m opencrbasic.lsp`) is a language server for CRBasic programs; the `logger`
setting selects the CR6 profile (default) or CR1000X, CR300 or CR350. It reports the compiler's
diagnostics and offers hover, completion, signature help, outline, folding, go to definition,
references and highlighting. Its highlighting names are the CR6 ones. A small VS Code client is in [`vscode/`](vscode/README.md).
Setup, settings, feature limits and the host-tested status are in
[`docs/lsp-user-guide.md`](docs/lsp-user-guide.md). Only macOS has been tested in VS Code so far.

## How it was built

The exe is the logger's own compiler built for Windows. Its instruction, function and
predefined-symbol tables were extracted from the binary (`tools/extract_tables.py`), its
behaviour was studied with Ghidra and with thousands of runs of the real exe under Wine, and
everything is checked differentially:

| Tool | Checks |
|---|---|
| `tools/oracle.py` | runs the real exe under Wine (cached) |
| `tools/difftest.py --logger L` | normalized output vs the exe over repository programs and 507 CHM help examples |
| `tools/tdftest.py` | `-z` TDF and `-l` table list, byte for byte |
| `tools/fuzz.py`, `tools/fuzz_replay.py` | mutated programs, differential; reproducers in `tests/corpus/fuzz/` |
| `uv run pytest -q` | unit tests, corpus parse tests, TDF fixtures |

Differential and replay runs default to reporting differences without failing on known gaps. Use
the gates when a nonzero exit is required:

```bash
uv run tools/difftest.py --logger cr6 --gate no-regressions
uv run tools/difftest.py --logger cr6 --gate exact
uv run tools/fuzz_replay.py --gate no-regressions --json replay.json
uv run tools/fuzz_replay.py --gate exact --json replay.json
```

`no-regressions` fails for a false rejection, opencrbasic crash or hang (and both tools
fail for an oracle error). `exact` fails for any result that does not match the normalized
reference output. Replay's `--json FILE` writes summary counts and individual result rows for CI
and later triage. See [`PLAN.md`](PLAN.md#remaining-work-strategy) for how remaining differences
are measured and fixed.

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
