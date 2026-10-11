# CRBasic language server: user guide

How to run the opencrbasic language server in VS Code, what each setting does, which editor
features exist and what a real host has shown. It describes the code at base `3da66c7`.

Sections 2 to 7 describe the implemented behaviour from code and documentation. Only the rows of
the status table in section 8 were observed in a VS Code run, and each names the host record
(revision) it came from. Anything else is marked **untested**. Contracts and per-module limits are
in [lsp-api.md](lsp-api.md).

## 1. Status in brief

- Only macOS (arm64, VS Code 1.141.0) has been tested in an editor. Windows and Linux are
  **untested**.
- Diagnostics, outline, folding, hover, completion, signature help, navigation and syntax
  highlighting have passed provider-level checks on a host. Visual and typing checks are partly
  pending (section 8).
- Rename, formatting and quick fixes exist as core logic but the server does not offer them on this
  base (section 5.10).
- H-02 (a toast when the interpreter path changes) and H-03 (`.CR300` opens as plain text) have code
  fixes in LSP-01e that are **not host-retested**. H-04 (non-ASCII include names) is **open**.

## 2. Install and launch

Requirements: Python 3.10 or newer with [uv](https://docs.astral.sh/uv/); VS Code 1.95 or newer
(`vscode/package.json` `engines`); Node.js with npm, only to build the client.

From the repository:

```bash
cd opencrbasic
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"   # the value opencrbasic/.envrc sets
uv sync                                                      # installs the dev group, which includes pygls
cd vscode && npm ci && npm run compile
```

- `uv sync` installs the `dev` group, which includes pygls (`pyproject.toml`). For a runtime-only
  environment, run `uv pip install -e ".[lsp]"` in an environment you manage. The `lsp` extra is
  `pygls>=2.1.1,<3`.
- Set `opencrbasic.pythonPath` to that environment's interpreter, as an absolute path with `$HOME`
  expanded, for example `/Users/<you>/.uv-envs/opencrbasic/bin/python`. VS Code does not expand
  `$HOME` in settings.
- The client runs from source: open `opencrbasic/vscode/`, press F5 ("Run Extension"), then open a
  `.CR6` file in the Extension Development Host. `vscode/package.json` has no packaging script.
- If the interpreter imports `opencrbasic` from another checkout (a git worktree, for example), set
  `opencrbasic.serverEnv` to `{"PYTHONPATH": "<worktree>/opencrbasic/src"}`.

The server is `python -m opencrbasic.lsp` (or the `crbc-lsp` script after installation), over stdio.
It writes one startup line to stderr before it serves requests:

```
opencrbasic-lsp: opencrbasic=<path> api_version=<N> git=<short sha|unknown>
```

It refuses to start, with exit status 2, when `analysis.API_VERSION` is not the version it was
written for (`lsp-api.md`, Launch). Observed at `7c450ed5`: `api_version=1`, `git=7c450ed5`.

## 3. Settings

Names and defaults come from `vscode/package.json`. "Applied" says how a change reaches the server.

| Setting | Default | Meaning | Applied |
|---|---|---|---|
| `opencrbasic.pythonPath` | `python3` | Interpreter that runs the server | restarts the server |
| `opencrbasic.serverArgs` | `["-m", "opencrbasic.lsp"]` | Arguments after the interpreter | restarts the server |
| `opencrbasic.serverEnv` | `{}` | Extra server environment variables; they override inherited ones (for example `PYTHONPATH`) | restarts the server |
| `opencrbasic.entries` | `["**/*.CR6"]` | Workspace-relative globs of entry programs | sent to the running server |
| `opencrbasic.exclude` | `[]` | Globs removed from the entries | sent to the running server |
| `opencrbasic.logger` | `cr6` | Profile: `cr6`, `cr1000x`, `cr300` or `cr350` | sent; every entry is re-analysed |
| `opencrbasic.encoding` | `utf-8` | Encoding for unsaved text and for files | sent; every entry is re-analysed |
| `opencrbasic.debounceMs` | `300` | Quiet time after an edit before analysis (minimum 0) | sent; later jobs |
| `opencrbasic.analysisTimeoutMs` | `10000` | Limit for one analysis (minimum 1) | sent; later jobs |
| `opencrbasic.helpDirectory` | `""` | Folder of the extracted CRBasic help (section 6) | sent; indexed in the background |
| `opencrbasic.trace.server` | `off` | `messages` or `verbose` trace in the `opencrbasic` output channel | applied at once |

- The server pulls the non-launch settings (`workspace/configuration`) after each change notice.
  Observed at `7c450ed5`: changing `debounceMs` and `exclude` took effect without a restart.
- `featureWaitMs` (default 1500) is a server-side tuning key that the extension does not contribute.
  It bounds how long a feature waits for a fresh analysis (section 5.8).
- An invalid value is ignored with a stderr line and the previous value is kept (`lsp-api.md`,
  Configuration). **Untested** in an editor.

## 4. Entry programs

The server analyses each *entry* together with the files it includes.

- Entries are the files matching `entries` (workspace-relative, case-insensitive globs: `*`, `?`,
  `**`, `{a,b}`) minus `exclude`. Directories whose name starts with `.` and `node_modules` are not
  walked.
- A `.crb` library is never an entry. Its diagnostics come from the entries that include it. When
  several entries contribute to the same file, each message gets an `[EntryName] ` prefix. Observed
  at `7c450ed5`.
- While it is open, a `.CR6` file is an entry of itself, and so is a file whose extension belongs to
  the configured logger (for example `.CR1X` with `cr1000x`). **Untested** in an editor.
- An open `.crb` that no entry includes shows one information note at line 0: "No configured entry
  program includes this file; diagnostics come from the entry programs that include it." Observed at
  `7c450ed5`.
- Changing `entries` or `exclude` re-discovers the entries; a file that drops out loses its
  diagnostics. Observed at `7c450ed5`: excluding `crbasic/SbdPayload_tester.CR6` removed its warnings,
  and removing the exclusion restored them.
- **Repository root:** the default `**/*.CR6` opened at the repository root matches 2,781 `.CR6`
  files: 2,758 in `opencrbasic/tests/corpus/` (fuzz and CHM corpora), 16 in `crbasic/` and 4 in
  `examples/` (counted at `3da66c7`). The cost of analysing all of them is not measured. For the
  station programs, open the folder that contains `crbasic/` and set `entries` to `["crbasic/*.CR6"]`.
  Observed at `7c450ed5`: an explicit restart with the `crbasic/*.CR6` entries gave a last initial
  publication 0.576 s after `initialize`. That is not an all-entries-complete signal.
- The logger comes from `opencrbasic.logger` and never from the file extension. A `.CR300` file with
  the default `cr6` is not an entry; set the logger to `cr300` to analyse it. Observed at `7c450ed5`
  after the language was chosen by hand. The automatic association is LSP-01e, **not host-retested**.
- Files changed outside the editor (for example by `git pull`) are **not watched** on this base. The
  server reads an entry's files when it analyses that entry; restart the server to re-read them
  (`opencrbasic: Restart Server`). File watching is LSP-06a and is not on this base. This is inferred
  from the code: there is no file-watch handler, and every entry is analysed after `initialized`.

## 5. Features

Each feature is described from the code. Its host status follows the description.

### 5.1 Diagnostics

Compiler messages for each entry appear on the line they name, merged across entries, with source
`opencrbasic`. Analysis runs after `debounceMs` of quiet and immediately on open, save, close and
configuration change. The analysis reads the unsaved text of open files, not their disk copies.

- Ranges cover whole physical lines, so a statement continued with `_` is underlined on all its
  lines. There are no column ranges. Line numbers are the compiler's.
- A message whose file cannot be resolved goes to line 0 of the entry, with `<file>:<line>: `
  prepended.
- An internal failure, a timeout (`analysisTimeoutMs`) or a worker crash gives an error at line 0 of
  the entry, source `opencrbasic (internal)`. The next successful analysis clears it.

Host: at `7c450ed5`, an invalid edit gave errors after the debounce, undo cleared them, an unsaved
overlay of a library changed the diagnostics of the entries that include it, and Don't Save restored
the disk diagnostics. At `e77ee5cc` (after LSP-01d), an edit on physical line 32 of `ShuntLib.crb`
covered lines 32 and 33. Before LSP-01d, line 33 alone was marked (`7c450ed5`, H-01).

### 5.2 Hover

Hover on a declared name (`Public`, `Dim`, `Const`, `Sub`, `Function`, `DataTable`, `Alias` and
others) shows its type, units, declaring file and line. Hover on a catalog instruction or function
shows its signature and help. Predefined constants show their value. Nothing is shown in comments or
strings.

- A name declared more than once lists up to five declarations and says the scope is not resolved.
- Hover reads the last analysis, so while you type it can show declarations from before the edit.
- Without help text the hover says why (section 6), in one of two placeholders.

Host: at `1b01311d`, the provider returned VoltSE's title, parameter descriptions and a "Source: help
page" line; a variable hover showed type and source file; there was no result inside a comment or
string; clearing `helpDirectory` gave the no-help placeholder. Visual rendering: **untested**.

### 5.3 Signature help

Triggered by `(` and `,`. Parameter names come from the help only when the help's parameter count
equals the checker's count. Otherwise the parameters are `arg1` to `argN` and the documentation says
the names are not applied. Function argument counts follow the checker's accepted counts, not the
help. For example, FTPClient accepts 6, 10 or 11 arguments. Parameters after the smallest accepted
count are marked optional, which is a simplification (`lsp-api.md`, Hover). The accepted counts come
from 130 real-compiler cases with the argument values recorded at `4d88a186` (host record
`e77ee5cc`); they are not claimed for every argument value.

Host: at `1b01311d`, the active parameter was 0 after `VoltSE(` and 1 after the first comma. Highlight
rendering: **untested**.

### 5.4 Completion

Triggered by `.`, and offered while you type a name. At a prefix it lists program names first (Subs,
variables and constants visible in the enclosing routine), then catalog names (keywords,
instructions, functions and symbols). After `name.` it lists the fields of that table or structure.
The replaced range is the prefix only.

- There is no argument or type context. No items are offered inside a string or after `'`.
- At most 200 items are returned. The server then reports the list as complete
  (`isIncomplete: false`), so the editor filters those 200 items as you type instead of asking again.
  This is inferred from the code and the protocol; **untested** in an editor.

Host: at `1b01311d`, prefix `Vo` listed a program name before the instructions, `Status.` and `Public.`
returned fields, and the replaced range covered only the typed prefix. Typing responsiveness:
**untested**.

### 5.5 Outline and breadcrumbs

The outline lists `BeginProg`, `Scan`, `SlowSequence`, `DataTable` with its outputs, `ConstTable`,
`StructureType`, `Sub` and `Function` with their parameters, and the file-level `Public`, `Dim`,
`Const` and `Alias` declarations.

- Only code the compiler reads is listed: inactive `#If` branches and text after `EndProg` are not.
  Declarations inside a `Sub` are not listed.
- A `.crb` shared by several entries shows the view of the first entry (sorted by path).
- A file included twice appears once per load.

Host: at `1b01311d`, the outline of `Main` and of `ShuntLib` was non-empty. Breadcrumb layout:
**untested**.

### 5.6 Folding

Folds cover block statements (for example `If`, `Select`, `Sub`, `Scan`, `For`, `Do`), `#If` to
`#EndIf` and runs of two or more comment lines. Folds are computed from the buffer text.

- `Case` branches do not fold. `#If` conditions are not evaluated, so inactive branches fold too.
- A `SlowSequence` with no `EndSequence` ends at the next `SlowSequence` or at `EndProg`. The compiler's
  own rule for this was not observed.

Host: at `1b01311d`, a directive block and a comment run folded. Chevron display: **untested**.

### 5.7 Include links

Each `Include` and `IncludeSection` file name is a document link, and it has a target only when the
compiler resolved the file. Matching is case-insensitive, as the compiler's is. Opening the target
resolves the library (`1b01311d`, provider level). Ctrl-click, or Cmd-click on macOS, and the
underline: **untested** in an editor. Non-ASCII names are in section 7.

### 5.8 Go to definition, find references, highlights

These use the binding index: F12 goes to a declaration, including one in a `.crb`; Shift+F12 lists
references; occurrence highlights mark reads and writes.

- An ambiguous name shows every candidate.
- Catalog and predefined names (instructions, functions, `Status`, `Public`, `NAN`) have no definition
  location, but their references are listed.
- Names in strings, names in inactive `#If` branches, names after `EndProg` and derived table fields
  (for example `TC_Avg` from `Source`) are not resolved.
- While an edit is pending, a request that needs the program waits for a fresh analysis: up to
  `featureWaitMs` (1.5 s) when an older analysis exists, and up to the analysis timeout plus 2 s when
  none exists. After that it answers from the last analysis, so positions can be off until the new
  analysis lands. Text-based requests (completion, hover, signature help, folding) never wait.

Host: at `1b01311d`, a use of `Shunt_Current_A` in `Main` resolved to `ShuntLib`, and references and
highlights were non-empty. Visual highlight rendering: **untested**.

### 5.9 Semantic tokens and syntax highlighting

- Semantic tokens colour declared names by kind: variables, constants (read-only), parameters,
  subs and functions, structure members and tables. A name that is only referenced gets no semantic
  token.
- The TextMate grammar (`vscode/syntaxes/crbasic.tmLanguage.json`) colours comments, strings with `""`,
  numbers, directives, keywords, instructions, built-in functions, predefined constants and operators.
  It is generated from the CR6 catalog and uses the CR6 names whatever `opencrbasic.logger` is set to.
  Do not edit it by hand; see [lsp-api.md](lsp-api.md), "Generated TextMate grammar".

Host: at `602f6462`, 12 real TextMate/Oniguruma scope checks passed. At `7c450ed5` the highlighting was
inspected visually in Dark Modern: comments, strings, numbers, keywords and instructions had distinct
colours. Predefined constants render white in that theme. At `1b01311d`, semantic tokens were
non-empty before and after an edit. Repainting after an edit without a keystroke: **untested** in
an editor.

### 5.10 Not offered by the server on this base

Rename, indentation formatting and quick fixes exist as core modules with their own tests (LSP-07a,
LSP-07b, LSP-07c; [lsp-api.md](lsp-api.md)), but the server does not register their handlers. VS Code
shows no rename, format or quick-fix action. Code actions, inlay hints, pull diagnostics and server
commands are also not offered. **Untested** in an editor.

## 6. Help directory

`opencrbasic.helpDirectory` names a folder holding `Instructions/*.htm` and `parameters/*.htm`, the
layout of the extracted CRBasic help. In this repository that is `extracted_chm/` at the repository
root. A relative path is resolved against the first workspace folder, so with the workspace folder
`opencrbasic/` use `../extracted_chm`.

- The index is built once, in a background thread, when the setting is applied. The `extracted_chm`
  index took about 0.5 s on the cloud measurement host (LSP-wire handoff). Changing the setting needs
  no re-analysis.
- An empty value gives `Help: not configured (No help directory configured).` on hover and in signature
  documentation.
- A name with no page gives `Help: still to do (No help page for this name).`
- Help changes no checking rule. The compiler's instruction table stays authoritative.
- The help is Campbell Scientific documentation, used for internal purposes under
  [legal.md](legal.md). The extension does not bundle it.

Host: see the hover row in section 8 (`1b01311d`).

## 7. Encoding and file names

- The compiler reads source bytes as Latin-1. Unsaved text is encoded with `opencrbasic.encoding`
  before analysis, so a saved file and its unsaved text give the same analysis when the setting
  matches the file's encoding. When some characters cannot be represented, the document gets one
  warning at line 0 (source `opencrbasic (server)`).
- Positions use the editor's negotiated encoding (UTF-16 in VS Code by default). The server converts
  from byte columns of the document's text. Observed at `1b01311d`: after a string earlier on the line that holds
  a non-BMP character (U+1F600, a surrogate pair in UTF-16), the use of a later name still resolved,
  and an error at the end of that line ended at the UTF-16 line end.
- **Non-ASCII include names (H-04, open).** At `7c450ed5` a UTF-8 file name, `É space.crb`, was not
  resolved: the diagnostic shows mojibake (`Cannot open include file  Ã\u0089 space.crb.`). The
  compiler's own behaviour, observed on Wine at `e77ee5cc` (T-05): a UTF-8 name in the source gives a
  cannot-open warning with exit status 0, and the Latin-1 byte name resolves. The editor's display of
  the message text is LSP-01f, which is not on this base.
- Names with spaces resolve, and one file has one URI whether or not it is open (`7c450ed5`).

## 8. Host-tested status

Each record applies to its own revision only. Later changes, such as LSP-01e, are untested unless a
row says otherwise.

| Area | Observed | Record |
|---|---|---|
| Server starts in the Extension Development Host; startup line in the output channel | Yes, with `api_version=1` and the revision | [7c450ed5](lsp-rounds/host-tests/2026-10-11-7c450ed5/RESULTS.md) |
| Startup line logged at error level in the channel | Yes; a success shows as an error | `7c450ed5` |
| Info copy of the startup line (LSP-01e) | **Untested** | - |
| `Main.CR6`: no errors, expected tester warnings | Yes | `7c450ed5` |
| Invalid edit: errors after debounce; undo clears them | Yes | `7c450ed5` |
| Unsaved library overlay merges diagnostics; Don't Save restores them | Yes | `7c450ed5` |
| Library edit on physical line 32 covers lines 32 and 33 | Yes (fixed by LSP-01d; was line 33) | [e77ee5cc](lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md) |
| `debounceMs` and `exclude` change without restart | Yes | `7c450ed5` |
| `opencrbasic: Restart Server` | Yes | `7c450ed5` |
| Interpreter path change restarts the server | Yes, new server starts; an error toast also appears (H-02) | `7c450ed5` |
| Interpreter path change without the toast (LSP-01e) | **Untested** | - |
| `.CR300` opens as CRBasic automatically (LSP-01e) | **Untested**; at `1b01311d` and `7c450ed5` it opened as plain text (H-03) | [1b01311d](lsp-rounds/host-tests/2026-10-11-1b01311d/RESULTS.md) |
| Open `.crb` with no entry shows the information note | Yes | `7c450ed5` |
| Include name with a space: one URI, opened or not | Yes | `7c450ed5` |
| UTF-8 non-ASCII include name | Not resolved; mojibake in the message (H-04, open) | `7c450ed5` |
| Syntax highlighting, scope checks | 12 of 12 pass | [602f6462](lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md) |
| Syntax highlighting, visual check, Dark Modern | Yes | `7c450ed5` |
| Help hover, variable hover, completion, signature help, outline, folding, links, navigation, semantic tokens, Unicode, after-edit update (provider and API level) | 16 checks pass; the `.CR300` association check fails | [1b01311d](lsp-rounds/host-tests/2026-10-11-1b01311d/RESULTS.md) |
| Same, visual rendering, repaint, typing responsiveness | **Untested** (the record lists them as pending) | - |
| Benchmark on an idle macOS host: hover p95 0.856 ms, completion p95 3.948 ms (in-process core calls); diagnostics p95 133.740 ms after the debounce, from an end-to-end edit-to-publish of 433.740 ms (20 edits) | Measured once; not a guarantee | `e77ee5cc` |
| Windows and Linux | **Untested** | - |
| Rename, formatting, quick fixes | Not offered by the server; core only | - |
| File watching of files changed outside the editor | Not on this base (LSP-06a) | - |

## 9. Known limits

- One platform observed (macOS). Windows and Linux are untested.
- Outside-editor changes are not seen until the server restarts or the entry is analysed again.
- Completion stops at 200 items, reported as complete (section 5.4).
- Ranges are whole lines. There are no token or column ranges for diagnostics or hover.
- Results during an edit can be stale (section 5.8). A timeout restarts the worker pool; the entry
  shows an error until its next successful analysis.
- The full per-module limits are in [lsp-api.md](lsp-api.md).

## 10. Troubleshooting

- **Output channel.** Open View > Output and choose `opencrbasic`. The first line is the startup line
  (section 2). Trace is set by `opencrbasic.trace.server`.
- **`No module named opencrbasic.lsp`** (observed at `602f6462`, when the server was absent). Point
  `pythonPath` at an environment where `uv sync` ran, or set `serverEnv.PYTHONPATH`.
- **`refusing to start`, exit status 2.** The package's `API_VERSION` is not the server's. Use one
  checkout for both.
- **Server failed to start** (error message). Check `pythonPath`. Run `<pythonPath> -m opencrbasic.lsp`
  in a terminal: the startup line should appear on stderr, then the process waits for input. Stop it
  with Ctrl-C.
- **Toast after changing `pythonPath`** (H-02, observed at `7c450ed5`). The LSP-01e fix is not
  host-retested. If the new server did not start, run `opencrbasic: Restart Server`.
- **`.CR300` opens as plain text** (H-03). Choose CRBasic from the language picker; set `logger` to
  `cr300` to see its diagnostics. The automatic association is LSP-01e, not host-retested.
- **No diagnostics for a file.** Check that the file is an entry (section 4). The information note
  tells you when a `.crb` is not included by any entry.
- **Stale results for a file changed outside the editor.** Run `opencrbasic: Restart Server`.

## 11. Where the details live

- [lsp-api.md](lsp-api.md): contracts, provenance and known limits for each module.
- [LSP_TO_BE_TESTED.md](../LSP_TO_BE_TESTED.md): the host checks still pending.
- [LSP_PROGRESS.md](../LSP_PROGRESS.md): the round ledger.
