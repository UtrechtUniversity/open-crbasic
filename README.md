# opencrbasic

A native re-implementation of Campbell Scientific's CRBasic pre-compilers for the CR6, CR1000X,
CR300 and CR350 dataloggers (`CR6Comp.exe`, `cr1000xcomp.exe`, `cr300comp.exe`, `cr350comp.exe`),
with a language server. It runs on Linux, macOS and Windows with only Python 3.15+, no Wine and no
Windows binaries. It is MIT licensed and **not affiliated with, endorsed by or derived from code of
Campbell Scientific**; provenance and what this repository deliberately leaves out are in the
[legal notes](docs/legal.md). It checks programs; it is not the reference compiler, so keep
Campbell's compiler as the gate before flashing hardware.

## Install

```bash
pip install opencrbasic           # crbc, the compiler
pip install "opencrbasic[lsp]"    # also crbc-lsp, the language server (adds pygls)
# or, with uv:
uv tool install opencrbasic       # uv tool install "opencrbasic[lsp]" for the language server
```

Requires Python 3.15 or newer. The compiler has no runtime dependencies.

## Related projects

- [open-crbasic-vsix](https://github.com/UtrechtUniversity/open-crbasic-vsix): VS Code extension
  (Marketplace: `OttodeVisserUU.opencrbasic`), the client for `crbc-lsp`.
- [open-crbasic-rs](https://github.com/UtrechtUniversity/open-crbasic-rs): a Rust port of the
  verdict path (crate `opencrbasic`, binary `crbc`), equal to this package on all fixtures.
- [open-loggernet](https://github.com/UtrechtUniversity/open-loggernet): a LoggerNet-free client
  and `oln` command line for the loggers; it uses opencrbasic as its compiler when
  `CR6COMP=opencrbasic` is set.

## Supported loggers

| Logger | `--logger` | File extension | Status |
|---|---|---|---|
| CR6 | `cr6` (default) | `.CR6` | gated against `CR6Comp.exe` |
| CR1000X | `cr1000x` | `.CR1X` | gated against `cr1000xcomp.exe` |
| CR300 | `cr300` | `.CR300` | best effort |
| CR350 | `cr350` | `.CR350` | best effort |

"Gated" means the reference verdict, execution mode and first error for realistic mistakes are
compared with the real compiler on every release; "best effort" means measured and kept, not gated.
Per-logger differences: [`docs/loggers.md`](docs/loggers.md).

## Limitations

- **Not the reference compiler.** Measured against the real compilers on the CRBasic help examples,
  a field station's programs and fuzzed mutations, not exhaustively. A small number of false accepts
  (programs opencrbasic accepts and Campbell's compiler rejects) are known and listed in the
  [release record](docs/release-1.0.md). In the gated area one is known: a `Sub` name that is also
  another routine's parameter, used as a value.
- **Diagnostics after the first error** may differ in order, wording or cascade from the reference.
  The first error is fixed when it comes from a realistic mistake
  ([`docs/first-error-review.md`](docs/first-error-review.md)).
- **Not implemented:** the CDM binary image of `-b` (an empty file is written for programs that use
  CDM hardware); `-m` is accepted and does nothing; `-t` covers only the oracle-fitted instruction
  set. CR1000X, CR300 and CR350 table definition files (`-z`) use the CR6 Status table (best effort).
  Outputs and what is best effort per profile: [`docs/artifacts.md`](docs/artifacts.md).
- **Editor:** the editor features were host-tested in VS Code on macOS only; Windows and Linux are
  not claimed, and the check and coverage-notice display has not been host-tested.
- **No hardware tests:** compiling and comparing says nothing about runtime behaviour on a logger.

## Three channels

Every finding belongs to one channel ([PLAN](PLAN.md#three-diagnostic-channels)):

1. **Reference diagnostics**: what the reference compiler reports. Default `crbc` stdout, the message
   file and the exit status carry only these, so `crbc` stays a drop-in.
2. **opencrbasic checks** (`OCB001`-`OCB007`): likely defects the reference accepts, each with
   evidence, controls and a default severity; `crbc --strict` (stderr, exit 3 on a check error),
   `--report`, `opencrbasic.toml`, and the editor (source `opencrbasic (check)`). See
   `docs/checker.md` "opencrbasic checks OCB002-OCB007 (M3)".
3. **Coverage notices**: CR6 reference rules opencrbasic is known not to model, from the
   [evidence inventory](docs/inventory.md); `--report` and, when enabled, editor hints. Information,
   never a verdict.

## Accepted differences

In the differential tools, equal means equal exit status and equal stdout after whitespace
normalization (line endings, trailing spaces); the artifact matrix compares bytes. Differences in the order,
wording or cascade of diagnostics after the first error are accepted;
differences in the first error are fixed when they come from a realistic mistake and otherwise
recorded in [`docs/first-error-review.md`](docs/first-error-review.md). Runner failures and
reference crashes are never counted as verdicts.

## Versions

- The package version (`pyproject.toml`) and `opencrbasic.__version__` are one number
  (`tests/test_versions.py`). Major: a change to the drop-in contract or a gated verdict policy;
  minor: new checks, notices or features; patch: fixes. The VS Code extension, in its own repository, has its
  own version.
- `analysis.API_VERSION` (now 2) versions the editor facade; the language server refuses to start
  with another (`docs/lsp-api.md`). `checks.SCHEMA_VERSION` (now 2) versions the `--report` JSON.
  Each is bumped only on an incompatible change.

## Usage

`crbc` is a drop-in for `CR6Comp.exe` (`docs/cli.md`):

```
crbc [options] program [messagefile]
  -s            also print ProgSignature
  -z FILE       write the binary table definition file (TDF)
  -l FILE       write the text table list (also -c)
  -p FILE / -i FILE   write the preprocessed source (-i also expands Include/IncludeSection)
  -u            add "declared but not used" warnings
  -t            the [Times] section (oracle-fitted instruction set only)
  -b FILE       CDM binary image (not implemented; see Limitations)
  -v / -d       print version (then compile / and exit)
  --logger L    cr6 | cr1000x | cr300 | cr350 (default: from the file extension
                .CR6/.CR1X/.CR300/.CR350, else cr6)
  --no-message-file   do not write the message file (CR6cmple.xxx, CR1000Xcmple.xxx, ...)
  --strict      also run opencrbasic checks (likely defects the reference accepts) on stderr;
                exit 3 when the program compiles but a check reports an error
                (exit 4: invalid opencrbasic.toml)
  --report FILE write a JSON report (reference verdict and diagnostics, check findings)
  --config FILE use this opencrbasic.toml (default: nearest ancestor of the program)
  --check CODE=SEVERITY   set a check severity (error|warning|info|off); repeatable
  --expand FILE expand #Define/#Macro macros into FILE, then compile FILE instead
```

`#Define` and `#Macro` are an opencrbasic extension, not CRBasic: the reference compilers reject
them. `--expand` writes plain CRBasic (definitions commented out, uses expanded inline) that
`CR6Comp.exe` accepts and that is the file to send to the logger; write it next to the source so
its `Include`s resolve. The language server expands them the same way, so the editor reports
the expansion's diagnostics on the line of each use. Syntax and rules:
[`macros.py`](src/opencrbasic/macros.py).

```crbasic
#Define LOGD(msg) DebugMsg = msg    ' release build: #Define LOGD(msg)
#Macro LOGV(name, value)
  DebugMsg = name & "=" & value
  CallTable Debug
#EndMacro
```

Check configuration and the report schema: `docs/checker.md` "Check configuration and report file".

Output follows the exe's format: `Program.CR6 -- Compiled in SequentialMode.` (CR300/CR350:
`-- Compiled OK.`), `Warning: ...` lines, or `-- Compile Failed!` followed by `line N: message`
lines; the same text goes to `<LOGGER>cmple.xxx` (for example `CR6cmple.xxx`) with CRLF line
endings; exit status 0 or 1.

As a library:

```python
from pathlib import Path
from opencrbasic import profiles
from opencrbasic.compiler import compile_file

result = compile_file(Path("Main.CR6"), profiles.get("cr6"))
result.ok, result.mode, result.signature
[d.cr6comp() for d in result.diagnostics.items]
result.tables  # list of tdf.TdfTable (Status, DataTables, DataTableInfo, ConstTable, Public)
```

`parser.parse_file()` gives the syntax tree (`syntax.py`) for other tools.

[openloggernet](https://github.com/UtrechtUniversity/open-loggernet) uses opencrbasic when
`CR6COMP=opencrbasic` is set.

## Editor support

`crbc-lsp` (`python -m opencrbasic.lsp`) is a language server for CRBasic programs; the `logger`
setting selects the CR6 profile (default) or CR1000X, CR300 or CR350. It reports the compiler's
diagnostics and offers hover, completion, signature help, outline, folding, go to definition,
references, highlighting, rename, formatting and quick fixes. Its highlighting names are the CR6 ones.
The VS Code client is [open-crbasic-vsix](https://github.com/UtrechtUniversity/open-crbasic-vsix). Setup, settings, feature limits and the
host-tested status are in [`docs/lsp-user-guide.md`](docs/lsp-user-guide.md). It also shows the
opencrbasic checks and, with `opencrbasic.coverageNotices`, the coverage notices. The editor features
were host-tested in VS Code on macOS (T-01 to T-18, `LSP_TO_BE_TESTED.md`); the check and notice display
(M6) has not been host-tested yet, and Windows and Linux are not claimed.

## Development

Python dependencies are managed with [uv](https://docs.astral.sh/uv/). The package itself has
no runtime dependencies; the `dev` group adds pytest, pefile (for the reverse-engineering tools),
pygls, pyrefly and pre-commit.

```bash
uv sync                              # environment from uv.lock
uv run pre-commit install            # ruff, pyrefly and file hygiene on every commit
uv run pytest -q -n auto             # tests
uv run crbc Program.CR6              # compile with the checkout
uv tool install .                    # optional: `crbc` on PATH outside the project
```

Contributor and agent conventions: [AGENTS.md](AGENTS.md).

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
| `tools/artifact_matrix.py` | every output file per profile and option set, with a baseline gate |
| `tools/inventory.py` | the CR6 evidence inventory and the coverage-notice table |
| `uv run pytest -q` | unit tests, corpus parse tests, TDF fixtures |

The tools that run the real compilers need them in a local, git-ignored `compiler/` directory
(copy `CR6Comp.exe`, `cr1000xcomp.exe`, `cr300comp.exe` and `cr350comp.exe` from a CRBasic Editor
install) and Wine on macOS and Linux.

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

## Releasing

Every push to `dev` that passes CI is published to TestPyPI as `<version>.dev<run number>`. A push
to `main` publishes to PyPI when `version` in `pyproject.toml` (and `opencrbasic.__version__`) has
not been released yet. Publishing uses PyPI trusted publishing from the `pypi` and `testpypi` GitHub
environments; no API token is stored in the repository.

## License

[MIT](LICENSE), copyright 2026 Utrecht University. Campbell Scientific, CRBasic, CR6, CR1000X, CR300,
CR350 and LoggerNet are trademarks of their owners; this project is independent of them.
