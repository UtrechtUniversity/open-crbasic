"""Language-feature answers as LSP types. A protocol adapter over the core modules.

Each function is synchronous and meant for a worker thread (`asyncio.to_thread`): it calls
`editing`, `hover` or `navigation` and converts what they return. The core modules speak physical
0-based lines and decoded (Latin-1) columns; `Ctx` converts both ways using the negotiated position
encoding and the configured file encoding (through the editor text of an open document, else
through its bytes), and nothing else. There are no CRBasic rules here.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Callable, Mapping

from lsprotocol import types

from .. import editing, formatting, hover as hovering, inlay, navigation, positions, quickfix, rename

LEGEND = types.SemanticTokensLegend(token_types=list(navigation.TOKEN_TYPES),
                                    token_modifiers=list(navigation.TOKEN_MODIFIERS))

_COMPLETION_KIND = {
    "instruction": types.CompletionItemKind.Function,
    "function": types.CompletionItemKind.Function,
    "symbol": types.CompletionItemKind.Constant,
    "keyword": types.CompletionItemKind.Keyword,
    "variable": types.CompletionItemKind.Variable,
    "constant": types.CompletionItemKind.Constant,
    "alias": types.CompletionItemKind.Variable,
    "sub": types.CompletionItemKind.Function,
    "user_function": types.CompletionItemKind.Function,
    "table": types.CompletionItemKind.Class,
    "type": types.CompletionItemKind.Struct,
    "parameter": types.CompletionItemKind.Variable,
    "field": types.CompletionItemKind.Field,
}
_SYMBOL_KIND = {
    "sub": types.SymbolKind.Function, "function": types.SymbolKind.Function,
    "parameter": types.SymbolKind.Variable,
    "datatable": types.SymbolKind.Class, "output": types.SymbolKind.Field,
    "consttable": types.SymbolKind.Class, "const": types.SymbolKind.Constant,
    "structuretype": types.SymbolKind.Struct, "member": types.SymbolKind.Field,
    "beginprog": types.SymbolKind.Module, "scan": types.SymbolKind.Event,
    "subscan": types.SymbolKind.Event, "slowsequence": types.SymbolKind.Event,
    "public": types.SymbolKind.Variable, "dim": types.SymbolKind.Variable,
    "static": types.SymbolKind.Variable, "alias": types.SymbolKind.Variable,
}
_HIGHLIGHT_KIND = {"text": types.DocumentHighlightKind.Text,
                   "read": types.DocumentHighlightKind.Read,
                   "write": types.DocumentHighlightKind.Write}
_FOLD_KIND = {"region": types.FoldingRangeKind.Region, "comment": types.FoldingRangeKind.Comment}


def _key(path: Path) -> str:
    return str(path).lower()


class Ctx:
    """What one request needs to convert positions: the encodings, the documents' compiler bytes
    (a snapshot taken on the event loop), their URIs, the profile and the help provider.

    Every conversion goes through here. A document whose bytes carry its editor text
    (`positions.EditorBytes`) is mapped through that text, so a character the file encoding
    replaced keeps its editor width; any other file is mapped through its bytes."""

    def __init__(self, profile: str, position_encoding: str, file_encoding: str,
                 blobs: Mapping[Path, bytes], uris: Mapping[Path, str], help_provider: Any = None):
        self.profile = profile
        self.encoding = position_encoding
        self.file_encoding = file_encoding
        self.help = help_provider
        self._blobs = {_key(p): d for p, d in blobs.items()}
        self._uris = {_key(p): u for p, u in uris.items()}
        self._lines: dict[str, list[bytes] | None] = {}
        self._editor: dict[str, list[str] | None] = {}

    def data(self, path: Path) -> bytes | None:
        found = self._blobs.get(_key(path))
        if found is not None:
            return found
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def lines(self, path: Path) -> list[bytes] | None:
        key = _key(path)
        if key not in self._lines:
            data = self.data(path)
            self._lines[key] = None if data is None else positions.physical_lines(data)
        return self._lines[key]

    def editor_lines(self, path: Path) -> list[str] | None:
        """The editor text lines of an open document, or None when the document has no editor
        text (a file read from disk)."""
        key = _key(path)
        if key not in self._editor:
            found = self._blobs.get(key)
            text = getattr(found, "text", None)
            self._editor[key] = None if text is None else positions.text_lines(text)
        return self._editor[key]

    def uri(self, path: Path) -> str:
        return self._uris.get(_key(path)) or Path(path).absolute().as_uri()

    def decoded(self, path: Path, position: types.Position) -> tuple[int, int] | None:
        """LSP position -> (0-based line, decoded column); a line past the end clamps to the last."""
        lines = self.lines(path)
        if not lines:
            return None
        line = min(max(position.line, 0), len(lines) - 1)
        editor = self.editor_lines(path)
        if editor is not None:
            return line, positions.compiler_column(editor[line], position.character,
                                                   encoding=self.encoding,
                                                   file_encoding=self.file_encoding)
        _, col = positions.decoded_position(lines, line, position.character,
                                            encoding=self.encoding,
                                            editor_encoding=self.file_encoding)
        return line, col

    def position(self, path: Path, line: int, col: int | None) -> types.Position:
        """(0-based line, decoded column or None for the line end) -> LSP position. A line past
        the end of the file clamps to its last line."""
        lines = self.lines(path) or [b""]
        line = min(max(line, 0), len(lines) - 1)
        col = len(lines[line]) if col is None else col
        editor = self.editor_lines(path)
        if editor is not None:
            return types.Position(line, positions.editor_character(
                editor[line], col, encoding=self.encoding, file_encoding=self.file_encoding))
        out_line, character = positions.lsp_position(
            lines, line + 1, col, encoding=self.encoding, editor_encoding=self.file_encoding)
        return types.Position(out_line, character)

    def range(self, path: Path, span: editing.Span) -> types.Range:
        return types.Range(self.position(path, span.start_line, span.start_col),
                           self.position(path, span.end_line, span.end_col))

    def columns(self, path: Path, line: int, start: int, end: int) -> types.Range:
        return types.Range(self.position(path, line, start), self.position(path, line, end))


def _guard(ana: Any):
    """Core calls on one program run one at a time (the modules cache on the program and the
    feature calls are milliseconds)."""
    return ana.guard if ana is not None else contextlib.nullcontext()


def _memo(ana: Any, key: str, make: Callable[[], Any]) -> Any:
    return ana.cached(key, make) if ana is not None else make()


def _text(ctx: Ctx, path: Path) -> str | None:
    data = ctx.data(path)
    return None if data is None else data.decode("latin-1")


# ---- completion, hover, signature help (text based; the program supplies names) -------------


def completion(ctx: Ctx, ana: Any, result: Any, path: Path,
               position: types.Position) -> types.CompletionList:
    text, at = _text(ctx, path), ctx.decoded(path, position)
    if text is None or at is None:
        return types.CompletionList(is_incomplete=False, items=[])
    program = getattr(result, "program", None)
    with _guard(ana):
        declared = _memo(ana, "declared", lambda: editing.declared_names(program, ctx.profile)) \
            if program is not None else None
        found = editing.completion_candidates(result, text, at[0], at[1], ctx.profile,
                                              declared=declared)
    where = ctx.range(path, found.replace)
    items = []
    for n, item in enumerate(found.items):
        detail = item.detail or item.kind
        if item.param_count is not None:
            detail += f" ({item.param_count} parameter{'' if item.param_count == 1 else 's'})"
        items.append(types.CompletionItem(
            label=item.label, kind=_COMPLETION_KIND.get(item.kind), detail=detail,
            sort_text=f"{n:06d}", text_edit=types.TextEdit(where, item.label)))
    return types.CompletionList(is_incomplete=bool(getattr(found, "is_incomplete", False)),
                                items=items)


def hover(ctx: Ctx, ana: Any, result: Any, path: Path, position: types.Position) -> types.Hover | None:
    text, at = _text(ctx, path), ctx.decoded(path, position)
    if text is None or at is None:
        return None
    program = getattr(result, "program", None)
    with _guard(ana):
        declared = _memo(ana, "hover_declarations", lambda: hovering.declarations(program)) \
            if program is not None else None
        found = hovering.hover(result, text, at[0], at[1], ctx.profile, ctx.help, declared=declared)
    if found is None:
        return None
    return types.Hover(
        contents=types.MarkupContent(types.MarkupKind.Markdown, found.markdown),
        range=ctx.columns(path, found.line, found.start_column, found.end_column))


def signature_help(ctx: Ctx, path: Path, position: types.Position) -> types.SignatureHelp | None:
    text, at = _text(ctx, path), ctx.decoded(path, position)
    if text is None or at is None:
        return None
    call = hovering.call_context(text, at[0], at[1])
    if call is None:
        return None
    info = hovering.signature_help(call.name, call.active, ctx.profile, ctx.help)
    if info is None:
        return None

    def offsets(span: tuple[int, int]) -> tuple[int, int]:      # label offsets are UTF-16 units
        return (positions.units(info.label[:span[0]], "utf-16"),
                positions.units(info.label[:span[1]], "utf-16"))

    parameters = [types.ParameterInformation(
        label=offsets(p.label_span),
        documentation=types.MarkupContent(types.MarkupKind.Markdown, p.documentation)
        if p.documentation else None) for p in info.parameters]
    signature = types.SignatureInformation(
        label=info.label,
        documentation=types.MarkupContent(types.MarkupKind.Markdown, info.documentation)
        if info.documentation else None,
        parameters=parameters, active_parameter=info.active_parameter)
    return types.SignatureHelp(signatures=[signature], active_signature=0,
                               active_parameter=info.active_parameter)


# ---- inlay hints (LSP-09c) ---------------------------------------------------------------------


def inlay_hints(ctx: Ctx, ana: Any, result: Any, path: Path, start_line: int,
                end_line: int) -> list[types.InlayHint]:
    """Parameter-name hints on lines `start_line..end_line` (0-based, inclusive). The whole file's
    hints are computed once per program and kept; a request only filters them."""
    if result is None or ctx.help is None:
        return []
    with _guard(ana):
        every = _memo(ana, f"inlay:{_key(path)}:{getattr(ctx.help, 'directory', '')}", lambda: inlay.parameter_hints(
            result, path, 0, 1 << 30, ctx.profile, ctx.help))
    return [types.InlayHint(position=ctx.position(path, h.line, h.column), label=h.label,
                            kind=types.InlayHintKind.Parameter, padding_right=h.padding_right)
            for h in every if start_line <= h.line <= end_line]


# ---- outline, folding, links -----------------------------------------------------------------


def _symbol(ctx: Ctx, path: Path, sym: editing.Symbol) -> types.DocumentSymbol:
    full, header = ctx.range(path, sym.range), ctx.range(path, sym.selection)
    inside = (full.start.line, full.start.character) <= (header.start.line, header.start.character) \
        and (header.end.line, header.end.character) <= (full.end.line, full.end.character)
    return types.DocumentSymbol(
        name=sym.name or sym.kind, kind=_SYMBOL_KIND.get(sym.kind, types.SymbolKind.Variable),
        range=full, selection_range=header if inside else full, detail=sym.detail or None,
        children=[_symbol(ctx, path, c) for c in sym.children] or None)


def document_symbols(ctx: Ctx, ana: Any, result: Any, path: Path) -> list[types.DocumentSymbol]:
    if result is None:
        return []
    with _guard(ana):
        found = editing.document_symbols(result, path, ctx.profile)
    return [_symbol(ctx, path, s) for s in found]


def folding_ranges(ctx: Ctx, path: Path) -> list[types.FoldingRange]:
    text = _text(ctx, path)
    if text is None:
        return []
    return [types.FoldingRange(start_line=f.start_line, end_line=f.end_line,
                               kind=_FOLD_KIND.get(f.kind)) for f in editing.folding_ranges(text, ctx.profile)]


def _same(a: Path, b: Path) -> bool:
    return _key(Path(a).absolute()) == _key(Path(b).absolute())


def document_links(ctx: Ctx, ana: Any, result: Any, path: Path) -> list[types.DocumentLink]:
    if result is None:
        return []
    with _guard(ana):
        links = _memo(ana, "include_links", lambda: editing.include_links(result))
    return [types.DocumentLink(range=ctx.range(path, link.range), target=Path(link.target).absolute().as_uri())
            for link in links if link.target is not None and _same(link.file, path)]


# ---- navigation -------------------------------------------------------------------------------


def _navigation(ana: Any, result: Any) -> Any:
    if result is None:
        return None
    with _guard(ana):
        return _memo(ana, "navigation", lambda: navigation.Navigation(result))


def _locations(ctx: Ctx, found: list[navigation.Location]) -> list[types.Location]:
    return [types.Location(ctx.uri(loc.path), ctx.columns(loc.path, loc.line, loc.start, loc.end))
            for loc in found if loc.path is not None]


def definition(ctx: Ctx, ana: Any, result: Any, path: Path,
               position: types.Position) -> list[types.Location]:
    nav, at = _navigation(ana, result), ctx.decoded(path, position)
    if nav is None or at is None:
        return []
    with _guard(ana):
        found = nav.definition(path, *at)
    return _locations(ctx, found)


def references(ctx: Ctx, ana: Any, result: Any, path: Path, position: types.Position,
               include_declaration: bool) -> list[types.Location]:
    nav, at = _navigation(ana, result), ctx.decoded(path, position)
    if nav is None or at is None:
        return []
    with _guard(ana):
        found = nav.references(path, at[0], at[1], include_declaration)
    return _locations(ctx, found)


def document_highlights(ctx: Ctx, ana: Any, result: Any, path: Path,
                        position: types.Position) -> list[types.DocumentHighlight]:
    nav, at = _navigation(ana, result), ctx.decoded(path, position)
    if nav is None or at is None:
        return []
    with _guard(ana):
        found = nav.document_highlights(path, *at)
    return [types.DocumentHighlight(ctx.columns(path, h.line, h.start, h.end),
                                    _HIGHLIGHT_KIND.get(h.kind)) for h in found]


def semantic_tokens(ctx: Ctx, ana: Any, result: Any, path: Path) -> types.SemanticTokens:
    nav, lines = _navigation(ana, result), ctx.lines(path)
    data: list[int] = []
    if nav is not None and lines:
        kinds = {name: i for i, name in enumerate(navigation.TOKEN_TYPES)}
        mods = {name: 1 << i for i, name in enumerate(navigation.TOKEN_MODIFIERS)}
        last_line = last_start = 0
        with _guard(ana):
            found = nav.semantic_tokens(path)
        for tok in found:
            if tok.line >= len(lines):
                continue                              # a stale program: the line is gone
            begin = ctx.position(path, tok.line, tok.start)
            end = ctx.position(path, tok.line, tok.start + tok.length)
            length = end.character - begin.character
            delta = begin.line - last_line
            if length <= 0 or delta < 0 or (not delta and begin.character < last_start):
                continue
            data += [delta, begin.character if delta else begin.character - last_start, length,
                     kinds[tok.type], sum(mods[m] for m in tok.modifiers)]
            last_line, last_start = begin.line, begin.character
    return types.SemanticTokens(data=data)


# ---- formatting (LSP-07-wire) -----------------------------------------------------------------


def _indent(options: types.FormattingOptions) -> str:
    width = max(1, min(int(options.tab_size or 1), 16))
    return " " * width if options.insert_spaces else "\t"


def formatting_edits(ctx: Ctx, path: Path, options: types.FormattingOptions,
                     where: types.Range | None = None) -> list[types.TextEdit]:
    """Indentation edits for the document, or for the lines `where` touches (a range that ends at
    character 0 of a later line does not include that line). Text-only: the refusal policy
    (`formatting.format_refusal`) is the caller's."""
    text = _text(ctx, path)
    if text is None:
        return []
    indent = _indent(options)
    if where is None:
        found = formatting.format_indentation(text, ctx.profile, indent)
    else:
        end = where.end
        if end.character == 0 and end.line > where.start.line:
            end = types.Position(end.line - 1, 0)
        first, last = ctx.decoded(path, where.start), ctx.decoded(path, end)
        if first is None or last is None:
            return []
        found = formatting.format_range(text, first[0], last[0], ctx.profile, indent)
    return [types.TextEdit(ctx.columns(path, e.line, e.start, e.end), e.new_text) for e in found]


# ---- rename (LSP-07-wire) ---------------------------------------------------------------------


def prepare_rename(ctx: Ctx, results: list, path: Path, position: types.Position,
                   source: Any = None
                   ) -> tuple[types.PrepareRenamePlaceholder | None, rename.Refusal | None, frozenset]:
    """(placeholder, refusal, files): `files` are the files of the declaration and its references
    (`PrepareResult.files`), empty on a refusal."""
    at = ctx.decoded(path, position)
    if at is None:
        return None, rename.Refusal("no_range", "the document cannot be read"), frozenset()
    found = rename.prepare_rename(results, path, *at, source=source)
    if not found.ok:
        return None, found.refusal, frozenset()
    assert found.path is not None           # an ok PrepareResult carries its range
    return types.PrepareRenamePlaceholder(
        range=ctx.columns(found.path, found.line, found.start, found.end),
        placeholder=found.placeholder), None, found.files


def rename_edit(ctx: Ctx, results: list, path: Path, position: types.Position, new_name: str,
                versions: Mapping[Path, int], source: Any = None
                ) -> tuple[types.WorkspaceEdit | None, rename.Refusal | None, dict]:
    """(edit, refusal, file hashes). The edit is `documentChanges` with the document version for
    every open document and `None` for a closed one. The hashes are `rename`'s content hashes.
    `source`: the `SourceProvider` `rename` checks freshness against (default: the analysed
    overlays on the disk)."""
    at = ctx.decoded(path, position)
    if at is None:
        return None, rename.Refusal("no_range", "the document cannot be read"), {}
    found = rename.rename(results, path, at[0], at[1], new_name, source=source)
    if not found.ok:
        return None, found.refusal, {}
    changes = []
    for target, edits in sorted(found.edits.items(), key=lambda kv: str(kv[0])):
        changes.append(types.TextDocumentEdit(
            types.OptionalVersionedTextDocumentIdentifier(ctx.uri(target), versions.get(target)),
            [types.TextEdit(ctx.columns(target, e.line, e.start, e.end), e.new_text)
             for e in edits]))
    return types.WorkspaceEdit(document_changes=changes), None, dict(found.versions)


# ---- quick fixes (LSP-07-wire) ----------------------------------------------------------------


def _first_line(diag: Any) -> int:
    if diag.span:
        return max(diag.span[0] - 1, 0)
    return max(diag.line - 1, 0) if diag.line > 0 else 0


def code_actions(ctx: Ctx, sources: list[tuple[str, Any]], path: Path,
                 diagnostics: list[types.Diagnostic], version: int | None,
                 display: Callable[[str], str]) -> list[types.CodeAction]:
    """Quick fixes for the diagnostics of the request that this server published. `sources`:
    (entry file name, `AnalysisResult`) of the entries that contributed diagnostics to `path`.
    A request diagnostic is matched to an analysis diagnostic by the displayed text (with the
    `[Entry] ` prefix the merge may add) and the first line; the same fix from several entries is
    offered once."""
    actions: list[types.CodeAction] = []
    seen: set = set()
    for lsp_diag in diagnostics:
        if lsp_diag.source != "opencrbasic":
            continue
        for name, result in sources:
            for diag in result.diagnostics:
                if diag.path is None or not _same(diag.path, path):
                    continue
                shown = display(diag.message)
                if _first_line(diag) != lsp_diag.range.start.line or not (
                        lsp_diag.message == shown or lsp_diag.message == f"[{name}] {shown}"):
                    continue
                for act in quickfix.code_actions(result, path, diag, ctx.profile):
                    if any(not _same(e.path, path) for e in act.edits):
                        continue
                    key = (lsp_diag.range.start.line, act.title, act.edits)
                    if key in seen:
                        continue
                    seen.add(key)
                    edits = [types.TextEdit(ctx.columns(path, e.line, e.start, e.end), e.new_text)
                             for e in act.edits]
                    actions.append(types.CodeAction(
                        title=act.title, kind=types.CodeActionKind.QuickFix,
                        diagnostics=[lsp_diag],
                        edit=types.WorkspaceEdit(document_changes=[types.TextDocumentEdit(
                            types.OptionalVersionedTextDocumentIdentifier(ctx.uri(path), version),
                            edits)])))
    return actions
