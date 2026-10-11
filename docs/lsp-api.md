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
ParamHelp(name, link, description, optional=False)
pair_signature(name, type_codes, help) -> PairedSignature(matched, help_count, catalog_count, params, note)
render_markdown(help, sig=None) / render_plain(help, sig=None) / render_signature_label(sig)
coverage_report(profile, provider, names=None) -> CoverageReport(total, with_help, matching, mismatches, without_help)
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
  `(or Alias)`) is not a parameter; `[ X ]` brackets are dropped from the name and do not set
  `optional`, which follows the word `optional` only.
  `description` comes from the page's Parameters section, else the linked parameter page, else `""`.
  `optional` is `True` only where the syntax line says `optional`; `False` means "not stated".
- Pairing is by count only. `pair_signature` uses help names when `len(params)` equals the catalog
  type-code count and otherwise returns `matched=False` with a note and placeholder labels
  (`arg1`...): no guessing. Help names never change checker rules; types come from the catalog.
- Rendering is separate from parsing and returns text; Markdown escapes the help text.
- Provenance: the help is Campbell Scientific documentation, read from the local directory.
  Internal use only, so copying it is permitted ([legal.md](legal.md), 2026-10-11); LSP-04a's
  tests nevertheless use synthetic fixtures (`tests/lsp/fixtures/help/`). The server (LSP-04b) takes the directory from the
  setting `opencrbasic.helpDirectory`, default none: no help is shown unless configured.
- Coverage (observed, CR6 catalog against the local `extracted_chm/`): see
  [LSP-04a-handoff.md](lsp-rounds/LSP-04a-handoff.md). Documentation coverage is not
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
- Range: compiler line N is LSP line N-1, characters 0 to the end of that line in the file's
  current text (open document, else disk decoded with `encoding`); line 0 is LSP line 0; a line
  past the end clamps to the last line. A diagnostic whose file cannot be resolved (`path` is
  `None`) is anchored at line 0 of the entry with the text `<file>:<line>: ` prepended.
- The implementation-failure marker diagnostics (`Internal error: unable to compile program.` /
  `... parse program.`) and `implementation_failures` are published with source
  `opencrbasic (internal)`, severity error, the failure strings appended to the message. Timeout,
  worker crash and scheduler errors are published the same way at line 0 of the entry.
- Publishes carry the document `version` for open documents. A file that had diagnostics and now
  has none gets an empty publish; identical consecutive publishes are suppressed.
- An open `.crb` that no analyzed entry read gets one Information diagnostic at line 0: `No
  configured entry program includes this file; diagnostics come from the entry programs that
  include it.` (only after every entry has completed a first analysis).
- Known limit: compiler line numbers are not yet mapped to physical lines, so a diagnostic can sit
  on the wrong line after an initializer pull moved the compiler's line numbering (LSP-02
  fixes this); ranges are whole lines, not token spans; columns are UTF-16 only for the line end.

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
structure_member label`, plus `system_table` and `external` for predefined targets), `scope`
(`global`, `routine`, `structure`, `predefined`), `routine` (the owning `SubDef`, scope `routine`),
`container` (owning `StructureType`/`ConstTable`), `file`, `line` (the compiler's), `node` (the AST
node: `VarDecl`, `ConstDecl`, `Param`, `SubDef`, `Instruction`, `Label`), `stmt` (innermost
statement), `unit` (top-level statement of the `:`-segment: the owner of the text), `index`
(position of `unit` in `program.statements`), `type`, `quoted`, `external` (the `CatalogEntry`),
`alias_target` (a `Reference`), `unverified_scope`.

`Reference`: `name` (spelling at the use), `role`, `file`, `line`, `resolved` (tuple), `ambiguity`
(text), `container` (member references: the table/structure searched), `routine`, `node`, `stmt`,
`unit`, `index`, `base` (member references: the base's reference), `is_write`, `forward`, and
`status`: `resolved` (one proven target), `ambiguous` (several candidates, or a stated reason),
`unmodelled` (a `Table.Field` or `Status.X` field name: the table is found, field names are not
modelled), `unresolved` (nothing found).

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
| `Table.Field`: the base resolves to the table; the field part is `unmodelled` for a `DataTable` and for `Status`, `Settings`, `DataTableInfo` (exists only once a `DataTable` does). `Public.X` resolves to a global `Public`/`Alias` of that name (inference from the Public table's contents, not oracle-tested). `ConstTable.Name` resolves to the `Const` inside that table. | `symbols.system_table_names`, `const_table_fields`; `tests/fixtures/table_members_scope.json` (field names come from `FieldNames` and the output instruction, `tables.py`; not re-derived here). |
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

### Not modelled (recommendations only)

- Data-table field names (`Table.Field` definitions, `FieldNames`): would need `tables.py`'s
  field derivation exposed for reuse.
- Whether tables and variables share a namespace; visibility of `Public`/`Alias`/labels inside a
  routine; use-before-declare for variables. Each needs a focused oracle probe.
- A checker could later take its declared-name and scoped-view helpers from this index, only after
  a differential gate (`tools/selfdiff.py` plus the fixtures above) shows equal behaviour.
