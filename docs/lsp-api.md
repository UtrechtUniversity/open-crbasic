# Language-server API

Contracts for editor tooling on top of the compiler. Specification history: `docs/lsp-rounds/LSP-00.md`
("Contracts for LSP-01"). The server section is "Language server (LSP-01)" below.

## Analysis facade (API_VERSION 1)

`opencrbasic.analysis` runs the CLI's own compile driver (`compiler.compile_file`) over a program
whose files may have unsaved edits. There is no second pipeline: `analyze` is `compile_file` plus
a `SourceProvider` and a result mapping. It writes no files and does not raise for CRBasic input
(`compile_file` already turns an internal failure into a rejected program).

### Source providers (`opencrbasic.sources`)

| Class | Behaviour |
|---|---|
| `DiskSource` | The loader's historical behaviour: `read` is `read_bytes` with `OSError` -> `None`; `list_dir` is `None` for a non-directory, else the names of the files (not subdirectories) in `iterdir` order. |
| `OverlaySource(base, overlays)` | Overlay bytes win over `base`. An overlay file also appears in its directory's listing (after the base names, sorted), even when `base` has no such directory. |
| `RecordingSource(inner)` | Records `reads` (path -> sha256 hex, `None` for a miss) and `listings` (directory -> sorted name tuple, `None` for a non-directory). Returns `inner`'s data and order unchanged. |

Paths are `Path.absolute()`: not lowercased, symlinks not resolved. `OverlaySource` makes its
keys absolute; `analyze` makes `entry` absolute. Include matching stays the case-insensitive name
comparison in `source._resolve_include_path`, which now takes the provider's `list_dir` and picks
the first match in the listing's order.

The provider is an optional `source=None` (None: `DiskSource`) argument of `source._Loader`,
`source.load_program`, `parser.parse_file` (both reads, including `_set_last_line`'s),
`signature.program_signature`, `compiler.compile_file`/`_compile_once`/`_finish`. `compile_file`
also takes `signature=True`; `False` skips `program_signature` (so `CompileResult.signature` is
`None`). `preprocess` and the CLI stay disk-only. The loader records which path each include
resolved to (`display name as written, stripped -> Path`) and exposes it as
`Diagnostics.include_paths` and `Program.include_paths`.

### API

```python
analyze(AnalysisRequest(entry, logger="cr6", overlays={}, warn_unused=False, signature=False))
    -> AnalysisResult(api_version, request, ok, mode, diagnostics, rendered_lines,
                      implementation_failures, dependencies, program)
```

- `logger` is a profile name (`profiles.get`); it is never inferred from the extension. An
  unknown name raises `ValueError`.
- `diagnostics`: `AnalysisDiagnostic(severity, message, line, file, path, rendered)` in the order
  of `CompileResult._sort_key` (the order of `result_lines`), **not** cut by the 4096-byte message
  buffer. `rendered` is `Diagnostic.cr6comp()`. `rendered_lines` is exactly
  `CompileResult.result_lines(show_signature=request.signature)` and is cut by the buffer.
- `path`: a diagnostic whose `file` is empty or equals the entry's name (case-insensitive) maps
  to the entry, whatever its line (including line 0, the documented entry anchor); a `file` that
  is an include display name maps to the path that include resolved to; anything else is `None`.
- `signature=False` skips the signature read; it changes no diagnostic. `signature=True` adds the
  `ProgSignature` line to `rendered_lines` for a successful compile.
- `implementation_failures`: the project's internal-error markers, separate from CRBasic messages.
  The marker `Internal error: unable to compile program.` also stays in `diagnostics`, as in the CLI.
- `dependencies`: `reads` and `listings` from the `RecordingSource`. A missing include shows as a
  `None`/absent read plus the directory listing that lacked it; a changed file hash or listing is
  what makes a cached result stale.
- `program`: the parsed AST (`None` when parsing failed). Opaque and unstable.

### Encoding

Overlays are bytes. The compiler decodes bytes as Latin-1, as for disk files, so a caller encodes
editor text with the workspace file encoding (LSP-00 "Encoding", default `utf-8`) before passing
it; a saved buffer then gives the same bytes, and the same analysis, as its disk file.

### Known limits

- Under `OPENCRBASIC_STRICT=1` internal exceptions are re-raised (as for `compile_file`).
- A missing or unreadable entry file yields a rejected program (the marker, not a CRBasic message).
- `DiskSource.list_dir` does not guard `iterdir` errors (permission), as before.
- Not gated against the Wine oracle (none exists in this environment); equivalence with the CLI
  is tested by disk vs identical-bytes overlay and by `tools/selfdiff.py`.

## Help provider (`opencrbasic.helpdocs`)

Display-only instruction documentation, separate from the analysis facade and from the catalog.

```python
HelpProvider(directory)            # directory = root holding Instructions/*.htm and parameters/*.htm
    .lookup(name) -> InstructionHelp | None      # case-insensitive; None for unknown/unparsable
    .names() -> tuple[str, ...]                  # upper-case names with parsable help
InstructionHelp(name, names, title, syntax, params: tuple[ParamHelp, ...], summary, source_path)
ParamHelp(name, link, description, optional=False, repeatable=False)
pair_signature(name, type_codes, help) -> PairedSignature(matched, help_count, catalog_count, params, note)
render_markdown(help, sig=None) / render_plain(help, sig=None) / render_signature_label(sig)
coverage_report(profile, provider, names=None) -> CoverageReport(total, with_help, matching, mismatches, without_help)
function_coverage_report(profile, provider, names=None) -> FunctionCoverageReport(total, with_nargs, with_help, checked, matching, mismatches, without_help, unchecked)
function_arg_count_classes(profile, provider, names=None) -> tuple[ArgCountRow(name, help_count, help_required, nargs, category)]
```

- Lazy: the first `lookup` parses every `Instructions/*.htm` once. Nothing raises; a missing
  directory, an unreadable page, malformed HTML or a page without a Syntax section gives `None`.
- A page documents every name in its `<h1>` (split on `/`) and in its Syntax-section call forms;
  `params` are the parameters of the form for the looked-up name, in syntax order. Every top-level
  `,` or `;` span of the call parentheses is one parameter, linked or not (`link` is the
  `../parameters/` target, or `None`). Rules: a link holding several names (`Mult, Offset`) gives one
  parameter per name with that link; a word glued after a link (`Mult, Offse</a>t`) belongs to the
  link's name; a link starting after other name text begins a new parameter; commas inside quotes or
  brackets do not split; a parenthesised note directly after a bare statement name (`(optional)`,
  `(or Alias)`) is not a parameter; statement-block brackets outside a call's parentheses
  (`[statementblock]`, `Next [counter]`) are not parameters.
  Decided by the human 2026-10-11 ([LSP_PROGRESS.md](../LSP_PROGRESS.md) "Human decisions"):
  - A bare `[X]` (one name in brackets, linked or not, e.g. `[ModbusOption]`, `[ FFTSpaInit ]`)
    sets `optional=True` and drops the brackets. Other brackets stay as written (`Mode [A,B,C]`)
    and do not set `optional`.
  - A trailing `...` on the last name (`Item1, Item2, Item3...`) makes one parameter named after
    the stem (`Item`) with `repeatable=True`; the numbered run ending in that name collapses into
    it. The signature label shows `Item...`. An unnumbered `Extra...` is `Extra`, repeatable.
  - Quoted names keep their quotes (`"MenuName"`).
  - An `[optional]` or `(optional)` span that is a list item of its own (`FileOption, [optional],` in
    FTPClient, EMailSend, HTTPPost, HTTPPut) creates no parameter; it sets `optional=True` on the
    parameter immediately before it. Only a span directly after a named parameter applies; a marker
    with nothing before it is ignored.
  `description` comes from the page's Parameters section, else the linked parameter page, else `""`.
  `optional` is `True` where the syntax line says `optional` (inline, or as an item after the name) or a
  bare `[X]` wraps the name; `False` means "not stated".
- Pairing is by count only. `pair_signature` uses help names when `len(params)` equals the catalog
  type-code count and otherwise returns `matched=False` with a note and placeholder labels
  (`arg1`...): no guessing. Help names never change checker rules; types come from the catalog.
- Rendering is separate from parsing and returns text; Markdown escapes the help text.
- Provenance: the help is Campbell Scientific documentation, read from the local directory.
  Internal use only, so copying it is permitted ([legal.md](legal.md), 2026-10-11); LSP-04a's
  tests nevertheless use synthetic fixtures (`tests/lsp/fixtures/help/`). The server (LSP-04b) takes the directory from the
  setting `opencrbasic.helpDirectory`, default none: no help is shown unless configured.
- Function coverage (LSP-04c) is a separate report over `profile.functions`. A row is checked only
  when its table has `nargs`: the help parameter count must equal it. Rows with help and no `nargs`
  are `unchecked`; rows with `nargs` and no help are in `without_help`. Instruction numbers are
  unchanged. Signature help for functions takes the checker's accepted argument counts (see Hover).
- Argument-count classes (LSP-04d): `function_arg_count_classes` lists the functions whose help count
  differs from `nargs` as `packing_cap` (`nargs` 6, the 16-bit `argspec` maximum), `optional_tail`
  (help required count equals `nargs`) or `other`. It is a classification only. Signature help uses help names only where the checker's
  accepted-count tables support them (LSP-04e, see Hover); otherwise `argN`. Evidence and the decision are in
  [LSP-04d-handoff.md](lsp-rounds/LSP-04d-handoff.md).
- Coverage (observed, CR6 catalog against the local `extracted_chm/`): see
  [LSP-04a-handoff.md](lsp-rounds/LSP-04a-handoff.md) and, after the 2026-10-11 rules,
  [LSP-04a-rules-handoff.md](lsp-rounds/LSP-04a-rules-handoff.md). Documentation coverage is not
  compiler-rule coverage.

## Instruction catalog (LSP-03a)

`opencrbasic.catalog` lists every name a program can use for one logger profile.

```python
cat = InstructionCatalog.for_profile("cr6")     # cached; ValueError for an unknown profile
cat.get("voltse")            # CatalogEntry(name="VoltSe", kind="instruction", ...) or None
cat.get("if", "symbol")      # one kind only
cat.entries("if")            # every entry of the name, in kind priority order
cat.names("keyword")         # profile spellings of one kind, sorted case-insensitively
iter(cat), len(cat), "voltse" in cat
```

`CatalogEntry` is a frozen dataclass: `name` (the profile's spelling), `kind`, `profile`, `source`
(`instructions.json`, `functions.json`, `symbols.json`, `keyword:table`, `keyword:parser`),
`params` (instructions: type codes, see `docs/type-codes.md`), `value` (symbols), and for
functions `code`, `func_kind`, `argspec`, plus `nargs`/`argtypes` for the rows that have them.
Lookup is case-insensitive; names keep a trailing `$` (`CHR$` and `CHR` are different functions).

### Provenance

Instructions, functions and symbols are built only from `profiles.get(name).instructions`,
`.functions` and `.symbols`: the tables that `tools/extract_tables.py` read out of each logger's
compiler (see `docs/legal.md`). There is no second hand-written instruction list, and the counts
of the first three kinds equal the row counts of the profile's data files (tested for all four
profiles).

### Kinds overlap

A name can have several kinds because the tables overlap: `If`, `Select`, `Case`, `Sub`,
`Function`, `Scan`, `Do`, `For` are instruction rows and symbols (the words after `End`/`Exit`);
`To`, `Is` and the word operators are function rows. `get(name)` returns one entry by the priority
instruction, function, symbol, keyword; `entries(name)` returns all.

### Keywords

Kind `keyword` marks the words that structure a program. Most are already instruction rows, so
the catalog derives them: `TABLE_KEYWORDS` in `catalog.py` names the block and declaration words
(`If` ... `EndIf`, `For`/`Next`, `Sub`/`EndSub`, `BeginProg`/`EndProg`, `Scan`/`NextScan`,
`DataTable`/`EndTable`, `Public`, `Dim`, `Const`, `Alias`, `Units`, `Include`, ...), and a profile
gets the entry (with the table's spelling, `source` `keyword:table`) only when its own
instruction table has the row. The parser dispatches on those words in `Parser._parse_statement`
or reads them as ordinary instruction rows.

The tables lack four language words, listed in `EXTRA_KEYWORDS` (`source` `keyword:parser`):
`Then`, `Step`, `As`, `Optional`. Each is recognised by name in `parser.py` (`is_ident("THEN")` in
`_parse_if`/`_parse_elseif`, `STEP` in `_parse_for`, `AS` in declarations, `_parse_subdef` and
`_parse_param`, `OPTIONAL` in `_parse_param`); `test_extra_keywords_are_in_parser_source` checks
that the strings are still there. `And`, `Or`, `Not`, `Xor`, `Mod`, `Eqv`, `Imp`, `To`, `Is` are
function rows and stay functions. Directives (`#If`, `#ElseIf`, `#Else`, `#EndIf`,
`#If_no_remove`, `#UnDef`) are not catalog entries; the parser matches them with `_DIRECTIVE_RE`
and the generated grammar repeats that list. `Rem` is not a comment (`source._strip_comment`
only knows `'`), so it is not highlighted as one.

### Generated TextMate grammar

`tools/gen_grammar.py` writes `vscode/syntaxes/crbasic.tmLanguage.json` (scope `source.crbasic`)
from the CR6 catalog and the lexer's operator list. Patterns, in match order: comment (`'` to end
of line), string (`"` with `""`), directive, number (decimal, `.5`, exponent, `R` suffix, `&H`,
`&B`), keyword, word operator, instruction (`support.function.instruction.crbasic`), builtin
function (`support.function.builtin.crbasic`), predefined constant (`support.constant.crbasic`),
operator. A name that is in several kinds takes the first of that order. Every regex starts with
`(?i)`, names are escaped and sorted longest first, and names are bounded by lookarounds (not
`\b`, so `CHR$` works and `Tmp.001`, `Scan1`, `MyIf` do not match). Output is sorted JSON with a
trailing newline.

```bash
cd opencrbasic
uv run --no-sync python tools/gen_grammar.py           # regenerate
uv run --no-sync python tools/gen_grammar.py --check   # exit 1 when the committed file is stale
```

`tests/lsp/test_grammar_sync.py` runs the check, validates the JSON, compiles every regex with
Python `re` (the patterns stay in the syntax shared with Oniguruma; they also compile as
JavaScript `RegExp` when `(?i)` is turned into a flag), and tokenizes sample lines with a small
matcher (earliest match wins, ties go to the earlier rule). That proves the patterns match the
intended text under Python `re`; it does not prove how VS Code renders the grammar.

## Source positions

`opencrbasic.positions` (LSP-02a) maps what the compiler reports back to the physical files. It is
read-only over provenance the loader records; no compiler output changed (`tools/selfdiff.py
--compare` 0 differ). Brief and criteria: `docs/lsp-rounds/LSP-02.md`.

### Recorded provenance

- `source.Piece(start, end, file, line, col, occurrence)`: `LogicalLine.text[start:end]` is
  physical line `line` (1-based, the true line of that file, unlike `LogicalLine.line`) from
  decoded column `col` on. `LogicalLine.pieces` lists them in order and covers every character
  that came from a physical line, through `_` continuation (including the blanks before the `_`
  that the join keeps, the dropped `_`, and a lone `_` line) and `Public`/`Dim` initializer pulls
  (CR6, CR1000X, CR300, CR350). The `" = "` or `" "` the pull inserts, and the text of
  `include_*`/`overflow` lines, have no piece.
- `LogicalLine.phys = (first, last)`: physical lines the statement spans (pulled lines included).
  `LogicalLine.occurrence`: `Occurrence.id` of the load. Both default (`None`, `0`); the
  `LogicalLine` constructor is unchanged. `include_begin`/`include_end`/`include_warning`/
  `include_error` lines carry the span of the `Include`/`IncludeSection` statement. The
  `unpulled` lines (the declaration as written, restored when an initializer is not read) carry
  their own pieces.
- `source.Occurrence(id, file, path, parent, site)`: one entry per load of a file, in load order
  (`Program.occurrences`, `Diagnostics.occurrences`; id 0 is the entry). A file included twice has
  two occurrences; `parent` is the including occurrence and `site` the physical line span of the
  including statement, so the include-site chain is recoverable.
- `Program.line_map: {file display name: {reported line: [LineSpan(first, last, occurrence)]}}`
  (recorded by the parser, `parse_file` and `parse_text`). It uses the numbering the compiler
  reports, including the parser's give-back of unread (inactive `#If`) initializer pulls, so after
  a pull the reported line is smaller than the physical one. Several logical lines can share a
  reported line and a doubly-included file has one span per occurrence: all are kept. Lines of an
  inactive `#If` branch, directives, and lines the compiler never reads (after the first top-level
  `EndProg`, after an overflow line) are mapped too; for those never-read lines pulls are treated
  as unread. File keys are the same display names as `AnalysisDiagnostic.file`.

### API

| Function | Purpose |
|---|---|
| `reported_spans(line_map, file, line) -> list[LineSpan]` | All physical spans reported at that line (file name matched exactly, then case-insensitively). |
| `line_span(line_map, file, line) -> (first, last) \| None` | Union of those spans; None when unknown. |
| `logical_ranges(ll, start, end) -> list[PhysicalRange]` | Logical column range `[start, end)` -> `PhysicalRange(file, occurrence, line, start_col, end_col)`, one per physical line/contiguous slice; inserted characters map to nothing. An empty range gives one empty range, or none inside inserted text. |
| `exact_range(ll, start, end) -> PhysicalRange \| None` | The range when it is one contiguous physical slice (a token from a single piece). |
| `physical_lines(data) -> list[bytes]` | The loader's line split (CRLF and LF). |
| `lsp_position(data, line, col, *, encoding="utf-16", editor_encoding="utf-8") -> (line0, character)` | Decoded column -> LSP position. |
| `lsp_range(data, physical_range, ...)` | Both ends of a `PhysicalRange`. |
| `decoded_position(data, line, character, *, encoding="utf-16", editor_encoding="utf-8") -> (line1, col)` | The inverse of `lsp_position` (LSP-wire): LSP `(line0, character)` -> 1-based line and decoded column. |

Token columns are offsets into `LogicalLine.text`. The parser keeps no token or logical-line
reference on AST nodes, so a consumer obtains tokens by `lexer.tokenize(ll.text)` on the loader's
lines (`source.load_program`), then `exact_range`. Colon statements need nothing special: a
segment is a substring of the logical text, so its columns convert like any other.

`AnalysisDiagnostic.span` (default None) is `line_span` of the diagnostic's `(file, line)`: the
physical first/last line in the diagnostic's file. None for line 0 and for unknown lines. The
compiler reports lines only, so no column range is invented for a diagnostic.

### Encoding

The compiler decodes bytes as Latin-1, so a decoded column is a byte offset into the physical
line's bytes. `lsp_position` decodes `line_bytes[:col]` with `editor_encoding` (errors replaced,
a trailing incomplete sequence dropped, so a column inside a multi-byte character snaps to that
character's start) and counts `utf-16` code units, `utf-8` bytes of the decoded text, or `utf-32`
code points. A column past the end of the line is clamped; a line outside the file raises
`IndexError`; an unknown position encoding raises `ValueError`. Bytes invalid in the editor
encoding become U+FFFD (1 UTF-16 unit, 3 UTF-8 bytes), as an editor that reads the file with
replacement would show them.

`decoded_position` is the inverse: `character` counted in `encoding` units -> the byte offset into
the line. On a character boundary `decoded_position(d, *lsp_position(d, l, c)) == (l, c)`; a
`character` inside a character (the middle of a UTF-16 surrogate pair) snaps back to its start; a
`character` past the line end gives the line length; a negative one gives 0; a line outside the
file raises `IndexError`. An all-ASCII line short-cuts to `min(character, len)`.

### Known limits

- Only ASCII-compatible editor encodings are meaningful (the line split is on bytes).
- A lone CR (not part of CRLF) stays inside a loader line but is a line break for an editor; lines
  after one differ. Not handled.
- Diagnostics carry lines only. `span` of a diagnostic whose reported line is shared by several
  logical lines, or by several occurrences, is their union.
- A multi-piece range (across a continuation or pull) comes back as several `PhysicalRange`s; an
  editor range for it is the caller's choice.
- Statements are not annotated with columns; AST nodes still carry only `line`.
- Not gated against the Wine oracle (none exists here); the map reproduces our own emulation of
  the reported line numbers.

## Language server (LSP-01)

`opencrbasic.lsp` is a protocol adapter on pygls 2.1.1 / lsprotocol 2025.0.0. It contains no
language rules: all CRBasic knowledge comes from `analysis.analyze` and the core feature modules.
Modules: `server.py` (pygls handlers, `main`), `session.py` (documents, entries, scheduling,
diagnostics merge, the programs kept for features; no pygls import), `features.py` (core answers ->
LSP types; imports lsprotocol), `config.py`, `worker.py` (the pool job; imports only the core compiler). The package
`__init__` imports nothing third-party, so analysis workers never load pygls.

### Launch

`python -m opencrbasic.lsp` (or the `crbc-lsp` script, `opencrbasic.lsp.server:main`), stdio
transport. With the editable install in a git worktree, set `PYTHONPATH=<worktree>/opencrbasic/src`
or the main checkout is imported. First stderr line:

```
opencrbasic-lsp: opencrbasic=<path to opencrbasic/__init__.py> api_version=<N> git=<short sha|unknown>
```

The same line is sent once as `window/logMessage` of type Info after `initialized`: clients show
stderr at error level, so the stderr copy alone reads as an error on a successful start.

The revision comes from `git -C <package dir> rev-parse --short HEAD` (failure is ignored). The
server was written for `analysis.API_VERSION == 1`; any other value prints
`opencrbasic-lsp: refusing to start: ...` and exits with status 2. Exit status after `shutdown` +
`exit` is 0; `exit` without `shutdown` (or stdin closing) is 1. Everything the server logs goes to
stderr; stdout carries only protocol frames.

### Capabilities

`textDocumentSync` with `openClose`, `change = 1` (Full) and `save`; diagnostics are pushed
(`textDocument/publishDiagnostics`), there is no pull provider. Language features (LSP-wire, each
with a stdio transcript test in `tests/lsp/test_features_server.py`):

| Capability | Registration | Core function |
|---|---|---|
| `completionProvider` | trigger `.` | `editing.completion_candidates` |
| `hoverProvider` | | `hover.hover` |
| `signatureHelpProvider` | triggers `(` and `,` | `hover.call_context`, `hover.signature_help` |
| `documentSymbolProvider` | hierarchical `DocumentSymbol` | `editing.document_symbols` |
| `foldingRangeProvider` | | `editing.folding_ranges` |
| `documentLinkProvider` | no resolve | `editing.include_links` |
| `definitionProvider`, `referencesProvider`, `documentHighlightProvider` | | `navigation.Navigation` |
| `semanticTokensProvider` | `full` only; legend = `navigation.TOKEN_TYPES` / `TOKEN_MODIFIERS` | `Navigation.semantic_tokens` |
| `renameProvider` | `prepareProvider` (advertised when the client declares `textDocument.rename.prepareSupport`, as pygls decides) | `rename.prepare_rename`, `rename.rename` |
| `documentFormattingProvider`, `documentRangeFormattingProvider` | | `formatting.format_indentation`, `formatting.format_range` |
| `codeActionProvider` | kinds `["quickfix"]` | `quickfix.code_actions` |
| `inlayHintProvider` | no resolve | `inlay.parameter_hints` ("Inlay hints and commands (LSP-09c)") |
| `executeCommandProvider` | `opencrbasic.showCompilerOutput`, `opencrbasic.showTableFields` | `commands.compiler_output`, `commands.table_fields` |

pygls additionally advertises `workspace.workspaceFolders` (changes are handled, see "Workspace (LSP-06a)").
Nothing else (range or delta semantic tokens, no other code-action kinds, no on-type formatting,
no pull diagnostics) is advertised.

### Configuration

Keys (defaults in `config.py` follow LSP-00 "Configured entries"): `entries` (`["**/*.CR6"]`),
`exclude` (`[]`), `logger` (`"cr6"`), `encoding` (`"utf-8"`), `debounceMs` (300),
`analysisTimeoutMs` (10000), `maxFileBytes` (2097152), `maxQueuedJobs` (64), `maxCacheMb` (256) ("Workspace (LSP-06a)"), `helpDirectory` (`""`: no help text), `inlayHints.parameterNames` (`true`; a nested object `{"inlayHints": {"parameterNames": false}}` or the flat key; "Inlay hints and commands (LSP-09c)") and `featureWaitMs` (1500, see
"Language features"). The VS Code extension contributes every key except `featureWaitMs`, with the
same defaults (`vscode/package.json`, checked by `test_vscode_contributes_server_tuning_keys`);
`featureWaitMs` is internal and set only by a client that sends it. Sources, in order: `initializationOptions`, then, when the client
declares `workspace.configuration`, a `workspace/configuration` request for section `opencrbasic`
(after `initialized` and on every `workspace/didChangeConfiguration`; a pulled value overrides the
initial one key by key). A client without pull support may send the section in the
`didChangeConfiguration` params (`settings` or `settings.opencrbasic`). An invalid value is
ignored with a stderr line and the previous value kept. Changing `logger` or `encoding`
re-analyzes every entry; changing `entries`/`exclude` re-discovers (entries that drop out have
their diagnostics cleared); `debounceMs` and `analysisTimeoutMs` apply to later jobs.

Entry discovery globs the workspace folders (`entries` minus `exclude`; workspace-relative,
case-insensitive; `*`, `?`, `**`, `{a,b}`; directories starting with `.` and `node_modules` are not
walked) at startup, on configuration change, on workspace-folder changes and on watched-file events ("Workspace (LSP-06a)"). An opened file with
extension `.CR6` (case-insensitive; additionally the extension of the configured logger, e.g.
`.CR1X`) is an entry for itself while it is open, configured or not. A `.crb` is never an entry.

### Workspace (LSP-06a)

- **Watched files.** After `initialized`, when the client declares
  `workspace.didChangeWatchedFiles.dynamicRegistration`, the server registers
  `workspace/didChangeWatchedFiles` with two watchers: `config.watch_pattern()`, `**/*.{...}` over
  `crb` and the program extensions of every logger profile, each in lower and upper case (a
  mixed-case extension is not watched), and `**/*` with `WatchKind.Delete` only (host finding H-07:
  VS Code reports a deleted folder only as the folder's URI, which the first glob does not match). Without client support one stderr line says so and closed-file changes
  are noticed only through save/open/close. Handling (`Session.files_changed`): events for an open
  document are ignored (its text wins; closing restores the disk state); other events re-analyse
  every entry whose last analysis read the file or listed its directory, so a created include
  resolves the missing-include warning and a deleted one brings it back; a created or deleted
  program file re-runs discovery (a new entry appears, a deleted one drops out and its diagnostics
  are cleared); a rename is delete + create. A changed file whose bytes equal what the entries
  last read re-runs nothing. An event for a path with another extension is a directory: a created
  one, or a deleted one that is or contains a discovered or open file or anything an entry read or
  listed, runs discovery again, and entries that read, listed or live below it are re-analysed (a
  deleted path that holds nothing known, such as a `.txt` file, changes nothing). Delay: `debounceMs`.
  Includes are looked up in the entry's own directory only (observed: `CPU:sub/Lib.crb` is not
  resolved by the loader), so directory events matter for discovery.
- **Workspace folders.** `workspace/didChangeWorkspaceFolders` adds/removes folders, re-runs
  discovery and clears diagnostics of entries that drop out (unless open).
- **Bounds** (reported as `opencrbasic (server)` warnings at line 0 of the entry, plus a
  `window/logMessage` Warning once per cause; never CRBasic errors; compiler semantics unchanged;
  keys are tuning keys; the VS Code extension contributes them, see Configuration):

  | Key | Default | Meaning |
  |---|---|---|
  | `maxFileBytes` | 2097152 | An entry whose file, or any file it reads (open text or disk), is larger is not analysed. Checked before the job on the entry and the files its last analysis read, and after it on the files it read. Changing it re-analyses every entry. |
  | `maxQueuedJobs` | 64 | Entries with an analysis task alive at once. Further entries wait (open documents first) and run as slots free; none is dropped. |
  | `maxCacheMb` | 256 | Pickled parsed programs kept for the language features. The least recently used program of another entry is dropped (its diagnostics stay); a later feature request re-analyses that entry. Results exist only for configured and open entries. |

  Include depth and cycles are the loader's own business; the server adds only the existing job
  timeout and pool restart. Observed: an entry that `IncludeSection`s itself ends in a
  `RecursionError` inside the parser and is published as `opencrbasic (internal)`; the server
  stays usable (see the LSP-06a handoff, compiler issue).
- **Cancellation.** `$/cancelRequest` for a feature request that waits for a program is answered
  with error -32800 (RequestCancelled) by pygls; a request already running in a thread finishes.
- `completion`: `isIncomplete` follows `editing.Completion.is_incomplete` when the core provides it.

### Overlays and encoding

Every open document is an overlay: its text encoded with `encoding` (`errors="replace"`) replaces
the disk file for every analysis. When replacement happened the document gets one Warning
(source `opencrbasic (server)`, line 0) saying that characters were not representable.

### Scheduling

- Per entry: a change schedules a re-analysis after `debounceMs` of quiet (open, save, close and
  configuration changes schedule immediately). One job per entry at a time; a newer request while
  one is in flight discards that job's result and runs once more. A result is also discarded when
  an open document it depends on changed version since the snapshot.
- Jobs run in a spawn-context `ProcessPoolExecutor` (2 workers), at most 2 concurrently, awaited
  from asyncio so the event loop never blocks on analysis.
- Affected entries for a change to file F: entries whose last `dependencies.reads` contain F or
  whose `dependencies.listings` contain F's directory, F itself when it is an entry, and every
  entry that has not completed a first analysis. All configured entries are analyzed once after
  `initialized`. On open or close, an entry whose last analysis read exactly the bytes now in
  effect (sha256 compared) is not re-run; only its recorded overlay version is updated.
- Timeout (`analysisTimeoutMs`, counted from when the job starts): the pool's processes are
  killed and the pool is recreated; the entry gets a server-error diagnostic. A pool break (worker
  crash, or the kill above while another entry's job ran) retries each affected job once, alone in
  the pool; a second break is reported as a crash for that entry only. The error diagnostic is
  cleared by the entry's next successful analysis.
- `shutdown` kills the pool; the process then exits on `exit`.

### Diagnostics mapping

- Per file, diagnostics from every entry that mention it are merged; the message gets the prefix
  `[<EntryName>] ` when more than one entry contributes to that file. Source `opencrbasic`,
  severity error/warning.
- A contribution is dropped while the file's open version differs from the one the entry's result
  was computed with, so stale ranges are never shown with a newer version.
- Range: when `AnalysisDiagnostic.span` is known (LSP-01d), the range starts at character 0 of
  the span's first physical line and ends at the end of its last physical line, in the diagnostic's
  own file, so a continued statement covers all its lines. Otherwise compiler line N is LSP line
  N-1, characters 0 to the end of that line; line 0 is LSP line 0. Line numbers clamp to the file's
  current text (open document, else disk decoded with `encoding`). A diagnostic whose file cannot be
  resolved (`path` is `None`) is anchored at line 0 of the entry with the text `<file>:<line>: `
  prepended.
- The implementation-failure marker diagnostics (`Internal error: unable to compile program.` /
  `... parse program.`) and `implementation_failures` are published with source
  `opencrbasic (internal)`, severity error, the failure strings appended to the message. Timeout,
  worker crash and scheduler errors are published the same way at line 0 of the entry.
- Publishes carry the document `version` for open documents. A file that had diagnostics and now
  has none gets an empty publish; identical consecutive publishes are suppressed.
- An open document that is not an entry and that no analyzed entry read gets one Information
  diagnostic at line 0 (only after every entry has completed a first analysis). A `.crb` gets `No
  configured entry program includes this file; diagnostics come from the entry programs that
  include it.`; a program file (for example a `.CR300` under logger `cr6`, host finding H-06) gets
  `This file is not an entry program for the configured logger and entries, and no entry program
  includes it.`
- Display text (LSP-07-wire, host finding H-04): the compiler decodes source bytes as Latin-1, so
  its message text for a name written in UTF-8 reads `Ã\x89`. The published `message` is the compiler
  text re-decoded for display: `text.encode("latin-1").decode(encoding, "replace")` with the
  configured `encoding` (`Session.display`, `session.display_text`); a message with a character above
  U+00FF (not compiler text) and a codec that does not exist are left unchanged, and the server's own
  messages and the internal-error markers are never re-decoded. Display only: `analysis`, the CLI and
  the structured results are unchanged, and quick fixes match on the raw compiler text.
- Known limit: ranges are whole physical lines, not token spans; the only column is the line end,
  counted in the negotiated position encoding (utf-16 by default). A file loaded twice (two occurrences) gets the union of its spans, which can cover
  more lines than one occurrence.

### Test hooks

Read inside `worker.run_analysis` only (inherited by the spawned workers from the server's
environment): `OPENCRBASIC_LSP_TEST_CRASH=<substring>` makes the worker call `os._exit(3)` when
the entry path contains the substring; `OPENCRBASIC_LSP_TEST_SLEEP=<substring>:<seconds>` sleeps
that long first (for timeout tests). `tests/lsp/test_server.py` drives the real server over stdio
with `tests/lsp/lspclient.py`.

### Language features (LSP-wire)

`lsp/features.py` turns the core modules' answers into LSP types (a synchronous adapter that runs in
a worker thread via `asyncio.to_thread`); `lsp/server.py` registers the handlers; `lsp/session.py`
supplies the program. No CRBasic rule is in `lsp/`. Handoff with the measurements and the host
checklist: [LSP-wire-handoff.md](lsp-rounds/LSP-wire-handoff.md).

**Where the program comes from (option b).** The analysis job in the worker pool also returns the
parsed program pickled (`JobResult.program`, about 0.5 MB for `Main.CR6`, about 30 ms to pickle).
The session keeps it per entry as `Analysed` together with the open-document versions the analysis
read (`versions`). The first feature request that needs it unpickles it in a thread (about 25 ms)
and builds an `AnalysisResult` that holds the program and a request (entry, logger, the snapshot's
overlays) but no diagnostics. Objects derived from the program (declared names, hover declarations,
the `Navigation` with its binding index, the include links) are cached on the `Analysed`. There is
no second analysis. The alternative (a), analysing again in a thread of the server process,
was measured and not chosen: see the handoff.

**Which program answers a request.** A file that is an entry uses its own program. A `.crb` uses the
first entry (sorted by path) whose last analysis read it and that has a program. A file no entry
read gets no program: text-based requests still work, the others answer empty.

**Stale data.** A program is *fresh* for a file when the document version it read equals the
document's current version. Completion, hover, signature help and folding are text-based: they read
the current text (and take names from the program, fresh or not) and never wait. The other
requests (document symbols, links, definition, references, highlights, semantic tokens) read the
program: when it is not fresh and an analysis is pending they wait, without blocking the event
loop, until it is, for at most `featureWaitMs` when an older program exists and at most
`analysisTimeoutMs` + 2 s when none does (the first request after opening a document). After that
they answer from the last program (positions may be off while the document changed) or empty. A
request never fails because of stale data; an unexpected exception inside a feature is logged to
stderr and answered with an empty result. Features wait for the first configuration (bounded,
15 s) so the help directory and encoding are known. When the client declares
`workspace.semanticTokens.refreshSupport`, the server sends `workspace/semanticTokens/refresh`
after every new program, since semantic tokens are otherwise only re-requested on edits.

**Position encoding.** The client's `general.positionEncodings` is negotiated by pygls (first
client-listed value of `utf-8`, `utf-16`, `utf-32`; `utf-16` when absent). The conversion is at the
boundary only, in `features.Ctx`, for every feature and for diagnostics. The core modules only see
decoded (Latin-1) columns, which are byte offsets into the compiler bytes.

- An open document's overlay bytes carry its editor text (`positions.EditorBytes`). When the file
  `encoding` replaces characters, the mapping goes through that text, one editor character at a
  time: each counts `len(ch.encode(encoding, "replace"))` bytes and its own width in the negotiated
  position encoding (`positions.compiler_column` in, `positions.editor_character` out). So an
  emoji under `latin-1` is 2 UTF-16 units in the editor and 1 byte to the compiler; editor columns
  after it stay correct for hover, highlights, completion and rename/quick-fix edits, and
  diagnostic ranges use the editor line. Without replacement the mapping is the same byte count.
- A file with no editor text (read from disk for an included or unopened file) maps through its
  bytes with `positions.decoded_position` / `positions.lsp_position`, decoding with `encoding`.
- The per-character mapping assumes a stateless encoding. Inferred from the code, not observed:
  a BOM-writing codec such as `utf-16` adds bytes that no character accounts for, so its columns
  are not checked.
- A line past the end of the document (a stale request) clamps to its last line.

**Help.** `helpDirectory` (a relative path is resolved against the first workspace folder) is read
by one `helpdocs.HelpProvider` per resolved directory, shared behind a lock and indexed in a
background thread when the setting is applied (about 0.5 s for `extracted_chm`, measured). Changing
it needs no re-analysis.

**Mappings that are the adapter's choice.** Completion items: `sortText` keeps the core order
(program names first), `textEdit` replaces the identifier prefix, `detail` is the kind plus the
parameter count; `kind` maps instruction/function/sub/user_function to Function, symbol/constant to
Constant, keyword to Keyword, variable/alias/parameter to Variable, table to Class, type to Struct,
field to Field. Outline kinds: sub/function Function, datatable/consttable Class, structuretype
Struct, member/output Field, const Constant, beginprog Module, scan/subscan/slowsequence Event,
declarations Variable. A file included twice appears once per occurrence in the outline. Document
links carry a target only when the compiler resolved the include. Semantic-token lengths are in the
negotiated unit. Signature-help label offsets are UTF-16 offsets into the label.

Known limits: the outline and links of a file are those of the program that read it, so a `.crb`
shared by several entries shows the first entry's view; `references` and `definition` of a
position in a stale document may land on the wrong name until the analysis arrives; folding of a
file with lone-CR line breaks differs from the editor (as in "Source positions"); a completion at
an empty prefix returns every catalog name (about a thousand items).

### Rename, formatting and quick fixes (LSP-07-wire)

`lsp/server.py` (handlers), `lsp/features.py` (conversion to LSP types), `lsp/session.py`
(`entries_reading`, `current`, `unknown_entries`, `fresh_contributors`). No CRBasic rule is in
`lsp/`: the cores are `rename.py`, `formatting.py` and `quickfix.py`. Handoff and host-test list:
[LSP-07-wire-handoff.md](lsp-rounds/LSP-07-wire-handoff.md).

**Inputs.** `Analysed` now also keeps the job's `reads` (path to sha256), the raw diagnostics and
`ok`/`failures`, and `build()` returns them in the rebuilt `AnalysisResult` (`dependencies.reads`,
`diagnostics`, `ok`, `implementation_failures`). `AnalysisDiagnostic.rendered` is empty and
`dependencies.listings` empty in that rebuilt result.

**Which entries.** `Session.entries_reading(path)` waits (bounded by `analysisTimeoutMs` + 2 s, without
blocking the loop; an evicted program is re-analysed) until no entry the file can affect has an
analysis pending, then returns every entry whose last analysis read the file. An entry is *current*
when its newest analysis produced the program and every open document it read has the version the
analysis saw. Rename and formatting refuse when any such entry is not current (reason `pending`) and
when no entry reads the file (`no_entry`). Rename also refuses while an entry never completed an
analysis (failed, timed out or still queued: what it reads is unknown; reason `unanalysed`).

**Refusals** are LSP errors, code -32803 (`RequestFailed`), message `Cannot rename: <reason text>`,
`data = {"reason": <code>}`. The codes are `rename.REASONS` plus `no_entry`, `pending`, `unanalysed`,
`no_program`. `prepareRename` refuses the same way (the editor shows the message). pygls logs every
exception a handler raises, so the server filters those log records and the error report for its own
refusal exception.

**`textDocument/prepareRename`** returns `{range, placeholder}` (range converted with the negotiated
encoding). **`textDocument/rename`** returns a `WorkspaceEdit` with `documentChanges` only: one
`TextDocumentEdit` per file, `textDocument.version` = the open document's version, `null` for a file
that is not open. Versioned-edit rule: the analyses must match every open document they read
(`current`), no `await` separates that check from the snapshot of the open documents' bytes, and after
the (threaded) computation each edited open document must still have the snapshot's version and its
sha256 must equal the content hash `rename.rename` reports; else the request is refused (`stale`). A
closed file is checked by `rename` itself against the bytes on disk (`stale`). The server passes
`rename.rename` and `prepare_rename` `source=OverlaySource(DiskSource(), <open documents' compiler
bytes>)`, so every file an entry read is also compared with the open buffers as they are now.

**`textDocument/formatting` / `rangeFormatting`** use `FormattingOptions`: `insertSpaces` with
`tabSize` (1 to 16) gives that many spaces per level, otherwise one tab per level. A range that ends at
character 0 of a later line does not include that line. Refusal (empty edit list plus a
`window/showMessage` Info `opencrbasic: Cannot format: ...`): no entry reads the file, an entry is not
current, or `formatting.format_refusal` of any entry that reads the file says the program has errors.
Note this includes errors in other files of that entry.

**`textDocument/codeAction`** (kind `quickfix`; a request whose `only` excludes it answers `[]`): for each
diagnostic of the request context with source `opencrbasic`, the analysis diagnostic with the same
displayed text (with the optional `[Entry] ` prefix) and the same first line is passed to
`quickfix.code_actions`, for every entry whose published diagnostics for the file are from the current
program. Each action carries the originating diagnostic and a `WorkspaceEdit` with one versioned
`TextDocumentEdit` (the open document's version). The same fix from several entries is offered once;
edits outside the requested file are dropped.

### Inlay hints and commands (LSP-09c)

Handoff: [LSP-09c-handoff.md](lsp-rounds/LSP-09c-handoff.md). The cores are "Inlay hints (LSP-09a)"
and "Commands (LSP-09b)"; the adapter adds no CRBasic rule.

**`textDocument/inlayHint`.** Answers from `inlay.parameter_hints` over the whole file, computed once per
parsed program (and help directory) and filtered by the request range (line based; a range that ends
at character 0 of a later line excludes that line). Like the other program-based requests it waits
for a fresh program (`featureWaitMs`); if the program still does not reflect the open document's
version the answer is `[]`, because the lines of a stale program would put hints at the wrong
places. Positions go through `features.Ctx` (negotiated encoding, editor text of open documents).
Each hint is `InlayHint(position, label = "Name:", kind = Parameter, paddingRight = true)`; the label
is the core's, with its trailing colon. `[]` when `inlayHints.parameterNames` is `false`, when no
help directory is configured (the core needs help names) or when the file has no program. A handler
failure is logged and answered `[]`.

**Refresh.** After each new parsed program the server sends `workspace/inlayHint/refresh`, only when
the client declares `workspace.inlayHint.refreshSupport` (same rule as semantic tokens). It is also
sent when a configuration change alters `inlayHints.parameterNames` or `helpDirectory`.

**Commands** (`workspace/executeCommand`, one argument: the document URI string). The entry is the
document itself when it is an entry (configured, or open with an entry extension); a `.crb` takes the
first entry, sorted by path, whose last analysis read it, after pending analyses of the entries it
can affect have finished (bounded, as for rename). The result is a JSON string.

| Command | Result |
|---|---|
| `opencrbasic.showCompilerOutput` | `commands.compiler_output(entry, logger, overlays)`: the text `crbc` prints for the entry, computed from the open buffers as overlays (unsaved text is used, a library buffer is compiled through its entry) and the disk, writing no file. Compiler text is passed through the same latin-1 to file-encoding display step as diagnostics (`session.display`); for ASCII the text equals the core's. |
| `opencrbasic.showTableFields` | `commands.render_table_fields(commands.table_fields(result))` for the entry's current parsed program; Markdown. A program that does not compile gives the core's "No tables" text. |

Refusals are LSP errors (code -32803, `data.reason`), the same shape as rename: `bad_arguments` (not
exactly one string argument, or a non-`file:` URI), `no_entry` (no entry reads the file), and for
table fields `pending` (the entry's analysis is not current) and `no_program`. A command name the
server does not advertise is answered by pygls with -32602.

The VS Code client registers the palette commands `opencrbasic.openCompilerOutput` and
`opencrbasic.openTableFields` (titles "opencrbasic: Show Compiler Output" and "opencrbasic: Show Table
Fields"). The ids differ from the server's because `vscode-languageclient` registers every
advertised server command itself and a second registration of one id fails. They open a read-only
`opencrbasic-output:` document beside the editor whose content is the server command's answer for the
active CRBasic file; running the command again refreshes it.

## Binding index (LSP-05a)

`opencrbasic.bindings` is a read-only index over the parsed program: every declaration, every name
use, and the declarations each use resolves to. Nothing in the compiler or the checkers calls it,
so compiler acceptance is unchanged (`tools/selfdiff.py`: 0 differ). Handoff with the observed
corpus counts: [LSP-05a-handoff.md](lsp-rounds/LSP-05a-handoff.md).

```python
ix = bindings.build(result.program, "cr6")      # AnalysisResult.program, or parser.parse_file()[0]
ix.declarations, ix.references                  # tuples, in source (emission) order
ix.failures                                     # IndexFailure(index, file, line, message)
ix.unparsed                                     # Unparsed statements the parser could not take apart
ix.declarations_named("x"), ix.references_to(decl), ix.records_in(stmt), ix.counts()
ix.lookup("x", routine=None, role="read")       # Resolution(candidates, ambiguity)
ix.token_of(record)                             # lexer Token (col into record.unit.text) or None
bindings.name_tokens(stmt, "x", member=False, string=False)   # token-based, strings skipped
bindings.segment_starts(logical_text)           # [(offset, segment text)] as the parser splits on ':'
```

`build` never raises for a program (a bad profile name is still `ValueError`). Each statement is
indexed inside its own `try`: on an internal error its records are discarded and one
`IndexFailure` is kept; resolution failures are recorded the same way.

### Records

`Declaration` (identity, not value, distinguishes records): `name` (source spelling), `kind`
(`public dim static const sub function param alias datatable consttable structuretype objecttype
structure_member label field`, plus `system_table` and `external` for predefined targets), `scope`
(`global`, `routine`, `structure`, `table`, `predefined`), `routine` (the owning `SubDef`, scope `routine`),
`container` (owning `StructureType`/`ConstTable`), `file`, `line` (the compiler's), `node` (the AST
node: `VarDecl`, `ConstDecl`, `Param`, `SubDef`, `Instruction`, `Label`), `stmt` (innermost
statement), `unit` (top-level statement of the `:`-segment: the owner of the text), `index`
(position of `unit` in `program.statements`), `type`, `quoted`, `external` (the `CatalogEntry`),
`alias_target` (a `Reference`), `unverified_scope`, `modelled` (a table whose fields are all declared),
`site` (a `field`: see "Table fields").

`Reference`: `name` (spelling at the use), `role`, `file`, `line`, `resolved` (tuple), `ambiguity`
(text), `container` (member references: the table/structure searched), `routine`, `node`, `stmt`,
`unit`, `index`, `base` (member references: the base's reference), `is_write`, `forward`, and
`status`: `resolved` (one proven target), `ambiguous` (several candidates, or a stated reason),
`unmodelled` (a member of a table whose field names the index cannot list: a `Status`/`Settings`/
`DataTableInfo` name outside the profile data, any member of a DataTable whose derivation failed;
see "Table fields" below), `unresolved` (nothing found).

Roles, as far as the AST tells: `read`, `write` (assignment and `For` targets), `call` (`Call`,
bare Sub calls, an indexed name that resolves only to Subs/Functions/built-in functions),
`member_base`, `member` (`is_write` marks an assigned member), `alias_target`, `units_target`,
`table_ref` (a name that resolves only to tables), `argument` (a top-level instruction or call
argument: the AST does not say whether the instruction reads or writes it), `type` (an `As` type
word: structure types and predefined type symbols), `instruction` (the keyword of an instruction
statement, resolved to the catalog).

### Scope policies and evidence

| Policy | Evidence |
|---|---|
| Resolution is case-insensitive on AST names; the declared spelling is kept. | `symbols.py` upper-cases every lookup. |
| Outside a routine, `Public`/`Dim`/`Const`, `Sub`/`Function`, `Alias`, tables and structure types are global. A routine is the span from a `Sub`/`Function` statement to `EndSub`/`EndFunction` or the two-word `End Sub`/`End Function` (`bindings.closes_routine`, shared with hover; LSP-R1 finding 1). | `symbols.scoped_view` (`docs/checker.md` "Scope-aware lookups and statement identity (round 8)"; `tests/test_scope_shadow.py`). The compiler's `scoped_view` tests only the one-word closers; see [LSP-R1-handoff.md](lsp-rounds/LSP-R1-handoff.md). |
| `Dim`, `Const` and parameters inside a routine are local and win over a global of the same name; the same parameter name in two routines is two declarations. | `docs/checker.md` ("`Sub` parameters and `Dim`s inside a `Sub`/`Function` may shadow"); `tests/fixtures/const_shadow_sub.json` and `sub_param_const.json` (a parameter named like a `Const` declared after the Sub is local). |
| A local that shadows a global `Const` declared earlier is a compile error the index does not model; the local still wins. | `const_shadow_sub.json`. |
| `Sub`/`Function` names are global and order-free (forward references resolve). Variables and constants are also resolved order-free, but `Reference.forward` is true when every candidate is declared in a later statement. | `symbols.declared_subs` (whole program); order for variables is unverified (`declared_names` "deliberately global"), hence the flag. |
| CR6/CR1000X: a local `Dim`/parameter whose name is a built-in function resolves to the local. CR300/CR350: it does not shadow the built-in, so the reference is `ambiguous` with candidates (built-in, local). | `docs/checker.md` "Function-named assignment targets and CR300/CR350 local shadows" and "Expression expects (" section. The CR6 side rests on the accepted arithmetic probe; "CR6 scoping of a Sub-local" is listed there as unfinished. |
| `Public` and `Alias` (and labels) inside a routine: scope inside the routine is recorded (`scope="routine"`, `unverified_scope=True`) and a use elsewhere gets that candidate with an `ambiguity` note. | No fixture establishes their visibility. |
| Two or more global candidates stay `ambiguous` with a reason: duplicate declarations (the compiler rejects them), the same include used twice, or a table sharing a variable's spelling. | `tests/fixtures/routine_names.json` (duplicate Sub, variable/Function clash). The vendor sample `avgrun_1.CR6` declares `Public TCTemp` and `DataTable (TCTemp,...)`; whether the namespaces are separate is not established here, so no choice is made. |
| `Table.Field` of a user `DataTable`: resolves to the `field` declaration of that table (see "Table fields"). `Public.X` resolves to a global `Public`/`Alias` of that name (inference from the Public table's contents, not oracle-tested). `ConstTable.Name` resolves to the `Const` inside that table. | `symbols.system_table_names`, `const_table_fields`; `tests/fixtures/table_members_scope.json`; field names come from `tables.py`. |
| Structure members resolve through the base variable's declared type (also through nested members); an unknown member is `unresolved`. | `checker/structs.py` model (`_structure_members`, `_member_chain`). |
| Predefined targets: names the catalog has (symbols, functions, instructions, per profile), plus `NAN` and `INF`, are `external` declarations (`index == -1`, shared per entry). Instruction keywords resolve to the catalog instruction, and a token the parser made an instruction (`instruction` role) never resolves to a user declaration of the same spelling (LSP-R1 finding 2). | `catalog.py`; `checker/expressions._EXTRA_PREDEFINED_SYMBOLS`; the parser's `S.Instruction` classification ([LSP-R1-handoff.md](lsp-rounds/LSP-R1-handoff.md)). |
| `Alias A(1) = N1, N2`: each right-hand name is an `alias` declaration with `alias_target` = the reference of `A`; `Units V = label`: `V` is a `units_target` reference. | `parser._parse_alias`, `_parse_units_segment`. |

### Inactive code, strings, includes

- Statements the parser did not keep (inactive `#If` branches, text after the first `EndProg`) are
  not in `program.statements` and so are not indexed. LSP-02 keeps those lines mappable.
- String literals are never references. A table named by a string (`DataTable ("Hist",...)`) is a
  declaration with `quoted=True`; `CallTable("Hist")` is not a reference to it. Rename must treat
  quoted tables as not safely renamable.
- `Static` is not a parser declaration (observed: `Static Q` is an `Unparsed` statement), so
  nothing is indexed for it. Labels are indexed when the parser makes
  a `Label`.
- A file included twice yields two declaration objects (different `unit`/`index`); a use then lists
  both and is `ambiguous` ("the same declaration is included more than once"). `file` is the
  compiler display name (`CPU:Lib.crb`).

### Positions for LSP-05b

The AST has no name columns, so records keep what is needed: `unit` (the top-level statement) and
`occurrence`, the 0-based ordinal of the record among the same-name tokens of `unit.text`
(identifier tokens before a `.` are a separate class from those after it; string tokens a third).
`ix.token_of(record)` tokenizes `unit.text` with `opencrbasic.lexer` and returns that token; its
`col` is an offset into `unit.text`. For a logical column, add the offset of the segment: use
`segment_starts(logical_text)` and take the segment whose text equals `unit.text` (for an inline
`If` body the text is a slice of the `If` statement's segment, and the occurrence is counted over
the whole segment; a `Units` statement's text is stripped). `Stmt.line` is the last physical line
of a continued statement, so physical positions come from LSP-02 provenance. Where the statement
text cannot be tokenized (a lex error) or the parser rewrote tokens (`DataTable(15Min,...)`),
`token_of` returns `None`; 5b must treat that as "no range". Occurrences are exact for the
statement kinds the AST models fully; array-parameter dimensions and `Alias` junk after the first
name are not in the AST and can shift later same-name occurrences in that statement (observed
corpus rate in the handoff).

### Table fields (LSP-05c)

`Declaration` kind `field`. `Table.Field` and `Table.Field(i)` resolve by name (case-insensitive)
to the fields of the table's declaration.

- User `DataTable`: scope `table`, `container` = the table declaration, in TDF order, from
  `ix.fields_of(table)` (they are not in `declarations_named`, not in `records_in`, and follow the
  statement declarations in `ix.declarations`). Names, order, `FieldNames` renaming and
  Alias splitting are `tables.py`'s: `bindings` imports its private helpers (`_scan_program`,
  `_build_public_table`, `_emit_output_fields`, `_apply_fieldnames`, `_split_by_alias`,
  `_alias_source`, `_fft_field`, `_build_data_table`) and mirrors only `_build_data_table`'s grouping
  of output instructions with their `FieldNames`. A table whose names differ from
  `_build_data_table`'s own list, or whose derivation raises (the latter also adds an
  `IndexFailure`), stays unmodelled (`table.modelled` False). `name_only` fields (FFT, CH400
  source) are not in the TDF and not declared. A table written as a string is also derived.
- `node` is the output instruction; `site` says what locates the field:
  `"fieldnames"` (a `FieldNames` string names it: `unit` is that statement, `token_of` gives the
  whole string token, `quoted=True`, so a rename must refuse it) or `"instruction"` (name derived
  from the Source, e.g. `TC_Avg`: `stmt`/`line`/`file` locate the output instruction, `token_of`
  is `None`). No token is given for derived names because the only token in that statement is the
  Source variable's own, which another record already owns; a second record at that range would
  make definition list the field for the variable and make a rename of the variable see a quoted
  target.
- A user table with no field of that name also resolves the `TableName.<property>` members
  (`BytesPerRecord EventCount EventEnd Output Record TableBytes TableFull TableSize TimeStamp`) to a
  predefined `field` (`site="property"`, `scope="predefined"`, shared per table); this list is from
  `extracted_chm/Info/datatableaccess.htm`. Any other member of a modelled table is `unresolved`.
- `Status`, `Settings`, `DataTableInfo`: predefined `field` declarations (shared per table, `index
  -1`) for names the profile data lists: `Status` = the CR6 `Status` TDF fields
  (`tables._status_table`) plus `settings_fields.json` `status`; `Settings` = its `settings`;
  `DataTableInfo` = `tables._datatableinfo_table` (Status and DataTableInfo for the `cr6` profile
  only). `Status` also takes the property list (`Status.TimeStamp`). A name outside the lists stays
  `unmodelled`, not `unresolved`: the project uses `Status.CardBytesFree`, which is in neither
  Status list.
- Navigation: `semantic_tokens` skips kind `field` (not in its legend map); `definition` on a
  `FieldNames`-named field's use gives the string token; references to it list the use sites.

### Not modelled (recommendations only)

- Fields of tables the TDF derivation does not name (structure-variable tables, `CPIStatus`,
  `CH400Status`, `ConstTable` fields other than their `Const`s), and `Static` declarations.
- Whether tables and variables share a namespace; visibility of `Public`/`Alias`/labels inside a
  routine; use-before-declare for variables. Each needs a focused oracle probe.
- A checker could later take its declared-name and scoped-view helpers from this index, only after
  a differential gate (`tools/selfdiff.py` plus the fixtures above) shows equal behaviour.

## Hover and signature help (LSP-04b-core)

`opencrbasic.hover` is the protocol-independent logic behind hover and signature help (pure
functions and frozen dataclasses; no pygls). Positions in and out are physical: 0-based line and
decoded-character column, as in "Source positions"; the server converts encodings. Brief:
`docs/lsp-rounds/LSP-04.md`; handoff: `docs/lsp-rounds/LSP-04b-core-handoff.md`.

```python
call_context(text, line, column) -> CallContext(name, active, line, column) | None
signature_help(name, active, profile, help_provider=None) -> SignatureInfo | None
hover(result, text, line, column, profile, help_provider=None, declared=None) -> Hover | None
declarations(program) -> {UPPER_NAME: [Declaration, ...]}
```

`profile` is a profile name or a `Profile`. `help_provider` is any object with `lookup(name)`
(`helpdocs.HelpProvider`); None means no help directory is configured.

- **`call_context`**: the innermost call around the position and the 0-based index of the argument
  the position is in. The statement is the physical line joined with its `_` continuations
  (a comment ends a line, so a `_` inside one continues nothing). Strings (`""` escape) and `'`
  comments are skipped; only top-level commas of the call count; grouping parentheses and `{}`
  array literals are transparent (`A((1,2), 3)` is still A, argument 0). Incomplete text answers:
  no `)`, a trailing comma, an unterminated string (a string ends at its line). A position in a
  comment, outside any call, past the text, or with a negative column gives None; a column past the
  end of the line clamps. The name is as written and may be dotted (`Status.Battery`) or end in
  `$`. `If`, `While`, `And`, `Then` and the other grammar words that may precede `(` are not
  calls. `line`/`column` locate the start of the name (a call can span several physical lines).
  A paren-less statement call (`Foo a, b`) is not recognised.
- **`signature_help`**: instruction (preferred) or builtin function, spelled as the profile
  spells it. Parameters pair with help through `helpdocs.pair_signature`: help names and text when
  the help count equals the catalog count, otherwise `arg1..argN` for the catalog count with
  `documentation` empty per parameter; the signature `documentation` then still carries the help
  summary and syntax line and a sentence saying the names are not applied. Nothing is paired by
  guess. Instruction parameters carry the catalog `type_code`, its `type_codes.json` `kind` (the
  names of `docs/type-codes.md`, none invented) and `optional` (code >= 100, or the help syntax
  says `optional`). Functions take the count from the table's `nargs` (a trailing argument may be
  optional; `checker/expressions.py`) unless the checker's own tables give accepted counts
  (`_ARITY_ALLOWED`, `_ARITY_RANGE`, or `_ARITY_MIN_SAFE` and `_ARITY_MAX_SAFE` both equal to
  `nargs`; imported by `hover._accepted_counts`, not copied). When the largest accepted count equals
  the help parameter count, help names pair with the catalog positions, parameters after the
  smallest accepted count are marked optional, and the documentation states the accepted counts
  (FTPClient: 6, 10 or 11, so 11 names with 6 required; HTTPPut: 4, 8 or 9; NetworkTimeProtocol:
  3 or 4). The optional marking is a simplification: it cannot show that FTPClient rejects 7-9.
  Otherwise `arg1..argN` for the table count. Functions have no type kind (`argtypes` is not
  decoded). A function
  with no `nargs` and no help parameters (operators such as `And`, `Not`, `To`), a constant, a
  keyword-only word, a dotted name or an unknown name gives None. `active_parameter` is clamped to
  `[0, count - 1]` (None for no parameters). `label_span` of each parameter indexes into `label`
  (the LSP `ParameterInformation.label` offset form). Descriptions are Markdown-escaped and one
  line.
- **`hover`**: the identifier at the position (or ending at it) when it is code: not in a comment
  or string, not a number or `&H`/`&B` literal, not a member after `.`. Declared names win, then
  the catalog:
  - declared (from `declarations(result.program)`, or the `declared` argument that LSP-05a
    bindings can supply): `Public`/`Dim`/`Static` (type as written, upper case; "none written
    (Float)" when there is no `As`), dimensions, string size, `Units` label (from `Units` statements),
    `Const` (value resolved by the parser when it could be folded), `Sub`/`Function` with parameters,
    `DataTable`/`ConstTable` names and `Alias` names; each with the declaring file (compiler
    display name) and the first physical line of the statement (`positions.line_span`; the reported
    line when unknown). A name declared several times (locals of different Subs) lists up to five
    and says scope is not resolved, because `hover` does not know which file or Sub the position is
    in; `Declaration.scope` names the enclosing Sub/Function.
  - instruction or function: title (help title when help exists), signature, summary, one bullet
    per parameter with `type_codes.json` kind and optional marker plus the help description, the
    mismatch sentence when help and catalog counts differ, and a provenance line naming the help
    page (for example `Source: help page Instructions/voltse.htm`). With no help text the line is an
    explicit placeholder, never inferred text: `Help: not configured (No help directory configured).`
    when no help directory is set, `Help: still to do (No help page for this name).` when the
    directory has no page for the name. The same placeholders appear in signature documentation, and
    the catalog facts (parameter count, type codes) are still shown. A name that is also a predefined
    constant gets an "Also a predefined constant" line.
  - predefined constant: `NAME = value` from the symbol table. Keyword-only words (`Then`, `Step`,
    `As`, `Optional`): one line. Operators without an argument count: the table row.
  The result carries the word's line and columns for the hover range.
- `declarations` reads only the statements the parser kept (not an inactive `#If` branch) and takes
  `Units` from separate `Units` statements (`VarDecl.units` is not filled by the parser). Parameters
  of Subs and fields of tables are not declarations here. The AST is from the last analysis; names
  from a stale AST are shown as they were.

Not covered, and not observed: rendering in an editor, UTF-16 conversion (the server's job), the
effect of a stale analysis while typing. Help text is Campbell Scientific documentation shown
verbatim-reduced (internal use, `docs/legal.md`); help names never change checker rules.

## Editing features (LSP-03b-core)

`opencrbasic.editing` holds the logic behind the outline, folding, document links and completion.
It is pure Python (no pygls), changes no compiler module (`tools/selfdiff.py --compare` 0 differ)
and is wired to the protocol by LSP-03b-wire. Brief and criteria: `docs/lsp-rounds/LSP-03.md`;
hand-off notes: `docs/lsp-rounds/LSP-03b-core-handoff.md`.

### Ranges

Every range is *before LSP position-encoding conversion*: `Span(start_line, start_col, end_line,
end_col)` with **0-based** physical lines and 0-based decoded (Latin-1) columns, i.e. byte offsets
into the physical line. `end_col=None` means "the end of `end_line`"; the AST carries lines only, so
symbol ranges are whole-line spans (`start_col == 0`, `end_col is None`). The server converts with
`positions.lsp_position(data, line + 1, col, ...)` (that function takes a 1-based line) and takes
the line length from the file bytes for `end_col=None`. `FoldRange` carries line numbers only.
Text arguments (`folding_ranges`, `completion_candidates`) are the buffer decoded as the compiler
decodes, Latin-1, so a column is an index into the line of that string.

### API

| Function | Result |
|---|---|
| `document_symbols(result, path, profile=None) -> list[Symbol]` | Outline of the file at `path` from `result.program`. |
| `folding_ranges(text, profile="cr6") -> list[FoldRange]` | Block, `#If` and comment-run folds from the buffer text alone. |
| `include_links(result) -> list[IncludeLink]` | One entry per `Include` / `IncludeSection` statement of every file of the program. |
| `completion_candidates(result, text, line, column, profile="cr6", declared=None) -> Completion` | Candidates at a position. `result` may be None or stale. |
| `declared_names(program, profile="cr6") -> list[DeclaredName]` | The `(name, kind, scope)` list completion uses by default (LSP-05a's binding index replaces it). A quoted `DataTable("Hist",...)` is declared as `Hist` (kind `table`), as in the outline. |
| `block_pairs(profile) -> dict` | Opener word -> closer word of the profile (upper case). |

### Block words

`block_pairs` is derived from the instruction catalog (`catalog.InstructionCatalog`), not typed in:
an instruction row `X` whose row `EndX` exists is a pair (If/EndIf, Select/EndSelect, Sub/EndSub,
Function/EndFunction, ConstTable/EndConstTable, StructureType/EndStructureType, ObjectType,
DialSequence, ModemHangup, SubMenu, ProChiPri, ApplyAndRestartSequence). Eight pairs are not named
that way and are listed in `editing._IRREGULAR_PAIRS`: For/Next, Do/Loop, While/Wend,
DataTable/EndTable, BeginProg/EndProg, Scan/NextScan, SubScan/NextSubScan,
SlowSequence/EndSequence. A pair is used for a profile only when both rows are in its instruction
table. The two-word closers (`End If`, `End Sub`, `Next Scan`, `Next SubScan`) are `End`/`Next`
followed by the identifier that completes a closer word. `Case`, `Else`, `ElseIf` do not fold.

`tests/lsp/test_editing.py` pins the CR6 pair table (`EXPECTED_CR6_PAIRS`), checks each word is an
instruction row in all four profiles that have it, and checks with `parser.parse_text` that the
parser reads each opener and each closer (one-word and two-word forms) as the statement
`_ast_event` expects. The same classification serves the text scan (`folding_ranges`, completion
scope) and the AST (`document_symbols`, `declared_names`).

One rule is not from the catalog: a `SlowSequence` without `EndSequence` (this project's `Main.CR6`
and CHM samples write none, and the compiler accepts them) is taken to end where the next
`SlowSequence` or the enclosing `EndProg` begins. The compiler's own rule was not observed.

### Folding

`folding_ranges` reads the buffer with the source loader (`source.load_text`, includes unresolved),
so continuation lines, comments and case follow the compiler; a line the loader calls an overflow
(over 510 characters) ends its pass, and loading restarts after it so one long line does not hide
the rest of the file. Per statement (split on top-level `:` like the parser) it takes the first
word, plus the second for `End`/`Next`; words in strings and comments never count. An `If` is a
block only in its multi-line form: `Then` ends the statement and, on CR6 and CR1000X, no further
`:` segment follows it (`If c Then : x` is a one-line If, as the parser reads it). Matching is by nearest opener of the same family: a closer closes the
innermost open block of its family and drops blocks opened inside it that were never closed; a
closer without an opener is ignored; an unclosed block gives no range. `#If`..`#EndIf` fold as one
range with `#ElseIf`/`#Else` inside; conditions are not evaluated, so inactive branches fold and
their own blocks fold too. Two or more consecutive comment-only lines give a `comment` fold. A
range runs from the opener's first line to the line before its closer (the closer stays visible)
and exists only with at least one line between header and closer.

### Outline

`document_symbols` walks `result.program.statements`, attributes each statement to physical lines
with `Program.line_map` (`positions.reported_spans`) and nests by block:

| `kind` | Statement | Children |
|---|---|---|
| `sub`, `function` | `Sub` / `Function` (detail: parameters and return type) | `parameter` per parameter |
| `datatable` | `DataTable` (a quoted `DataTable("Hist",...)` is listed as `Hist`; LSP-R1 finding 4) | `output`: each instruction in the body that is not a catalog keyword (so `Units` is left out; `DataInterval`, `Sample`, ... stay) |
| `consttable` | `ConstTable` | `const` |
| `structuretype` | `StructureType` | `member` |
| `beginprog`, `scan`, `subscan`, `slowsequence` | the block words | declarations inside, nested blocks |
| `public`, `dim`, `static`, `const`, `alias` | declarations at file level | none |

`range` is the header through the closer; `selection` is the header (both whole-line spans over
all physical lines of a continued statement). A block that is never closed has its header only.
Declarations inside a `Sub`/`Function` are not listed. Only code the compiler reads has symbols:
inactive `#If` branches and text after `EndProg` do not appear. The result lists the symbols of
the file at `path`; for a file included twice there is one entry per `source.Occurrence`
(`Symbol.occurrence`), at the same physical lines. Which occurrence a statement belongs to is
inferred from order (the reported line going back means the next load), because the AST records no
occurrence; an occurrence inside an inactive `#If` makes that attribution unreliable. `[]` when
`result.program` is None or the path is not in the program.

### Include links

`include_links` finds the statements with the loader (`IncludeLink.range` is the file name inside
the quotes, on the physical line where the literal is; `keyword` is the spelling written) reading
each file of `Program.occurrences` through `OverlaySource(DiskSource(), request.overlays)`, so
unsaved buffers count. `target` is `Program.include_paths[spec.strip()]` and is None for a missing
file. A file included twice is listed once. With no parsed program only the entry file is scanned
and every target is None.

### Completion

`completion_candidates` returns a `Completion(items, replace, prefix, member_of, is_incomplete)`. `replace` is the
identifier prefix before the cursor. There are no candidates inside a string (odd number of `"`
before the cursor) or after a `'`. After `name.` the items are the fields of the table called
`name` from `tables.build_tables(result.program)` (Status, Public, each DataTable, each
StructureType variable), `kind="field"`; unknown name or no result: none. Otherwise the items are
the program's names plus the catalog's. Catalog entries use the profile's spelling and one item per
name: the language word (`keyword`) beats the instruction row, then instruction, function, symbol.
`param_count` is `len(params)` for an instruction and `nargs` (else `len(argtypes)`) for a
function where the table has them, None otherwise; it counts table parameters, optional ones
included. Program names are `(name, kind, scope)`; `scope=None` is global, otherwise the name of a
Sub/Function and the name is offered only inside it. The current routine is found from `text`
(unclosed routines included), not from the possibly stale `result`. `declared=` replaces the AST
walk with a caller-built list. Program names sort before catalog names, then case-insensitively.

LSP-03c: at most `editing.COMPLETION_LIMIT` (200) items are returned, program names first, so the
list is the first 200 of the complete order. `is_incomplete` is True when more matched; the server
must then send `CompletionList.isIncomplete = true` so the client re-asks as the prefix grows (the
current `lsp/features.py` sends `False`; see `docs/lsp-rounds/LSP-03c-handoff.md`). The cap applies
to member lists too. The catalog's completion items are built once per profile (`_catalog_items`,
cached; the items are immutable), not per call.

LSP-03d: the routine-scope scan (`_scan`, the block scan of the whole buffer that completion uses to
find the current Sub / Function, and folding uses) is cached in a bounded LRU of 8 entries
(`_SCAN_CACHE_SIZE`), keyed by `(text, profile)`; the key is the text itself, so a hit is exact. It
returns a tuple. `_scan.__wrapped__` is the uncached function. A call on a buffer already scanned
skips the scan: on `Main.CR6` the completion call drops from about 16 ms to about 2.4 ms (p50, see
`docs/lsp-rounds/LSP-03d-handoff.md`). A changed buffer is a miss and costs what it did before. The
cache changes no result: `test_completion_on_main_cr6_cached_equals_uncached` compares every line
of `Main.CR6` through the cache and through the uncached scan.

### Known limits

- Everything depends on what the parser accepts: a `Sub` header the parser cannot read (it
  rejects `ByVal`/`ByRef`, for instance) has no outline entry, though it still folds.
- Folding does not evaluate `#If` and does not know a `Case` branch; `Else`/`ElseIf` branches do
  not fold on their own.
- Outline columns are not available (the AST has lines only): selection ranges are whole lines.
- Completion offers every catalog name at an empty prefix and does not look at the statement
  context (argument position, expected type). Locals are matched by routine name.

## Navigation (LSP-05b-core)

`opencrbasic.navigation` is the protocol-independent logic behind go to definition, find
references, document highlights and semantic tokens (no pygls). It reads an `AnalysisResult` and
the binding index ("Binding index (LSP-05a)"); it changes no compiler output. Positions in and out
are physical: 0-based line and decoded-character column (a byte offset into the line, "Source
positions"); the server converts to the client's position encoding with `positions.lsp_position`.
Handoff: [LSP-05b-core-handoff.md](lsp-rounds/LSP-05b-core-handoff.md).

```python
definition(result, path, line, col, *, index=None)                  -> [Location]
references(result, path, line, col, include_declaration=True, *, index=None) -> [Location]
document_highlights(result, path, line, col, *, index=None)         -> [Highlight]
semantic_tokens(result, path, *, index=None)                        -> [SemanticToken]
Navigation(result, index=None)     # the same four methods; builds the ranges once
```

`index` is a `BindingIndex` for `result.program`; when omitted one is built with
`result.request.logger`. The module functions reuse one `Navigation` while the same
`AnalysisResult` object is passed, so a new analysis rebuilds it. `path` is matched against the
occurrence paths as given (absolute) and symlink-resolved.

- `Location(path, file, line, start, end, occurrences)`: the range of a declared or referenced name
  (`start` inclusive, `end` exclusive). `occurrences` lists the `Occurrence.id` of every load of
  that file that gives the same range (a file included twice is one editor range, two loads). Sorted
  by path, line, column.
- `Highlight(line, start, end, kind)`, `kind` `write` (a declaration; an assignment or `For` target;
  an assigned member), `read` (read, call, member base, member, table reference, alias target,
  type, instruction) or `text` (instruction `argument` and `Units` targets, where the AST does not
  say whether the name is read or written). Same-file only, all loads of that file.
- `SemanticToken(line, start, length, type, modifiers)`: declared names of `path`, sorted, one
  per position; `length` is in decoded characters. Only declarations are tokens, references are not.

### Accessors for rename (LSP-05d)

The methods above return `Location`s only. `rename` needs the record behind a range, so the module
exposes the same ranges with their records (no behaviour change):

```python
Site(rec, path, file, occurrence, line, start, end)    # NamedTuple; rec is a Declaration or Reference
Navigation.sites_at(path, line, col)                   -> [Site]
Navigation.site_of(record)                             -> Site | None
Navigation.occurrence_paths()                          -> {int: Path | None}
path_keys(path)                                        -> {str}
```

- `sites_at`: the sites whose name covers the position (0-based line, decoded column). A position
  inside a name selects that name; a position just after its last character selects it only when no
  name contains the position. Empty when none.
- `site_of(record)`: the site of a declaration or reference of this analysis, `None` when its range
  is not exact (see "Range policy" below).
- `occurrence_paths()`: `Occurrence.id` to the file of that load (`None` when unresolved); id 0 is
  the entry.
- `path_keys(path)`: the normalised absolute and symlink-resolved spellings under which a path is
  matched.

### Policy

- The position is mapped through ranges, not text: each record's token (`BindingIndex.token_of`)
  plus the statement's `origin` and the loader's `Piece`s give a physical range. A position inside
  a name, or just after its last character, selects that record; anything else (whitespace,
  comment, string content, keyword the index does not record) selects nothing.
- A declaration at the position is its own target. A reference gives its resolved candidates: all
  of them when ambiguous (`definition` returns every candidate, `references` the union of their
  references), none when unresolved.
- External (catalog) and predefined declarations have no location: `definition` is empty for
  instruction names, built-in functions, `Status`, `Public`, `NAN`. `references` still lists
  references to them in the program. The server may show hover for these.
- Table fields (LSP-05c): `Tab.Field` resolves to a `field` declaration. A `FieldNames` field's
  location is its whole string literal; a derived-name field (`TC_Avg`) has no source range, so
  definition returns nothing for it. `Status.X`/`Settings.X` members are predefined (no location).
- Names in an inactive `#If` branch, after `EndProg`, or in a statement the parser could not take
  apart are not indexed, so they give nothing.
- Range policy: a token from one physical piece gets an exact range; a token whose characters
  cross a continuation join, or whose provenance or token is missing, gets none and is skipped
  (never manufactured). A quoted `DataTable("T")` name is a declaration whose range includes the
  quotes. Digit-leading table names (rewritten by the parser) have no token and no range.
- `occurrences`: declarations of a doubly included file are distinct records but share one editor
  range; they are merged into one `Location`.

### Parser attribute

`parser.py` sets `stmt.origin = (LogicalLine, segment offset)` on each top-level statement of a
logical line (purely additive; not a dataclass field, so equality and `repr` are unchanged). The
segment offset is the start of the statement's `:`-segment in `LogicalLine.text`; navigation
verifies `unit.text` against the logical text (a `Units` text is stripped) before using it.

### Semantic-token legend

Types (`TOKEN_TYPES`, wire index = position): `variable`, `parameter`, `function`, `property`,
`namespace`, `struct`, `type`. Modifiers (`TOKEN_MODIFIERS`): `declaration`, `readonly`.

| Declaration kind | Type | Modifiers |
|---|---|---|
| `Public`, `Dim`, `Static`, `Alias` | variable | declaration |
| `Const` | variable | declaration, readonly |
| `Const` inside a `ConstTable` | property | declaration, readonly |
| `Sub` parameter | parameter | declaration |
| `Sub`, `Function` | function | declaration |
| structure member | property | declaration |
| `DataTable`, `ConstTable` | namespace | declaration |
| `StructureType` | struct | declaration |
| `ObjectType` | type | declaration |

Labels and predefined/external declarations are not tokens. A constant is a `variable` with
`readonly` because LSP has no standard `constant` type.

### Not covered

Rendering in an editor, encoding conversion (server), rename (needs `quoted` tables refused),
table field-level references, references through a stale AST while typing.

## Inlay hints (LSP-09a)

`opencrbasic.inlay` is the protocol-independent logic behind parameter-name inlay hints (no pygls).
Positions are physical: 0-based line and decoded-character column ("Source positions"); the server
converts encodings. Handoff: [LSP-09a-handoff.md](lsp-rounds/LSP-09a-handoff.md).

```python
parameter_hints(result, path, start_line, end_line, profile, help_provider) -> [InlayHint]
InlayHint(line, column, label, kind="parameter", padding_right=True)
```

- `path` is a file of the analysis (matched like Navigation: absolute and symlink-resolved);
  `start_line..end_line` are 0-based physical lines, both inclusive. The result is sorted by position
  and has no duplicates (a file included twice gives one hint per position).
- A hint stands at the first character of each positional argument of an instruction call (the
  statement's first word, `Scan(...)`, `VoltSe(...)`) or a builtin-function call anywhere in a
  statement (including nested calls). `label` is the parameter name plus `:`, exactly as
  `hover.signature_help` labels it, and only when that signature is `paired` with help names: with no
  help provider, no help page, or help whose count does not pair, there are no hints and never an
  `argN` hint. Names are shown as help gives them (for example `"FieldName"` with its quotes, or the
  help-parsing artefact `ASCIIString(1,1,X)` for `ASCII`).
- No hint: for an empty argument slot; when the argument text equals the parameter name
  (case-insensitive); for an argument past the parameter count; for a name declared in the program
  (declared names win, as in hover); for a member call after `.`; for a paren-less statement call
  (`Foo a, b`) or an instruction that is not first in its statement (the body of an inline `If`);
  for a statement the lexer cannot tokenize.
- Positions come from the AST, not from text search: the statement's own text is tokenized, and a
  token's logical offset (`stmt.origin` segment offset plus the token column) is mapped through
  `positions.exact_range`. A hint needs the argument's first token in one physical piece; an argument
  that continues over `_` lines is hinted at its first token, and an argument whose first token itself
  crosses a join gets none. The argument may span pieces after that token.
- `const_value_hints` is not implemented. The function never raises (any failure gives `[]`).

## Formatting (LSP-07b)

`opencrbasic.formatting` is the protocol-independent indentation formatter (no pygls, no other
module changed). It imports `editing`'s private `_roles`, `_classify`, `_block_events` and
`_tolerant_tokens`, so the block structure is the one folding and the outline use. Brief:
`docs/lsp-rounds/LSP-07.md`; evidence and counts: `docs/lsp-rounds/LSP-07b-handoff.md`.

| Function | Result |
|---|---|
| `format_indentation(text, profile="cr6", indent="  ") -> list[TextEdit]` | Edits for the whole buffer. |
| `format_range(text, start_line, end_line, profile="cr6", indent="  ")` | The same edits restricted to the 0-based physical lines `start_line..end_line` (levels come from the whole text). |
| `apply_edits(text, edits) -> str` | Applies edits (test and tooling helper). |
| `format_refusal(result) -> str \| None` | Reason formatting must not be offered (the analysis has errors or failed internally); None: offer it. |

`TextEdit(line, start, end, new_text)`: `line` 0-based, columns decoded (Latin-1), `start` is always
0 (only leading whitespace is replaced). `text` is the decoded buffer; `\r` of CRLF is never part
of an edit. `indent` must be non-empty spaces and tabs (`ValueError` otherwise).

Rules:

- Every opener of `editing.block_pairs` indents its body one level; the closer returns to the
  opener's level (`End If`, `Next Scan`, ... too). `BeginProg` and `SlowSequence` are blocks, so
  `SlowSequence` sits one level inside `BeginProg` (this repository's `Main.CR6` writes it at
  column 0; a formatted copy differs there). A `SlowSequence` ends at the next one or at `EndProg`.
- `Else`/`ElseIf` sit at the level of their `If`. `Case` sits one level inside `Select`, its body
  one deeper (the repository's own style); `Case Else` likewise.
- Continuation lines (a statement continued with `_`) are one level deeper than the first line.
- Comment-only lines take the level of the next statement, or the current block depth when the
  next line is a directive or there is none.
- `#If`/`#ElseIf`/`#Else`/`#EndIf`/`#UnDef` are set to column 0 and do not change the level. Blocks
  opened inside a branch are tracked per branch; if the branches leave different blocks open,
  nothing after the `#EndIf` is edited.
- Blank and whitespace-only lines are not touched.
- Left alone: a statement with an unterminated string; a continuation line that begins inside a
  string; every line after an unclosed block opener, after a closer with no opener, after an
  `#EndIf` whose branches disagree, after the first overflow line, and after the top-level
  `EndProg` (the compiler stops reading there).
- A change that would push a physical line (or a continued statement, counted together as the
  loader does) over the 510-character buffer is dropped for that statement.
- Files with errors: formatting is not offered (`format_refusal`). The function itself works on
  text alone.

Preservation: for the programs in `crbasic/` (three indents) and the 198 CHM programs that compile
on `cr6`, the formatted copy gives identical `ok`, mode, diagnostics, TDF bytes and table list
(`ProgSignature` excluded), and formatting twice equals formatting once
(`tests/lsp/test_formatting.py`). Declaration initializers that the compiler pulls from following
lines are not modelled by the formatter's loader (`pull_init` off); those lines are indented as
separate statements.

## Quick fixes (LSP-07c)

`opencrbasic.quickfix` is the protocol-independent core of quick fixes (no pygls; server wiring is
a separate chunk). It imports `bindings` (resolution, `lookup`), `navigation` (exact token ranges;
it reads `Navigation._site_of`, a private mapping) and `catalog`. Brief:
`docs/lsp-rounds/LSP-07.md`; handoff: `docs/lsp-rounds/LSP-07c-handoff.md`.

```python
quickfix.code_actions(result, path, diagnostic, profile, *, index=None, source=None) -> list[CodeAction]
quickfix.apply_edits(data: bytes, edits) -> bytes        # test and tooling helper
quickfix.edit_distance(a, b) -> int                      # case-insensitive Damerau-Levenshtein
```

`result` is an `AnalysisResult`, `diagnostic` one of its `AnalysisDiagnostic`s, `path` the file the
actions are for (edits are only returned for that file), `profile` a profile name or `Profile`.
`CodeAction(title, edits, kind="quickfix", family)`; `Edit(path, line, start, end, new_text)` with
`line` 0-based and columns decoded (Latin-1) as in `navigation.Location`. `source` (default: disk
plus `result.request.overlays`) is read only by the include family. The call never raises for a
program; no match means `[]`.

### Message families

Matched against `AnalysisDiagnostic.message` (the compiler text, exactly as `docs/messages.tsv`
and the checker print it). The referenced name is a `bindings.Reference` with status `unresolved`
on the diagnostic's physical line span (`diagnostic.span`); a diagnostic without a span gives
nothing, except the line-less link message below.

| Family | Message pattern | References considered |
|---|---|---|
| undefined symbol | `Invalid symbol in expression: <statement>.` (CR6, CR1000X); `<name> is not defined.` (CR300, CR350; the name must match) | every unresolved name on the line (read, write, call, argument, table, member base, alias/units target) |
| undefined call or assignment target | `Invalid, or out of place expression: <statement>.` (the generic fallback; all loggers) | unresolved call and write references on the line |
| undefined Sub | `Sub <name> not defined.` (no Sub declared; has a line); `<name> is not declared as a Subroutine.` (line 0, the link step) | unresolved call references of that name, in `path` |
| missing include | `Cannot open include file  <name>.` (two spaces; CR6/CR1000X print the name without the drive) | the include string on the line |

`Unknown instruction: <text>.` is the `#` directive stop (`checker/__init__.py`, observed with
`#Frob`); there is no table of directives to propose from, so it gets no action. An unknown
instruction written without `#` is one of the `Invalid, or out of place expression` or `Sub ... not
defined` lines above.

### Candidates and ranking

Names come from the binding index (every declaration whose `lookup(name, ref.routine, ref.role)`
at the use gives candidates and no ambiguity: a local of another routine is never offered) and
the profile's catalog (instructions, functions, symbols, plus `NAN` and `INF`). A candidate must fit
the role: an assignment target takes variables and parameters only; a bare `Call X` only Subs; a
call statement Subs, Functions and catalog instructions/functions; a table reference tables; any
other read variables, constants, functions and symbols (arguments also tables). Distance is the
unrestricted Damerau-Levenshtein distance, case-insensitive, at most 2 (at most 1 when the
misspelled name has up to 3 characters; a distance of 2 requires the same first letter, which
removes short catalog names such as `PWR` for `pars`). Order: distance, user-declared before
catalog, upper-cased name; at most 5 per reference. The inserted spelling is the declaration's
own; a catalog name uses the spelling the program already writes for it, else the profile table's.
The distance and first-letter rules are this project's choice, not compiler evidence.

For the include family, candidates are the files in the directory listing of the including file
(sources provider, so unsaved buffers count) whose names end `.crb` or `.cr6`, are not the entry
program, and are within the same distance of the written name; only the file-name part of the
string changes (a `cpu:` drive stays). The loader already matches include names case-insensitively
(`source._resolve_include_path`), so a case-only mismatch never produces this message; the family is
for misspelled names. Names with a path separator get nothing.

### Limits

- An edit needs an exact one-slice token range (`navigation`); a name from a continuation join that
  splits it, a statement the parser could not take apart, or an unparsed token gives no action.
- A file loaded twice (two includes of one library) can give the same edit once; the candidates of
  the first load's reference win.
- The compiler stops reporting after some errors (pass-1 errors hide the later checks), so a fix may
  expose further diagnostics; each is handled in turn.
- Only text replacement of one token: no action creates a declaration, adds an `Include`, or deletes
  code.
- Evidence: `tests/lsp/test_quickfix.py` applies each fix and compiles again (`compile_file` through
  `analyze`), and sweeps one seeded misspelling per `crbasic/*.CR6` program (see the handoff for the
  rate). Compilation shows the fixed program is accepted, not that the chosen name is what the
  author meant.

## Rename (LSP-07a)

`opencrbasic.rename` is the protocol-independent core of `textDocument/prepareRename` and
`textDocument/rename` (no pygls). It uses the binding index and the ranges of `navigation` only: the
edits are the declaration's range plus the range of every reference that resolves to it, in every
entry. No source text is searched to find occurrences. Positions in and out are physical, a 0-based
line and a decoded-character column (a byte offset into the line, "Source positions"); the server
converts to the client's encoding. Handoff with the observed sweep counts:
[LSP-07a-handoff.md](lsp-rounds/LSP-07a-handoff.md).

```python
prepare_rename(results, path, line, column, source=None)            -> PrepareResult(ok, refusal, path, line, start, end, placeholder, files)
rename(results, path, line, column, new_name, source=None)          -> RenameResult(ok, refusal, edits, versions)
TextEdit(line, start, end, new_text)                   # NamedTuple, physical 0-based, end exclusive
Refusal(code, message)                                 # code is one of rename.REASONS
```

`results` is a list of `AnalysisResult`, **one per configured entry that reads `path`, the
declaration's file or an edited file** (the caller selects them). The clicked binding is resolved in
the results that read `path` and parse the position's line (one whose reads do not include `path`,
or that does not parse the line, for example because an `IncludeSection` or an inactive `#If`
leaves that part of the file out, is skipped there). Then, until nothing changes, every other result
that reads the declaration's file or an edited file joins: the same declaration is found in it at the
declaration token's file and position, and its references, freshness and collisions are checked like
the first. A result that parses an edited line but not the declaration is `entries_disagree`; one
that parses none of them is skipped. `PrepareResult.files` is every file holding the declaration or
a reference (LSP-R3a).

**Server selection (LSP-R3a).** `prepare_or_rename` starts with the entries reading the request
file; while the core's touched files (`PrepareResult.files`, or the edited files of a rename)
include one some entry outside the set reads, it adds the entries reading it (each must be current,
else `pending`) and recomputes. Renaming a use in entry A of a declaration in a library that entry
B also includes therefore edits and checks B too. An entry that never completed an analysis refuses the request (`unanalysed`)
as before. On success `edits` is `{Path: [TextEdit]}`, sorted, non-overlapping and
deduplicated across entries (a library included by several entries is edited once), and `versions`
is `{Path: sha256 hex}` of the bytes each edited file has now, which equal what it had when it was
analysed. The caller (the server) must apply the edits only to a document whose content still has
that hash.

**Freshness (LSP-R2a).** Before any binding or collision check, **every** file in
`dependencies.reads` of each used result, edited or not, is read again at the call and its sha256
compared with the recorded one; a difference (or a file that is gone) is `stale`. The bytes come from
`source`, an optional `SourceProvider` (`sources.py`) giving the current content of any file; the
default is each result's own `request.overlays` on the disk. A server holding newer unsaved buffers
than the analyses should pass an `OverlaySource` of them, otherwise rename sees the disk and the
analysis' overlays. The per-result cache of `rename.py` serves only checks derived from the analysis
(unindexed code, string words); edit coordinates and `versions` come from the fresh read. Not
detected: a file that did not exist at analysis and appears later (the loader records it only as a
directory listing, which changes for unrelated files too).

### Refusals (all-or-nothing; `Refusal.code`)

| Code | Refused when |
|---|---|
| `no_results`, `no_ast` | no analysis depends on the file; the program could not be parsed |
| `stale` | any file the analysis read (edited or not) differs now from the bytes it read, is gone, or differs between entries |
| `index_failure` | the binding index recorded a failure for a statement of an entry |
| `not_a_name` | the position is not on a name the index records in some entry that parses that line (comment, string, keyword the index does not record) |
| `unresolved`, `ambiguous`, `unmodelled` | the reference at the position, or any reference to the target, is not one proven single target (duplicate declarations, a table sharing a variable's spelling, a `Public` declared inside a routine used elsewhere); `unmodelled` is a table field name (`Tab.Field`, `Status.X`) |
| `external` | the target is predefined: catalog instruction, function, symbol or keyword, `Status`, `Public`, `Settings`, `NAN`, `INF` |
| `quoted` | the target is a table named by a string (`DataTable("T", ...)`); string names are never rewritten |
| `kind` | the declaration kind has uses the index does not record (labels) |
| `no_range` | the declaration or a reference has no exact physical range (token across a continuation join, digit-leading table name, no token) or its range is not the name |
| `text_mismatch` | the bytes at an edit range are not the name (index and text disagree) |
| `occurrences_differ` | a file loaded more than once gives declarations with different edit sets |
| `entries_disagree` | the entries do not agree on the declaration set (the target's physical range and kind) |
| `unparsed` | a statement the parser could not take apart mentions the name |
| `unindexed_code` | the old name occurs, in code of any entry that is in no statement (inactive `#If` branch, `#If` directive, text after `EndProg`), at a place no entry edits; or the new name occurs there |
| `table_field` | for a variable or alias: a `Table.Field` use whose field name equals the name or starts with `name_` (field names are derived from variable names and are not modelled) |
| `unchanged` | the new name is spelled exactly as the current one (a case-only change is allowed) |
| `invalid_name` | not `[A-Za-z_][A-Za-z0-9_]*` (the lexer's identifier rule); a trailing `$` is refused too |
| `reserved_name` | the new name is a keyword, instruction, function or symbol of the profile (`InstructionCatalog.get`, any kind), `Status`, `Public`, `Settings`, `DataTableInfo`, `NAN` or `INF` |
| `collision` | see below |
| `overlap` | two edits overlap (should not happen) |

### Collision rule (case-insensitive, checked in every entry)

- Target is a **global** (`Public`, `Dim`, `Const`, `Sub`, `Function`, `Alias`, table, structure
  type): refused if any declaration of any scope, including routine locals and parameters, has the
  new name (conservative: a global is visible in every routine). Structure members are separate.
- Target is a **routine local** (`Dim`, `Const`, parameter): refused if a global, a declaration
  with unverified scope, or a local of the same routine has the new name.
- Target is a **structure member**: refused if another member of the same structure type has it.
- In all cases a reference with the new name that is not part of the rename (for example an
  undeclared name) is also a collision, since renaming would bind it. A member use (`X.name`) counts
  when its container is the target's namespace: the owning type or `ConstTable` of a structure
  member or table constant, and the `Public` table for a `Public` variable or alias (`Public.newName`
  is bound by a `Public newName`). Member uses of other containers (`Status.newName`, data table
  fields) are not captures.

### Notes

- Public use: `rename.py` gets the declaration and reference behind a range through the accessors
  of "Navigation (LSP-05b-core)", "Accessors for rename (LSP-05d)" (`sites_at`, `site_of`,
  `occurrence_paths`, `path_keys`). It reads no private name of `navigation.py`.
- Diagnostics are not required to be clean: a program with errors can be renamed when every check
  above passes. The sweep compares diagnostics before and after.
- Not covered: names inside strings (`FieldNames("x")`, `CallTable("T")`), data table field names,
  `Static` declarations, anything after `EndProg` or in inactive `#If` branches (refused when the
  old name occurs there), the 510-character loader line limit for a longer new name, a maximum
  identifier length (no evidence found, not checked), and editor behaviour.

## Type checking (LSP-08e)

The language-server modules (listed in `pyproject.toml` `[tool.pyrefly]`) are type-checked with
Pyrefly: `uv run --no-sync pyrefly check` must report 0 errors. Compiler modules are outside the
gate. Fixes are annotations and narrowing; the few remaining suppressions are the pre-existing
`# type: ignore[...]` lines in `lsp/server.py` (pygls attributes set dynamically). The one
attribute read from a compiler module that its class does not declare (`TdfTable._dup_fields`) is
read with `getattr` in `bindings.py`.

## Commands (LSP-09b)

`opencrbasic.commands` is the core of two editor commands; the server wiring is LSP-09c. Neither
function writes a file (tested: the temp tree, the cwd and the source tree are unchanged).

```python
compiler_output(entry, logger="cr6", overlays=None) -> CompilerOutput(text, ok, program_name)
table_fields(result) -> list[TableInfo(name, fields: tuple[FieldInfo(name, type, units, process,
                                       dimension)], interval)]
render_table_fields(tables) -> str          # Markdown
```

- `compiler_output` is `compiler.compile_file(entry, profile, display_name=entry.name,
  source=OverlaySource(DiskSource(), overlays), signature=False)` and `"\n".join(result_lines())`
  plus the newline `print` adds. `text` therefore equals the stdout of
  `crbc --logger L --no-message-file ENTRY` (header with the file name only, `line N:` messages cut
  by the 4096-byte buffer, warnings, mode). `crbc`'s `-s`, `-u`, `-t`, `-b` are not offered. An entry
  that is neither on disk nor in `overlays` gives the CLI's `<path> -- Compile Failed!` / `No Program
  File` text (the path as given, absolute). `ok` is `CompileResult.ok`.
- `table_fields` takes an `AnalysisResult` and calls `tables.build_tables(program, profile,
  consts)`, the call `tables_hook.attach` makes, so tables and fields are the TDF's, in TDF order,
  `Status`, `DataTableInfo` and `Public` included. `type` is the TDF type name (`IEEE4`, `FP2`,
  `LONG`, `String`, `NSEC`, `Boolean`, ...; an unlisted code is `type N`); `units` and `process` are
  `None` when empty (`process` is the TDF processing text, `Avg` for `Average`); `dimension` is the
  field's element count. `interval` is the `-l` text (`5 MIN`) or `None`. The result is `[]` when the
  program did not compile (the compiler builds tables only after a clean check) or `build_tables`
  raises. It imports `tables.build_tables` and its private `_seconds_to_display` and reads the
  private `_interval_display` attribute that `render_table_list` also reads.
- Tests: `tests/lsp/test_commands.py` (stdout equality with in-process `cli.main` on temp copies
  for all `../crbasic/*.CR6`, every 25th CHM sample, an unsaved overlay and an overlay-only entry;
  table names, fields, units and processing equal to `build_tables` for `../crbasic/`).

## Test tiers (LSP-08d)

Environment for every command, from `opencrbasic/`: `export UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic PYTHONPATH=$PWD/src`
(and `uv run --no-sync`). `pytest-xdist` is in the dev group; `-n 4` runs four workers, and
`OPENCRBASIC_STRICT=1` is set by `tests/conftest.py` in every one of them
(`tests/lsp/test_strict_mode.py` fails otherwise).

| Tier | Selection | Command |
|---|---|---|
| fast | everything in `tests/lsp` except `slow` | `pytest tests/lsp -m "not slow" -n 4` |
| slow | corpus sweeps above ~3 s | `pytest tests/lsp -m slow -n 4` |
| server | tests that start the server subprocess | `pytest tests/lsp -m server -n 4` |
| LSP, once per integration | fast + slow | `pytest tests/lsp -n 4` |
| full | the whole suite | `pytest -q -n 4` |

Markers are registered in `pyproject.toml` and `--strict-markers` is on: a misspelt marker is an
error. `server` is a module-level mark on the subprocess test files; the server tests use
`tmp_path` and a private process each, so they run in parallel.

`tests/lsp/conftest.py` provides `memo_analyze`: a per-process memoised `analyze()` for unmodified
repository sources (`crbasic/`, `tests/corpus/`). The cache key is the request (entry, logger,
`warn_unused`, `signature`); a hit is returned only while every file and directory listing the
stored result depends on still has the hash it was computed from. It raises for overlays and for
paths outside the repository sources, so tests that write or overlay files cannot use it. The
returned result is shared: tests must not mutate it.

`tools/lsp_gate.py [--base REV] [--run]` prints (or runs) the gates a change needs, from
`git diff --name-only <base>...HEAD` plus the working tree: compiler or unclassified files -> full
suite and selfdiff; LSP-only files -> selfdiff and `tests/lsp`; `vscode/**` -> `npm test`; files in
`[tool.pyrefly].project-includes` or `pyproject.toml` -> `pyrefly check`; documentation only -> a
relative-link check (`--check-links FILE...`). Its classification is unit-tested in
`tests/lsp/test_lsp_gate.py`.
