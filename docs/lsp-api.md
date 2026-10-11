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
  unchanged. Signature help for functions uses `nargs` as the count (see Hover).
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
language rules: all CRBasic knowledge comes from `analysis.analyze`. Modules: `server.py` (pygls
handlers, `main`), `session.py` (documents, entries, scheduling, diagnostics merge; no pygls
import), `config.py`, `worker.py` (the pool job; imports only the core compiler). The package
`__init__` imports nothing third-party, so analysis workers never load pygls.

### Launch

`python -m opencrbasic.lsp` (or the `crbc-lsp` script, `opencrbasic.lsp.server:main`), stdio
transport. With the editable install in a git worktree, set `PYTHONPATH=<worktree>/opencrbasic/src`
or the main checkout is imported. First stderr line:

```
opencrbasic-lsp: opencrbasic=<path to opencrbasic/__init__.py> api_version=<N> git=<short sha|unknown>
```

The revision comes from `git -C <package dir> rev-parse --short HEAD` (failure is ignored). The
server was written for `analysis.API_VERSION == 1`; any other value prints
`opencrbasic-lsp: refusing to start: ...` and exits with status 2. Exit status after `shutdown` +
`exit` is 0; `exit` without `shutdown` (or stdin closing) is 1. Everything the server logs goes to
stderr; stdout carries only protocol frames.

### Capabilities

`textDocumentSync` with `openClose`, `change = 1` (Full) and `save`; diagnostics are pushed
(`textDocument/publishDiagnostics`), there is no pull provider. pygls additionally advertises
`positionEncoding` utf-16, `workspace.workspaceFolders` (changes are ignored until LSP-06) and an
empty `executeCommandProvider`; the server implements no command and no other language feature.

### Configuration

Keys (defaults in `config.py` follow LSP-00 "Configured entries"): `entries` (`["**/*.CR6"]`),
`exclude` (`[]`), `logger` (`"cr6"`), `encoding` (`"utf-8"`), `debounceMs` (300),
`analysisTimeoutMs` (10000). Sources, in order: `initializationOptions`, then, when the client
declares `workspace.configuration`, a `workspace/configuration` request for section `opencrbasic`
(after `initialized` and on every `workspace/didChangeConfiguration`; a pulled value overrides the
initial one key by key). A client without pull support may send the section in the
`didChangeConfiguration` params (`settings` or `settings.opencrbasic`). An invalid value is
ignored with a stderr line and the previous value kept. Changing `logger` or `encoding`
re-analyzes every entry; changing `entries`/`exclude` re-discovers (entries that drop out have
their diagnostics cleared); `debounceMs` and `analysisTimeoutMs` apply to later jobs.

Entry discovery globs the workspace folders (`entries` minus `exclude`; workspace-relative,
case-insensitive; `*`, `?`, `**`, `{a,b}`; directories starting with `.` and `node_modules` are not
walked) at startup and on configuration change; file watching is LSP-06. An opened file with
extension `.CR6` (case-insensitive; additionally the extension of the configured logger, e.g.
`.CR1X`) is an entry for itself while it is open, configured or not. A `.crb` is never an entry.

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
- An open `.crb` that no analyzed entry read gets one Information diagnostic at line 0: `No
  configured entry program includes this file; diagnostics come from the entry programs that
  include it.` (only after every entry has completed a first analysis).
- Known limit: ranges are whole physical lines, not token spans; columns are UTF-16 only for the
  line end. A file loaded twice (two occurrences) gets the union of its spans, which can cover
  more lines than one occurrence.

### Test hooks

Read inside `worker.run_analysis` only (inherited by the spawned workers from the server's
environment): `OPENCRBASIC_LSP_TEST_CRASH=<substring>` makes the worker call `os._exit(3)` when
the entry path contains the substring; `OPENCRBASIC_LSP_TEST_SLEEP=<substring>:<seconds>` sleeps
that long first (for timeout tests). `tests/lsp/test_server.py` drives the real server over stdio
with `tests/lsp/lspclient.py`.

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
| Outside a routine, `Public`/`Dim`/`Const`, `Sub`/`Function`, `Alias`, tables and structure types are global. A routine is the span from a `Sub`/`Function` statement to `EndSub`/`EndFunction`. | `symbols.scoped_view` (`docs/checker.md` "Scope-aware lookups and statement identity (round 8)"; `tests/test_scope_shadow.py`). |
| `Dim`, `Const` and parameters inside a routine are local and win over a global of the same name; the same parameter name in two routines is two declarations. | `docs/checker.md` ("`Sub` parameters and `Dim`s inside a `Sub`/`Function` may shadow"); `tests/fixtures/const_shadow_sub.json` and `sub_param_const.json` (a parameter named like a `Const` declared after the Sub is local). |
| A local that shadows a global `Const` declared earlier is a compile error the index does not model; the local still wins. | `const_shadow_sub.json`. |
| `Sub`/`Function` names are global and order-free (forward references resolve). Variables and constants are also resolved order-free, but `Reference.forward` is true when every candidate is declared in a later statement. | `symbols.declared_subs` (whole program); order for variables is unverified (`declared_names` "deliberately global"), hence the flag. |
| CR6/CR1000X: a local `Dim`/parameter whose name is a built-in function resolves to the local. CR300/CR350: it does not shadow the built-in, so the reference is `ambiguous` with candidates (built-in, local). | `docs/checker.md` "Function-named assignment targets and CR300/CR350 local shadows" and "Expression expects (" section. The CR6 side rests on the accepted arithmetic probe; "CR6 scoping of a Sub-local" is listed there as unfinished. |
| `Public` and `Alias` (and labels) inside a routine: scope inside the routine is recorded (`scope="routine"`, `unverified_scope=True`) and a use elsewhere gets that candidate with an `ambiguity` note. | No fixture establishes their visibility. |
| Two or more global candidates stay `ambiguous` with a reason: duplicate declarations (the compiler rejects them), the same include used twice, or a table sharing a variable's spelling. | `tests/fixtures/routine_names.json` (duplicate Sub, variable/Function clash). The vendor sample `avgrun_1.CR6` declares `Public TCTemp` and `DataTable (TCTemp,...)`; whether the namespaces are separate is not established here, so no choice is made. |
| `Table.Field` of a user `DataTable`: resolves to the `field` declaration of that table (see "Table fields"). `Public.X` resolves to a global `Public`/`Alias` of that name (inference from the Public table's contents, not oracle-tested). `ConstTable.Name` resolves to the `Const` inside that table. | `symbols.system_table_names`, `const_table_fields`; `tests/fixtures/table_members_scope.json`; field names come from `tables.py`. |
| Structure members resolve through the base variable's declared type (also through nested members); an unknown member is `unresolved`. | `checker/structs.py` model (`_structure_members`, `_member_chain`). |
| Predefined targets: names the catalog has (symbols, functions, instructions, per profile), plus `NAN` and `INF`, are `external` declarations (`index == -1`, shared per entry). Instruction keywords resolve to the catalog instruction. | `catalog.py`; `checker/expressions._EXTRA_PREDEFINED_SYMBOLS`. |
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
  optional; `checker/expressions.py`) and have no type kind (`argtypes` is not decoded). A function
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
    page (for example `Source: help page Instructions/voltse.htm`) or "No help directory configured." /
    "No help page for this name." A name that is also a predefined constant gets an "Also a
    predefined constant" line.
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
| `declared_names(program, profile="cr6") -> list[DeclaredName]` | The `(name, kind, scope)` list completion uses by default (LSP-05a's binding index replaces it). |
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
| `datatable` | `DataTable` | `output`: each instruction in the body that is not a catalog keyword (so `Units` is left out; `DataInterval`, `Sample`, ... stay) |
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

`completion_candidates` returns a `Completion(items, replace, prefix, member_of)`. `replace` is the
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
