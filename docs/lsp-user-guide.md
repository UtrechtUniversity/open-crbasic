# CRBasic language server: user guide

How to run the opencrbasic language server in VS Code, what each setting does, which editor
features exist and what a real host has shown. It describes the integrated state after LSP-08d (test tiers), LSP-08e (type checking), LSP-08f
(VSIX packaging) and LSP-09a-c (inlay hints, output commands), with the review-round-3 fixes (LSP-R3).

Sections 2 to 7 describe the implemented behaviour from code and documentation. Only what section 8
lists was observed in a VS Code run, and each row names the host record (revision) it came from.
Anything else is marked **untested**. "Stdio-tested" means a test drives
the real server over stdio with a test client (`tests/lsp/`); that is not an editor run. Contracts
and per-module limits are in [lsp-api.md](lsp-api.md).

## 1. Status in brief

- Only macOS (arm64, VS Code 1.141.0) has been tested in an editor, at the revisions named in
  section 8. Windows and Linux are **untested**.
- Diagnostics, outline, folding, hover, completion, signature help, navigation, semantic tokens and
  syntax highlighting passed provider or API checks in a VS Code Extension Development Host at
  `1b01311d`. Watched files, workspace folders, the size bound, the completion cap, rename,
  formatting, quick fixes, the display of compiler messages, the `.CR300` association and the
  interpreter change were run at API or protocol level in the same host at `7d6db3b1` and
  `679f2f70` (the client's provider commands, workspace edits, configuration updates and traces).
  Visual checks (F2 box, Ctrl+. menu, toasts, Problems and hover rendering, themes, Explorer clicks,
  repainting and typing) are still pending.
- **Not host-tested at all:** the parameter-name inlay hints, the output commands (LSP-09c; request
  T-15), the packaged `.vsix` (LSP-08f; request T-14) and the changes since `679f2f70`, including
  the review-round-3 fixes. Their checks are unit and stdio tests.
- The review findings of round 2 are fixed (section 9); round 3 is in progress.

## 2. Install and launch

Requirements: Python 3.10 or newer with [uv](https://docs.astral.sh/uv/) (`pyproject.toml`,
`requires-python`); VS Code 1.95 or newer (`vscode/package.json`, `engines`); Node.js with npm, only
to build the client. The macOS host run at `1b01311d` used Node 26.0.0 and npm 11.12.1.

From the repository:

```bash
cd opencrbasic
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"   # the value opencrbasic/.envrc sets
uv sync                                                      # installs the dev group, which includes pygls
cd vscode && npm ci && npm run compile
```

- `uv sync` installs the `dev` group, which includes pygls (`pyproject.toml`). For a runtime-only
  environment, install the `lsp` extra (`pygls>=2.1.1,<3`) into an environment you manage.
- Set `opencrbasic.pythonPath` to that environment's interpreter, as an absolute path with `$HOME`
  expanded, for example `/Users/<you>/.uv-envs/opencrbasic/bin/python`. VS Code does not expand
  `$HOME` in settings.
- Run from source: open `opencrbasic/vscode/`, press F5 ("Run Extension"), then open the repository
  root in the Extension Development Host. The launch configuration (`vscode/.vscode/launch.json`)
  passes only the extension path, so the host window opens without a folder. The repository's
  `.vscode/settings.json` (section 3) applies only when the repository root is open. `vscode/`
  has `compile`, `watch`, `test` and `package` scripts (`npm run package` builds the `.vsix`; see
  [../vscode/README.md](../vscode/README.md)).
- If the interpreter imports `opencrbasic` from another checkout (a git worktree, for example), set
  `opencrbasic.serverEnv` to `{"PYTHONPATH": "<worktree>/opencrbasic/src"}`.

The server is `python -m opencrbasic.lsp` (or the `crbc-lsp` script after installation), over stdio.
It writes one startup line to stderr before it serves requests:

```
opencrbasic-lsp: opencrbasic=<path> api_version=<N> git=<short sha|unknown>
```

The same line is also sent once as an Info log message after initialization, so the output channel
shows it at Info level (LSP-01e; observed at `7d6db3b1`). It refuses to start, with exit status 2,
when `analysis.API_VERSION` is not the version it was written for (`lsp-api.md`, Launch). Observed at
`7c450ed5`: `api_version=1`, `git=7c450ed5`.

Installed package: `tools/lsp_install_check.py` builds the wheel, installs it into fresh environments
outside the repository, and checks `crbc`, `crbc-lsp` and the core import without pygls. It passed 15 of 15
checks on a cloud Linux VM with Python 3.13.16 ([LSP-08c-install-cloud.md](lsp-rounds/LSP-08c-install-cloud.md))
and on macOS at `7d6db3b1` and `679f2f70` (host records in section 8). Windows is **untested**, and the
Linux run is not a host record.

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
| `opencrbasic.inlayHints.parameterNames` | `true` | Parameter names before call arguments (section 5.11); needs `helpDirectory` | sent; hints refresh |
| `opencrbasic.trace.server` | `off` | `messages` or `verbose` trace in the `opencrbasic` output channel | applied at once |

- The server pulls the non-launch settings (`workspace/configuration`) after each change notice.
  Observed at `7c450ed5`: changing `debounceMs` and `exclude` took effect without a restart.
- An invalid value is ignored with a log line and that key keeps its default or initialization-option
  value (`lsp/config.py`, `lsp-api.md`, Configuration). Observed in an editor only for `maxFileBytes` (a warning from
  `opencrbasic (server)` at `679f2f70`). Every reload rebuilds the configuration from the defaults,
  the initialization options and the pulled settings, so a setting removed from `settings.json`
  returns to its default without a restart.
- Tuning keys. Since LSP-01g the extension contributes `maxFileBytes`, `maxQueuedJobs` and
  `maxCacheMb` (Settings editor, `initializationOptions` and the configuration pull);
  `featureWaitMs` stays internal (not contributed; the server still reads it from the
  `opencrbasic` section). The numeric settings are integers. The three contributed keys reached the
  server in the editor at `572e54d4` (startup and API updates).

  | Key | Default | Meaning |
  |---|---|---|
  | `featureWaitMs` | 1500 | How long a feature waits for a fresh analysis (section 5.8) |
  | `maxFileBytes` | 2097152 | A file larger than this is not analysed (section 4) |
  | `maxQueuedJobs` | 64 | Entries with an analysis running at once; the rest wait |
  | `maxCacheMb` | 256 | Parsed programs kept for the language features |

- **Repository settings.** The repository's `.vscode/settings.json` sets `opencrbasic.entries` to
  `["crbasic/*.CR6"]` and `opencrbasic.helpDirectory` to `"extracted_chm"` (a human decision,
  commit `f306982`). The help path is relative to the first workspace folder, so it resolves when the
  repository root is the workspace folder.

## 4. Entry programs

The server analyses each *entry* together with the files it includes.

- Entries are the files matching `entries` (workspace-relative, case-insensitive globs: `*`, `?`,
  `**`, `{a,b}`) minus `exclude`. Directories whose name starts with `.`, `node_modules` and `__pycache__` are
  not walked. Discovery runs at startup, on a configuration change, on a workspace-folder change and when
  a program file is created or deleted (see "Files changed outside the editor" below).
- A `.crb` library is never an entry. Its diagnostics come from the entries that include it. When
  several entries contribute to the same file, each message gets an `[EntryName] ` prefix. Observed
  at `7c450ed5`.
- While it is open, a `.CR6` file is an entry of itself, and so is a file whose extension belongs to
  the configured logger (for example `.CR1X` with `cr1000x`). Observed at `679f2f70` for `.CR300`, `.CR1X`
  and `.CR350`.
- An open `.crb` that no entry includes shows one information note at line 0: "No configured entry
  program includes this file; diagnostics come from the entry programs that include it." Observed at
  `7c450ed5`.
- Changing `entries` or `exclude` re-discovers the entries; a file that drops out loses its
  diagnostics. Observed at `7c450ed5`: excluding `crbasic/SbdPayload_tester.CR6` removed its warnings,
  and removing the exclusion restored them.
- **The repository root.** The default `**/*.CR6` matches 2,781 `.CR6` files at base `810b8f9`:
  2,758 under `opencrbasic/tests/corpus/` (fuzz and CHM corpora), 16 in `crbasic/` and 4 in
  `examples/`. The count uses the server's own discovery function. Opening the repository root uses
  the repository's `.vscode/settings.json` instead, which matches the 16 station programs. Opening
  `opencrbasic/` alone keeps the default. The cost of analysing all 2,781 files is not measured.
  Observed at `7c450ed5`: an explicit restart with the `crbasic/*.CR6` entries gave a last initial
  publication 0.576 s after `initialize`. That is not an all-entries-complete signal, and the server
  exposes none.
- The logger comes from `opencrbasic.logger` and never from the file extension. A `.CR300` file with
  the default `cr6` is not an entry; set the logger to `cr300` to analyse it. Observed at `7c450ed5`
  after the language was chosen by hand. The automatic association (LSP-01e) was observed at `7d6db3b1` and
  `679f2f70`: all three extensions open as `crbasic` (section 8).

**Files changed outside the editor (LSP-06a).** When the client supports dynamic registration, the
server asks it to report changes to `**/*.{crb,cr6,cr1x,cr300,cr350}`, in lower and upper case, after
`initialized`. Without that support one stderr line says so, and closed files are noticed only on
save, open or close. A changed file re-analyses every entry that read it or listed its directory, so
a created include clears a missing-include warning. A created or deleted program file re-runs
discovery. An open document keeps its editor text until it is closed. A change whose bytes equal what
the entries last read re-runs nothing. Mixed-case extensions such as `.Cr6` are not watched, and
includes are looked up in the entry's own directory only. VS Code sent these events at `679f2f70`,
including for a deleted folder (H-07, fixed; it failed at `7d6db3b1`); how fast is not measured. Restarting
the server still works (section 10).

**Workspace folders (LSP-06a).** Adding or removing a folder re-runs discovery, and entries that drop
out lose their diagnostics unless they are open. Adding and removing a root was observed at `679f2f70`.

**Bounds (LSP-06a).** A file over `maxFileBytes` is not analysed. `maxQueuedJobs` limits how many
entry analyses run at once; the rest wait and none is dropped. `maxCacheMb` drops the least recently
used parsed program, and a later feature request re-analyses that entry. Each bound is reported as a
server warning at line 0 of the entry (source `opencrbasic (server)`) and once in the log. It is never
a CRBasic error (`lsp-api.md`, Workspace). The `maxFileBytes` warning was observed in an editor at
`679f2f70`; the queue and cache bounds are covered by stdio tests only.

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
- **Display text (LSP-07-wire, H-04).** The compiler decodes source bytes as Latin-1, so a UTF-8 name
  reads as mojibake in its message. The server re-decodes the compiler's message with
  `opencrbasic.encoding` before publishing it (`lsp-api.md`, Diagnostics mapping). Analysis, the CLI
  and the structured results are unchanged. Observed at the diagnostic and protocol level at `7d6db3b1`
  and `679f2f70`; Problems-panel rendering and cp1252 rendering were not seen.

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
count are marked optional, which is a simplification (`lsp-api.md`, Hover and signature help). The
accepted counts for the 130 real-compiler cases were recorded at `4d88a186` (host record
`e77ee5cc`); they are not claimed for every argument value.

Host: at `1b01311d`, the active parameter was 0 after `VoltSE(` and 1 after the first comma. Highlight
rendering: **untested**.

### 5.4 Completion

Triggered by `.`, and offered while you type a name. At a prefix it lists program names first (Subs,
variables and constants visible in the enclosing routine), then catalog names (keywords,
instructions, functions and symbols). After `name.` it lists the fields of that table or structure.
The replaced range is the prefix only.

- There is no argument or type context. No items are offered inside a string or after `'`.
- At most 200 items are returned (`COMPLETION_LIMIT`, `editing.py`). When the list is cut, the server
  sends `isIncomplete: true` (`lsp/features.py`), so the editor should ask again as the prefix grows
  instead of filtering only the first 200. At `679f2f70` the empty prefix returned 200
  items with `isIncomplete=true` and `Vo` returned 14 relevant items (provider level; list refresh
  while typing is not observed).
- Measured in-process on `Main.CR6` on a loaded cloud host: a request takes about 16 ms when the
  buffer has changed and about 2 ms when the scan is cached (LSP-03c and LSP-03d handoffs). These are
  not host measurements.

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
- References (Shift+F12, Peek References) list the uses in every entry that reads the
  declaration's file: from a use in entry A of a `Public` declared in a library that entry B also
  includes, B's uses are listed too (LSP-R4a). An entry whose analysis is not current (pending,
  failed or behind an unsaved edit) is left out of that request rather than refused; ask again
  when its analysis has finished. Definition and highlights use the request file's program only.
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
- When the client declares `workspace.semanticTokens.refreshSupport`, the server asks it to re-request
  tokens after each new analysis (`lsp-api.md`, Language features). Whether VS Code declares it was not
  checked here.
- The TextMate grammar (`vscode/syntaxes/crbasic.tmLanguage.json`) colours comments, strings with `""`,
  numbers, directives, keywords, instructions, built-in functions, predefined constants and operators.
  It is generated from the CR6 catalog and uses the CR6 names whatever `opencrbasic.logger` is set to.
  Do not edit it by hand; see [lsp-api.md](lsp-api.md), "Generated TextMate grammar".

Host: at `602f6462`, 12 real TextMate/Oniguruma scope checks passed. At `7c450ed5` the highlighting was
inspected visually in Dark Modern: comments, strings, numbers, keywords and instructions had distinct
colours. Predefined constants render white in that theme. At `1b01311d`, semantic tokens were
non-empty before and after an edit. Repainting after an edit without a keystroke: **untested** in
an editor.

### 5.10 Rename, formatting and quick fixes (LSP-07-wire)

The server registers these (`lsp/server.py`). Their cores have their own tests, and the server path
has stdio tests. They were run through VS Code's provider commands and workspace edits at `7d6db3b1`
and `679f2f70` (T-12; section 8); the F2 box, refusal text, Ctrl+. menu and toasts were not seen. A refusal is an LSP error or an information message,
never a CRBasic diagnostic.

- **Rename (F2).** Renames a declared name in every entry that reads its file, including closed files.
  Edits are versioned: an open document gets its version and a closed one gets `null`. Rename
  refuses with `Cannot rename: <reason>` and `data.reason` set. The reason is `no_entry` (no entry
  reads the file), `pending` or `unanalysed` (an entry's analysis is pending, failed or timed out),
  or `stale` (the text changed while the edit was computed). The core refusals (`rename.REASONS`,
  all or nothing) cover, for example: a name that is not a name in code, such as a comment, string or
  keyword (`not_a_name`); a predefined name (`external`); a new name that is a keyword, an invalid
  identifier or a reserved word (`reserved_name`, `invalid_name`); a name already in scope
  (`collision`); a table named by a string (`quoted`). A click on a comment-only line, in an inactive
  `#If` branch, on a directive or after `EndProg` is also `not_a_name`, with the message "the
  position is not in code any entry parses" (LSP-R4a). A case-only change is allowed.
- **Formatting.** Format Document and Format Selection change only leading whitespace. When any
  entry that reads the file has an error anywhere in its program, or an entry is not current, nothing
  changes and an information message reads `opencrbasic: Cannot format: ...`. The indentation rules
  (`lsp-api.md`, Formatting): each block body is one level deeper, `Else` and `ElseIf` sit with their
  `If`, `Case` sits one level inside `Select`, continuation lines are one level deeper, and `#If`
  directives stay at column 0. Lines after an unclosed block, or after the top-level `EndProg`, are left
  alone. `SlowSequence` is indented inside `BeginProg`, which differs from the layout of this
  repository's `Main.CR6`. Offline evidence: the 198 CHM programs that compile on `cr6` gave identical
  results after formatting (LSP-07b handoff, cloud run).
- **Quick fixes.** `Change to `Name`` for a misspelled name in an undefined-symbol, undefined-Sub or
  missing-include diagnostic. Candidates are declarations in scope and catalog names within a small
  edit distance (at most 2, at most 5 offered). The action replaces one token. It creates no
  declaration and no `Include`, and it is offered only for diagnostics of open documents.
- **Not offered.** Refactoring kinds, "fix all", pull diagnostics, range and delta semantic tokens.

Known limits and the open review findings for rename and formatting are in section 9.

### 5.11 Parameter-name hints and output commands (LSP-09c)

Both are wired in the server and the client. They have stdio tests and client unit tests;
**neither was run in an editor** (see [LSP-09c-handoff.md](lsp-rounds/LSP-09c-handoff.md) for the
host checks).

- **Parameter-name hints.** A grey `Name:` before each positional argument of an instruction or
  builtin function call, for example `Dest:`, `Reps:`, `Range:` before the arguments of `VoltSe`. The
  names are the ones signature help shows, so hints need `opencrbasic.helpDirectory` (section 6);
  without help text there are none. No hint appears where the argument already reads as the name, for
  an empty argument, or for a name declared in the program. Hints refresh when a new analysis
  finishes. Turn them off with `opencrbasic.inlayHints.parameterNames` = `false`. After an edit,
  hints for a changed file are withheld until the analysis catches up rather than shown at old lines.
- **opencrbasic: Show Compiler Output.** Opens, beside the editor, the text the command-line
  compiler prints for the active file's entry program: the header, `line N:` messages and warnings,
  computed from your unsaved text without writing a file. On a `.crb` the entry is the first entry
  program that includes it.
- **opencrbasic: Show Table Fields.** Opens a Markdown table of every data table the program
  defines (Status, DataTableInfo, Public and yours) with field names, types, units and processing.
  A program with errors shows "No tables". It is refused while the analysis is pending.
- Both documents are read-only; run the command again to refresh them. A refusal appears as the
  document's text.

## 6. Help directory

`opencrbasic.helpDirectory` names a folder holding `Instructions/*.htm` and `parameters/*.htm`, the
layout of the extracted CRBasic help. In this repository that is `extracted_chm/` at the repository
root. A relative path is resolved against the first workspace folder, so with the workspace folder
`opencrbasic/` use `../extracted_chm`. The repository's `.vscode/settings.json` (section 3) uses
`extracted_chm` for the repository root.

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
  before analysis (replacing characters the encoding cannot represent), so a saved file and its
  unsaved text give the same analysis when the setting matches the file's encoding. When some
  characters cannot be represented, the document gets one warning at line 0 (source
  `opencrbasic (server)`).
- **Open (LSP-R2b).** Editor positions are converted with the bytes after that replacement. With an
  encoding that cannot represent a character, the columns after that character on its line move, and
  an edit can land in the wrong place. The reviewer reproduced this at `f3069825` over stdio with
  `encoding: latin-1` and an emoji in a string: hover and highlights were two columns off, and an
  accepted completion produced `s = "😀" :oldNameN`. The default `utf-8` represents every character.
- Positions use the editor's negotiated encoding (UTF-16 in VS Code by default). The server converts
  from byte columns of the document's text. Observed at `1b01311d`: after a string earlier on the line
  that holds a non-BMP character (U+1F600, a surrogate pair in UTF-16), the use of a later name still
  resolved, and an error at the end of that line ended at the UTF-16 line end.
- **Non-ASCII include names (H-04).** Resolution matches the compiler. At `e77ee5cc` (T-05), a UTF-8
  name in the source (`Include "É space.crb"` with bytes `C3 89`) gave a cannot-open warning with exit
  status 0 from both the real compiler and `crbc`, and the Latin-1 byte name (`C9`) resolved. At
  `7c450ed5` the editor showed mojibake (`Ã\u0089 space.crb`). The display now re-decodes the message
  with the configured encoding (section 5.1), so with `utf-8` it reads `É space.crb`. That display
  change was observed at the diagnostic and protocol level at `7d6db3b1` and `679f2f70` (no `Ã`);
  Problems and hover rendering were not seen.
- Names with spaces resolve, and one file has one URI whether or not it is open (`7c450ed5`).

## 8. Host-tested status

Each record applies to its own revision only. API or protocol level means VS Code's provider commands,
workspace edits, configuration updates and client traces, not what is drawn on screen. Features added
after `679f2f70` (LSP-08f, LSP-09, LSP-R3) are untested in an editor.

| Area | Observed | Record |
|---|---|---|
| Server starts in the Extension Development Host; startup line in the output channel | Yes, with `api_version=1` and the revision | [7c450ed5](lsp-rounds/host-tests/2026-10-11-7c450ed5/RESULTS.md) |
| Startup line logged at error level in the channel | Yes; a success shows as an error | `7c450ed5` |
| Info copy of the startup line (LSP-01e) | Yes, as an info `window/logMessage`; the stderr copy still shows as `[error]` | [7d6db3b1](lsp-rounds/host-tests/2026-10-11-7d6db3b1/RESULTS.md) |
| `Main.CR6`: no errors, expected tester warnings | Yes | `7c450ed5` |
| Invalid edit: errors after debounce; undo clears them | Yes | `7c450ed5` |
| Unsaved library overlay merges diagnostics; Don't Save restores them | Yes | `7c450ed5` |
| Library edit on physical line 32 covers lines 32 and 33 | Yes (fixed by LSP-01d; was line 33) | [e77ee5cc](lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md) |
| `debounceMs` and `exclude` change without restart | Yes | `7c450ed5` |
| `opencrbasic: Restart Server` | Yes | `7c450ed5` |
| Interpreter path change restarts the server | Yes, a new server starts; an error toast also appears (H-02) | `7c450ed5` |
| Interpreter path change without the toast (LSP-01e) | Two valid changes restarted the server and republished; no failure message in the trace. Toast absence is a visual gap | `7d6db3b1`, [679f2f70](lsp-rounds/host-tests/2026-10-11-679f2f70/RESULTS.md) |
| `.CR300` opens as CRBasic automatically (LSP-01e) | **Fails** at `1b01311d` (H-03: plain text); `.CR300`, `.CR1X`, `.CR350` open as `crbasic` and logger `cr300` gives live diagnostics at `7d6db3b1` and `679f2f70` | [1b01311d](lsp-rounds/host-tests/2026-10-11-1b01311d/RESULTS.md) |
| Open `.crb` with no entry shows the information note | Yes | `7c450ed5` |
| Include name with a space: one URI, opened or not | Yes | `7c450ed5` |
| UTF-8 non-ASCII include name: mojibake in the message (H-04) | Yes at `7c450ed5`; with the display fix the message reads `É space.crb` with no `Ã` (diagnostic level; Problems rendering not seen) at `7d6db3b1`, `679f2f70` | `7c450ed5` |
| Syntax highlighting, scope checks | 12 of 12 pass | [602f6462](lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md) |
| Syntax highlighting, visual check, Dark Modern | Yes | `7c450ed5` |
| Help hover, variable hover, completion, signature help, outline, folding, links, navigation, semantic tokens, Unicode, after-edit update (provider and API level) | 16 checks pass | `1b01311d` |
| Same, visual rendering, repaint, typing responsiveness | **Untested** (the record lists them as pending) | - |
| Full pytest suite, client tests, repository verifier | 22,182 passed, 311 skipped; client 10 pass; `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` (H-05 strict-mode leak fixed) | `1b01311d` |
| Benchmark on an idle macOS host: hover p95 0.856 ms, completion p95 3.948 ms (in-process core calls); diagnostics p95 133.740 ms after the debounce, from an end-to-end edit-to-publish of 433.740 ms (20 edits, stdio) | Measured once; not a guarantee | `e77ee5cc` |
| Builtin argument counts (signature help) | 130 real-compiler cases; audit 0 differing | `e77ee5cc` (T-08 at `4d88a186`) |
| Watched files, workspace folders, `maxFileBytes` warning (T-10) | Pass at API level at `679f2f70`; folder deletion failed at `7d6db3b1` (H-07) and passes at `679f2f70`. Explorer clicks not seen. Queue and cache bounds: stdio and unit tests only | `7d6db3b1`, `679f2f70` |
| Completion cap (200) and `isIncomplete` | 200 items with `isIncomplete=true`; `Vo` gives 14 items including `VoltSe` (provider level; list refresh not seen) | `7d6db3b1`, `679f2f70` |
| Rename, formatting, quick fixes and their refusals (T-12) | Pass at API level (cross-file rename, refusals, formatting, format-on-save, quick fix); F2 box, refusal text and Ctrl+. menu not seen. One `undo` restores all texts; files the rename opened are then open and clean, not dirty (VS Code behaviour; section 9) | `7d6db3b1`, `679f2f70` |
| Unmatched `.CR300` information note (H-06) and rename of an unsaved library collision (R2a), Latin-1 positions (R2b) | Pass at provider level | `679f2f70` |
| Install check (LSP-08c, T-11) | macOS 15 of 15 at `7d6db3b1` and `679f2f70`; cloud Linux 15 of 15 ([LSP-08c-install-cloud.md](lsp-rounds/LSP-08c-install-cloud.md)); Windows **untested** | `7d6db3b1`, `679f2f70` |
| Packaged `.vsix` (LSP-08f, T-14); inlay hints and output commands (LSP-09c, T-15) | **Untested** in an editor (requests written, no record yet) | - |
| Windows and Linux | **Untested** (neither editor nor launcher) | - |

## 9. Known limits

- One platform observed (macOS). Windows and Linux are untested.
- **Review findings from round 2** ([review](lsp-rounds/review-2026-10-11-f3069825.md)) are fixed
  in LSP-R2a (`463a565`: rename checks every dependency's current bytes and `Public.<name>`
  captures) and LSP-R2b (`3d76e93`: positions map through the editor text when the encoding
  replaces characters); the server passes open buffers to rename's freshness check (LSP-06c).
  Remaining rename limit: a file that did not exist at analysis time and appears later is not
  detected ([LSP-R2a handoff](lsp-rounds/LSP-R2a-handoff.md)). The unsaved-library collision and the
  Latin-1 position checks passed at provider level at `679f2f70`.
- **Undo after a multi-file rename**: one Ctrl+Z restores all texts. Files the rename opened are
  then open and clean, not dirty (observed at `ccba3666`: entries that were closed were written by
  the rename and stayed closed until inspected; after one undo they were open and clean). At
  `7d6db3b1` the file the rename opened was closed again. Either way this is VS Code's behaviour,
  not the server's.
- Completion stops at 200 items and reports the list as incomplete (section 5.4).
- Ranges are whole lines. There are no token or column ranges for diagnostics or hover.
- Results during an edit can be stale (section 5.8). A timeout restarts the worker pool; the entry
  shows an error until its next successful analysis.
- Rename and formatting wait up to `analysisTimeoutMs` plus 2 s for pending analyses before refusing.
  A client with a shorter request timeout would show an error instead of the refusal (LSP-07-wire
  handoff).
- Formatting refuses per program: an error anywhere in an entry that reads the file blocks it.
- Quick fixes replace one token in open documents only. They never add a declaration or an `Include`.
- Files changed outside the editor: mixed-case extensions are not watched, and includes resolve only in
  the entry's own directory (LSP-06a handoff).
- Not offered: refactoring code actions, pull diagnostics. Parameter hints need the help directory
  and show the help parser's names as they are (for example `"FieldName"` with quotes for
  `SetSetting`).
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
- **Toast after changing `pythonPath`** (H-02, observed at `7c450ed5`). Fixed in LSP-01e; at `7d6db3b1`
  two interpreter switches logged no failed notification (toast absence not visually checked). If
  the new server did not start, run `opencrbasic: Restart Server`.
- **`[error]` lines in the output channel on a normal start or stop.** The startup line is also written
  to stderr, and the client logs stderr at error level; pygls logs `Server shutting down. No more
  requests!` the same way. Both are expected (observed at `7d6db3b1`).
- **`.CR300` opens as plain text** (H-03, observed at `1b01311d`). Fixed in LSP-01e: at `7d6db3b1`
  `.CR300`, `.CR1X`, `.CR350` open as CRBasic. Set `logger` to `cr300` to see its diagnostics.
- **No diagnostics for a file.** Check that the file is an entry (section 4). The information note
  tells you when an open file (a `.crb`, or since LSP-06c a program file of another logger) is not an
  entry and no entry includes it.
- **Stale results for a file changed outside the editor.** Watched files were host-tested at `7d6db3b1`
  (create/delete/rename of includes and entries, workspace folders). Deleting a whole folder left
  stale diagnostics there (H-07); LSP-06c adds a delete watcher for folders, **not yet host-tested**.
  If the output channel says the client does not support watched-file registration, changes to
  closed files are seen only on save, open or close. Run `opencrbasic: Restart Server` in either case.
- **`Cannot rename: ...`.** The text after the colon gives the reason (section 5.10). `pending` clears
  when the running analysis finishes. `unanalysed` means an entry has no completed analysis (it failed
  or timed out), and it stays until a later analysis of that entry succeeds.
- **`opencrbasic: Cannot format: ...`.** Fix the errors the message names. Formatting is not offered for
  a file with errors in an entry that reads it.

## 11. Where the details live

- [lsp-api.md](lsp-api.md): contracts, provenance and known limits for each module.
- [LSP_TO_BE_TESTED.md](../LSP_TO_BE_TESTED.md): the host checks still pending (T-04, T-09 to T-12).
- [LSP_PROGRESS.md](../LSP_PROGRESS.md): the round ledger.
