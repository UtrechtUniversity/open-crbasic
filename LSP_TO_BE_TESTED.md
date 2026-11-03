# CR6 language server: to be tested

Checks that the cloud container cannot run: it has no Wine/reference compilers and no VS Code
download. Each item says what to run and what result accepts it. When one is done, move it to
"Done" with the date, host, revision and observed result, and link the saved evidence. Design and
acceptance criteria: [LSP_PLAN.md](LSP_PLAN.md); live state: [LSP_PROGRESS.md](LSP_PROGRESS.md).

Environment for the Python checks (from the repository root): `export WINEPREFIX=$HOME/.wine-cr6
WINEDEBUG=-all`, `uv sync`.

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

### T-02 VS Code extension launch (LSP-01c/01b)

On macOS (and Windows/Linux when available), VS Code with Python 3.15+ and this checkout:

1. `uv sync` (installs pygls into the dev environment).
2. `cd ../open-crbasic-vsix && npm ci && npm run compile`.
3. Open the open-crbasic-vsix checkout in VS Code, run "Run Extension" (F5) with the extension host
   opening `tests/corpus/station/`; set `opencrbasic.pythonPath` to this repository's `.venv` interpreter.
4. Accept when: the "opencrbasic" output channel shows the server's startup line (import path,
   API version, revision); opening `Main.CR6` shows no errors (only the warnings
   `crbc` prints, e.g. `Table Shunt_Data, is declared but never called.`); typing an invalid
   line shows an error on that line after the debounce; undoing it clears it; editing
   `ShuntLib.crb` without saving updates `Main.CR6`'s and the testers' diagnostics;
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

### T-19 Windows/WSL re-checks after LSP-R6, appended 2026-10-16

Re-check on Windows and Remote-WSL after LSP-R6. Inputs:
H-11..H-17 and Workspace Trust,
H-18..H-21 and PanelTemp hover.
The macOS checks of the fixes do not establish Windows/WSL behaviour.

1. **H-11 (Windows):** run the server-marked tests and `tools/lsp_bench.py` with a silent client,
   without a keep-alive shim. Accept completed analyses and benchmark results without timeouts.
2. **H-12 (Windows):** run server tests that kill their client; teardown must succeed and leave no
   server or analysis-worker processes behind.
3. **H-13 (Windows):** run `tools/lsp_install_check.py`; fresh environments must use Windows
   `Scripts` executables and pass the install/stdio checks without host shims.
4. **H-15 (Windows):** run
   `tests/lsp/test_features_positions.py::test_ctx_clamps_a_line_past_the_end_and_converts_back`;
   the portable absolute-path fixture must pass.
5. **H-16 (Windows):** check diagnostics for unopened entries with a root URI using the client's
   drive-letter case and colon escaping. Published URIs must follow the client's spelling;
   opening the same file must not create a second Problems collection.
6. **H-18 (Remote-WSL):** change `opencrbasic.pythonPath` several times between valid interpreters,
   both while idle and immediately after an edit. Accept a new startup banner and diagnostics
   each time, with no provider-request error toast (especially semantic tokens).
7. **H-19 (Windows and Remote-WSL):** Show Compiler Output for `Main.CR6`; the tab title must be
   `Main.CR6 compiler output.txt`, with no leading backslash. Check Show Table Fields too.
8. **H-20 (Windows and Remote-WSL):** with default editor settings and `helpDirectory` configured,
   all 9 VoltSE parameter-name hints must be drawn, including `fN1:`, `Mult:` and `Offset:`.
9. **Workspace Trust:** in an untrusted folder, the Extensions view must show the description
   explaining that CRBasic runs a Python language server from the configured interpreter.
   `.CR6` remains Plain Text until the folder is trusted; trusting enables the extension.
10. **PanelTemp hover:** hover its `fN1` parameter help; the filter list must appear after
    "common options for filtering noise are:".

**T-17/H-21 clarification:** the earlier "popup is empty" wording describes the server result,
not the screen. Before first analysis, an empty automatic `.` result for `Status.`/`Public.`
shows no popup. The next letter shows fields once analysis has finished. Ctrl+Space shows
"Loading...", then VS Code's word-based suggestions. One Remote-WSL Status list appeared by
itself, so automatic appearance is possible but not guaranteed. **T-16 item 8 clarification:**
Problems entries can remain for about three minutes after closing the out-of-glob file's tab,
until VS Code sends `didClose`.


## Done
