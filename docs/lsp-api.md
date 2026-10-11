# Language-server API

Contracts for editor tooling on top of the compiler. Specification history: `docs/lsp-rounds/LSP-00.md`
("Contracts for LSP-01"). The server section is added by LSP-01b.

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
  `params` are the parameter links of the form for the looked-up name, in syntax order.
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
