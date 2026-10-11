"""Language-feature answers as LSP types. A protocol adapter over the core modules.

Each function is synchronous and meant for a worker thread (`asyncio.to_thread`): it calls
`editing`, `hover` or `navigation` and converts what they return. The core modules speak physical
0-based lines and decoded (Latin-1) columns; this module converts both ways with
`positions.lsp_position` / `positions.decoded_position` using the negotiated position encoding and
the configured file encoding, and nothing else. There are no CRBasic rules here.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Callable, Mapping

from lsprotocol import types

from .. import editing, hover as hovering, navigation, positions

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


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class Ctx:
    """What one request needs to convert positions: the encodings, the open documents' bytes
    (a snapshot taken on the event loop), their URIs, the profile and the help provider."""

    def __init__(self, profile: str, position_encoding: str, file_encoding: str,
                 blobs: Mapping[Path, bytes], uris: Mapping[Path, str], help_provider: Any = None):
        self.profile = profile
        self.encoding = position_encoding
        self.file_encoding = file_encoding
        self.help = help_provider
        self._blobs = {_key(p): d for p, d in blobs.items()}
        self._uris = {_key(p): u for p, u in uris.items()}
        self._lines: dict[str, list[bytes] | None] = {}

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

    def uri(self, path: Path) -> str:
        return self._uris.get(_key(path)) or Path(path).absolute().as_uri()

    def decoded(self, path: Path, position: types.Position) -> tuple[int, int] | None:
        """LSP position -> (0-based line, decoded column); a line past the end clamps to the last."""
        lines = self.lines(path)
        if not lines:
            return None
        line = min(max(position.line, 0), len(lines) - 1)
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
        return _utf16(info.label[:span[0]]), _utf16(info.label[:span[1]])

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
