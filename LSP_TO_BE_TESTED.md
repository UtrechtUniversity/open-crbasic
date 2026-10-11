# CR6 language server: to be tested

Checks that the cloud container cannot run: it has no Wine/reference compilers and no VS Code
download. Each item says what to run and what result accepts it. When one is done, move it to
"Done" with the date, host, revision and observed result, and link the saved evidence. Design and
acceptance criteria: [LSP_PLAN.md](LSP_PLAN.md); live state: [LSP_PROGRESS.md](LSP_PROGRESS.md).

Environment for the Python checks (from `opencrbasic/`): `export
UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all`,
`uv sync`.

## Pending

### T-01 Oracle gates after the shared driver/loader changes (LSP-01a, LSP-02a)

The source-provider refactor (LSP-01a) touches `source.py`, `parser.py`, `signature.py` and
`compiler.py`; the provenance/line-map recording (LSP-02a) touches `source.py` and `parser.py`. In the cloud it was gated by `tools/selfdiff.py` (opencrbasic output unchanged)
and the fixture-backed pytest suite only.

- `uv run --no-sync pytest -q` with Wine available: no failures; skips drop to the Wine-less
  count recorded in PROGRESS.md (311).
- `for l in cr6 cr1000x cr300 cr350; do uv run --no-sync tools/difftest.py --logger $l --group all; done`:
  same exact/verdict/mismatch counts as PROGRESS.md "Status".
- `uv run --no-sync tools/fuzz_replay.py --json scratch/replay-lsp01.json`: same class counts as
  PROGRESS.md (1,843 exact, 4 false accepts, 0 false rejects, ...).
- `uv run --no-sync tools/tdftest.py --group all`: unchanged.
- `cd ../scripts && uv run verify.py` with the real exe: `CRBasic 16/16 compiled`.

### T-02 VS Code extension launch (LSP-01c/01b)

On macOS (and Windows/Linux when available), VS Code with Python 3.10+ and this checkout:

1. `cd opencrbasic && uv sync` (installs pygls into the dev environment).
2. `cd vscode && npm ci && npm run compile`.
3. Open `vscode/` in VS Code, run "Run Extension" (F5) with the extension host opening the
   repository root; set `opencrbasic.pythonPath` to `$HOME/.uv-envs/opencrbasic/bin/python`.
4. Accept when: the "opencrbasic" output channel shows the server's startup line (import path,
   API version, revision); opening `crbasic/Main.CR6` shows no errors (only the warnings
   `crbc` prints, e.g. `Table Shunt_Data, is declared but never called.`); typing an invalid
   line shows an error on that line after the debounce; undoing it clears it; editing
   `crbasic/ShuntLib.crb` without saving updates `Main.CR6`'s and the testers' diagnostics;
   closing the edited file without saving restores the disk diagnostics.
5. Record the VS Code version, OS, revision and screenshots/log excerpts.

### T-03 Syntax highlighting (LSP-03a)

With the extension running as in T-02, open `crbasic/Main.CR6`, `crbasic/SBDIridiumLib.crb` and a
scratch file with the lines below. Accept when: `'` comments are comment-coloured to end of line
but an `'` inside a string is not; `"a""b"` is one string; `&H1F`, `&B101`, `.5`, `1.5E3` are
numbers; `#If`/`#EndIf` are directives; `If`/`Then`/`EndIf`/`Sub`/`Public`/`As` are keywords;
`VoltSE`, `CallTable`, `PortSet` are instruction-coloured; `Chr$(65)` and `Sqr(x)` are builtin
functions; `mV5000`, `ComC1` are constants; `Tmp.VoltSE`, `Scan1`, `MyIf` are **not** highlighted
as instructions/keywords; nothing carries over to the next line after an unterminated string.
Record the theme used and a screenshot.

```
Public s As String = "it's ""quoted""" ' comment with "quote"
Const H = &H1F + &B101 + .5 + 1.5E3
#If H > 0 Then
VoltSE(Tmp, 1, mV5000, 1, 1, 0, 15000, 1, 0) : s = Chr$(65)
#EndIf
x = Tmp.VoltSE + Scan1 + MyIf
s = "unterminated
```

## Done

None yet.


## Local host report (2026-10-11, implementation revision 602f6462)

[Full results and saved evidence](docs/lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md).

- T-01 for LSP-01a was executed: pytest and the real-compiler repository verifier pass;
  differential/replay counts match the recorded compiler baseline. Known oracle faults still
  give nonzero exits. TDF coverage-label and missing/empty LST caveats are in the record.
  LSP-02a was added to T-01 after this run and still requires its own measured revision.
- T-02: client build and five tests pass; real extension-host activation reaches Python, which
  reports `No module named opencrbasic.lsp`. The server is absent at the tested revision.
  Server-dependent acceptance remains pending.
- T-03: 12 real TextMate/Oniguruma scope checks pass, including both requested repository
  files. Visual colour/screenshot acceptance remains pending after the desktop app restart.

The existing pending requests above are preserved for newer integrations. This report does
not advance the cloud coordinator's round ledger or claim acceptance of untested revisions.
