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

### T-02 amendment (2026-10-11, appended): H-01 after LSP-01d

On a revision at or after `b21c1e1`, repeat the H-01 edit (ShuntLib physical line 32,
`Public Shunt_mV` -> `Public =`). Expected: the errors' range covers physical lines 32-33 (the
loader pulls line 33 into the `Public =` declaration; the compiler message echoes
`Public = Public Shunt_Current_A`). A different line or a single-line range is a failure.

### T-05 Non-ASCII include names with the real compiler (H-04), appended 2026-10-11

Real-compiler evidence needed before any change. With Wine and `compiler/CR6Comp.exe`, in a
scratch directory containing `É space.crb` (a minimal valid library) run CR6Comp on:
1. `Uri.CR6` saved as UTF-8 (`IncludeSection`/`Include` naming `É space.crb`, bytes C3 89),
2. the same program saved as Latin-1/cp1252 (byte C9),
and record stdout/exit for each, plus `crbc` (opencrbasic) on the same files. Also note how the
include file name is stored on disk (macOS NFD vs NFC). Save the raw outputs under a new host-tests
directory. Acceptance is the record itself; the coordinator decides the behaviour from it.

### T-06 Indentation-sensitive message (LSP-07b observation), appended 2026-10-11

opencrbasic gives a different first error for `tests/corpus/chm/xmlparse_1.CR6` when its prose line
`data from the xml file ...` is indented ("Illegal Data parameter") than when it is not ("Invalid
symbol in expression"). Run the real CR6Comp on the original file and on a copy whose lines are
indented by two spaces (opencrbasic: `uv run --no-sync python -c "from opencrbasic import
formatting as f; import sys; t=open(sys.argv[1],encoding='latin-1').read();
open(sys.argv[2],'w',encoding='latin-1').write(f.apply_edits(t, f.format_indentation(t)))"
tests/corpus/chm/xmlparse_1.CR6 /tmp/xmlparse_1_indented.CR6`). Record both real outputs and
`crbc` outputs. If the real compiler's message does not change with indentation, this is a
compiler-workstream defect; the formatter refuses files with errors either way.

### T-07 Benchmarks on an unloaded host (LSP-08a), appended 2026-10-11

The cloud run ([LSP-08a-bench-cloud.md](docs/lsp-rounds/LSP-08a-bench-cloud.md)) shared 4 CPUs with
five other workers (load average up to 9), so its diagnostics latency (server didChange -> publish
p95 931 ms after the 300 ms debounce; LSP_PLAN.md target 500 ms) is inconclusive. On the macOS
host with nothing else running: `cd opencrbasic && uv run --no-sync python tools/lsp_bench.py
--json scratch/lsp/bench-host.json --label "macOS host, idle" > docs/lsp-rounds/LSP-08a-bench-<host>.md`
(the tool records host, load and revision). Record whether hover/completion p95 < 100 ms and
diagnostics p95 < 500 ms after debounce hold. Measurement only; no acceptance threshold is
asserted.

### T-08 Builtin function argument counts with the real compiler (LSP-04d), appended 2026-10-11

Real CR6Comp only. For each of `EmailSend`, `EmailRecv`, `UDPDatagram`, `XMLParse`, `HTTPGet`,
`UDPOpen`, `TCPOpen`, `HTTPPost`, `CheckSum`, compile `Result = F(a1, ..., an)` inside a minimal
valid program for every n from 0 to (help parameter count + 1), with well-typed arguments (string
variables/literals and Long variables as the help describes). Save as a fixture in the style of
`tests/fixtures/ftpclient_arity.json` (use `tools/probe.py` with a `$BODY$` template and a cases
file; `scratch/mkfixture.py` if present), audit it with `tools/fixture_audit.py`, and record the
accepted counts per function. This feeds signature help (display only) and the compiler's own
arity checks.

### T-09 Language features in VS Code (LSP-wire), appended 2026-10-11

On a revision at or after the LSP-wire integration (see LSP_PROGRESS.md ledger), with T-02 setup
and `opencrbasic.helpDirectory` set to the repository's `extracted_chm`. Accept each when observed
(screenshot or log excerpt); the detailed expectations are in
[LSP-wire-handoff.md](docs/lsp-rounds/LSP-wire-handoff.md) "Needs VS Code host testing":

1. Hover on `VoltSE` shows help title, parameters and "Source: help page"; without the setting,
   "No help directory configured."; hover on a `Public` shows type/units/file:line; nothing in
   comments/strings.
2. Completion: prefix `Vo` in a Scan body lists program names then instructions; `Status.` and
   `Public.` list fields; accepting replaces only the prefix.
3. Signature help: `VoltSE(` then `,` moves the highlighted parameter.
4. Outline/breadcrumbs for `Main.CR6` and an opened `.crb`; folding chevrons (blocks, `#If`,
   comment runs); include file names underlined and Ctrl-click opens them.
5. F12 from `Main.CR6` to a name declared in a `.crb`; Shift+F12 lists references; occurrence
   highlights.
6. Semantic tokens colour declared names and settle after an edit without another keystroke.
7. Non-ASCII (`é😀` in a string earlier on the line): features land on the right token;
   diagnostic underlines end at the line end.
8. No UI freeze while typing in `Main.CR6`; outline/tokens update about 1 s after the last edit.

## Local host report (2026-10-11, e77ee5cc / 4d88a186 / 1b01311d)

[Oracle, range, encoding, indentation, benchmark and arity evidence](docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md).
[Integrated-feature retest and actual VS Code API observations](docs/lsp-rounds/host-tests/2026-10-11-1b01311d/RESULTS.md).

- T-01 at `e77ee5cc`: differential group totals, all replay classifications and TDF totals match
  the earlier host baseline; real-exe verify passes. Full pytest exposed four failures from
  benchmark import clearing strict mode (H-05), independently reproduced in the report.
  At `1b01311d`, after the coordinator's fix, full pytest passes **22,182 tests, 311 skipped**;
  client compile/10 tests and real-exe verify also pass. Exact BUILD lines are in the records.
- T-02 H-01 amendment: actual VS Code host API confirms all six merged ShuntLib errors cover
  physical lines 32–33, with unchanged disk bytes, and clear on undo.
- T-05: both real and native compilers resolve the Latin-1 filename; UTF-8 source produces a
  cannot-open warning despite exit 0. NFC filename bytes, valid Include/IncludeSection probes
  and raw outputs are saved. An initial malformed IncludeSection fixture is explicitly excluded.
- T-06: both compilers give the same line-78 message sequence before/after the requested
  formatting. The earlier reported native difference did not reproduce through `crbc`.
- T-07: no concurrent test jobs; hover/completion/diagnostics p95 meet the proposed targets
  on this host. Load and end-to-end versus in-process measurement limits are recorded.
- T-08 at `4d88a186`: 130 real-compiler cases saved in a fixture; audit gives **0 differing cases**.
  Accepted counts and exact argument variants are tabulated in the first report.
- T-09 at `1b01311d`: 16 actual VS Code provider/API checks pass. The extra `.CR300` association
  check still reports Plain Text (H-03). Visual rendering, automatic repaint, typing responsiveness,
  H-02 and Windows/Linux checks remain unverified; the supplement names the observation limits.

Earlier requests and reports remain intact for coordinator integration. Results apply only to
their named revisions; no later code or round acceptance is implied. No hardware was contacted.

### T-04 amendment (2026-10-11, appended): LSP-01e client fixes

On a revision with LSP-01e (`codex/lsp-01-client` integrated, see ledger): (1) H-02: change
`opencrbasic.pythonPath` between two valid interpreters; expect no `Sending notification ...
failed` / `Starting server failed` lines and no error toast, and the new server starts. (2)
Changing `debounceMs`/`exclude` with the server running still applies without restart. (3) H-03:
`.CR300`, `.CR1X`, `.CR350` files open as CRBasic (with logger `cr6` a `.CR300` gets the
"no configured entry" note; with logger `cr300` it gets live diagnostics). (4) The startup line
appears in the output channel as an info log message (record whether the stderr copy still shows
at error level).

### Coordinator integration note (2026-10-11, appended)

Integrated from the host records through `1b01311d`: H-01, H-05, T-01, T-03, T-07, T-08 and the
T-09 provider checks are accepted at their tested revisions; T-06 closed as not reproduced; T-05
shows parity of include resolution (display follow-up LSP-01f). Still requested: T-04 amendment
(LSP-01e: H-02 toast, H-03 association, banner), T-09 visual checks, Windows/Linux.

