# CR6 language server: to be tested

Checks that the cloud container cannot run: it has no Wine/reference compilers and no VS Code
download. Each item says what to run and what result accepts it. When one is done, move it to
"Done" with the date, host, revision and observed result, and link the saved evidence. Design and
acceptance criteria: [LSP_PLAN.md](LSP_PLAN.md); live state: [LSP_PROGRESS.md](LSP_PROGRESS.md).

Environment for the Python checks (from `opencrbasic/`): `export
UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all`,
`uv sync`.

## Pending

### T-01 Oracle gates after the shared driver change (LSP-01a)

The source-provider refactor touches `source.py`, `parser.py`, `signature.py` and
`compiler.py`. In the cloud it was gated by `tools/selfdiff.py` (opencrbasic output unchanged)
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

## Done

None yet.
