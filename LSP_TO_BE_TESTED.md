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

## Local host supplement (2026-10-11, tested revision 8b25214a)

[Full supplementary results and raw evidence](docs/lsp-rounds/host-tests/2026-10-11-8b25214a/RESULTS.md).
This revision includes LSP-02a (`43acb404`) and the help-parser fix (`665b40f7`).

- T-01 for both LSP-01a and LSP-02a is now executed: full pytest passes; four-profile diagnostic
  summaries and every replay case classification are unchanged from the first host run;
  TDF totals and baseline caveats are unchanged; the real-compiler repository verifier passes.
  Known reference faults still produce nonzero oracle gate exits. This is not full parity.
- T-02 remains pending server integration; T-03 remains pending visual confirmation after
  desktop restart. Their concrete first-run outcomes and logs are retained above.

These are revision-specific host findings for the remote coordinator to integrate. Newer
source changes require fresh affected checks; none are implicitly accepted by this record.

### T-04 Language server behaviour in VS Code (LSP-01b), appended 2026-10-11

Prerequisite: T-02 setup. Accept each when observed; record OS/VS Code version.

1. Settings: change `opencrbasic.exclude` / `opencrbasic.debounceMs` in the Settings UI; the server
   re-reads them (output channel or changed behaviour) without restart. Changing
   `opencrbasic.pythonPath` restarts the server; "opencrbasic: Restart Server" works.
2. Problems panel: whole-line ranges; a library included by several testers shows
   `[Entry.CR6]` prefixes; an open `.crb` included by no entry shows the information note.
3. URIs: diagnostics for an unopened included file appear under the same file entry as when the
   file is opened (watch for duplicates). On Windows also with a path containing spaces and a
   non-ASCII character.
4. Workspace: open the repository root; first analysis of all `crbasic/*.CR6` entries finishes;
   note the time from activation to the last publish.
5. Other loggers: a `.CR1X`/`.CR300` program with `opencrbasic.logger` set accordingly.

### Coordinator note (2026-10-11, appended)

The language server (LSP-01b) is on the branch from `a15d2b3`; T-02 and T-04 can run now. Later
revisions add hover/signature/editing logic modules that are not yet wired into the server; host
checks for them will be appended as T-05+ when they are wired.


## Local VS Code host report (2026-10-11, tested implementation 7c450ed5)

[Full results, findings, raw logs and screenshots](docs/lsp-rounds/host-tests/2026-10-11-7c450ed5/RESULTS.md).

- T-02 CR6 scenarios are observed: successful startup with import/API/revision banner, expected
  Main warnings, errors after invalid edits, clearing on undo, merged errors from an unsaved
  ShuntLib overlay, and disk diagnostics restored after Don't Save. Client compile/5 tests pass.
- T-03 visual check is complete in Dark Modern for the fixture, Main and SBDIridiumLib. Saved
  screenshots complement the earlier real tokenizer scope tests. Predefined constants render
  white in this theme despite their correct grammar scope.
- T-04 is partially passing: Settings UI changes reload without restart, exclusion removes/restores
  the tester warnings, restart command works, orphan library information note appears, and a
  space-containing include uses the same URI before/after opening without duplicate entries.
  Last initial nonempty publish was 0.576 s after initialize on an explicit restart; this is not
  an exposed all-entries-complete signal. Windows/Linux were not tested.
- Findings to address/retest: H-01 physical line 32 reported as 33 for the ShuntLib edit; H-02 a
  valid Python-path restart succeeds but raises a configuration-forwarding error toast; H-03
  `.CR300` opens as Plain Text (manual CRBasic selection enables live edits); H-04 a UTF-8 `É`
  include filename becomes mojibake and cannot resolve, while the ASCII space-only control works.
  Exact reproductions and evidence are in the report. These findings do not constitute round
  acceptance and are not silently marked fixed by newer, untested integrations.
- Clean full pytest at 7c450ed5: 21,932 passed, 311 skipped; real-exe verify passed with its
  quoted BUILD line in the report. An interrupted run and a run contaminated by inherited
  auto-save are retained and explicitly excluded from accepted results. Original CRBasic files
  are restored; no hardware was flashed or exercised.

Original requests and remote additions above remain intact. This dated report completes the
macOS visual follow-up and replaces the previous server-absent limitation for the tested revision;
future implementation changes and the remaining platform/findings checks require fresh evidence.

## Local gate supplement (2026-10-11, c0fd9bf1)

[Supplement and raw evidence](docs/lsp-rounds/host-tests/2026-10-11-c0fd9bf1/RESULTS.md): after
pulling the standalone editing/help-rule additions, full host pytest passes **22,048 tests,
311 skipped**; the real-exe repository verifier passes with its exact BUILD line in the record.
The server and client files are unchanged from the VS Code test revision; no additional VS Code
capability or finding fix is inferred. H-01 through H-04 above remain open for the coordinator.

### T-01 amendment (2026-10-11, appended): LSP-05b-core parser attribute

`3769504` adds `stmt.origin` (logical line and segment offset) to every parsed statement in
`parser.py` (additive, positions only). Rerun the T-01 gates on a revision at or after `b096f0f`;
accept when difftest/replay/tdftest counts and the real-exe `verify.py` BUILD line are unchanged
from the `8b25214a` host record. Cloud gates at `3769504`: selfdiff 0 differ, full pytest 21,869
passed / 507 skipped, native verify pass.

