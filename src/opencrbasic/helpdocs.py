"""Instruction help read at run time from a user-configured local help directory.

The directory is a local copy of the logger vendor's HTML help (`Instructions/*.htm`,
`parameters/*.htm`). Nothing from it is stored in this package; the provider only parses pages
the user points it at (`docs/legal.md`, `docs/lsp-api.md` "Help provider"). Help parameter names
and prose are display information only: they never influence checker rules.
"""

import re
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "ParamHelp", "InstructionHelp", "HelpProvider", "PairedParam", "PairedSignature",
    "pair_signature", "render_markdown", "render_plain", "render_signature_label",
    "CoverageReport", "coverage_report", "FunctionCoverageReport", "function_coverage_report",
    "ArgCountRow", "function_arg_count_classes",
]


@dataclass(frozen=True)
class ParamHelp:
    name: str
    link: str | None            # target of the syntax link, as written (e.g. "../parameters/x.htm")
    description: str            # plain text; "" when the help has none
    optional: bool = False      # True only where the syntax line states it; False means "not stated"
    repeatable: bool = False    # `Item1, Item2, Item3...` collapsed to one `Item`; name is the stem


@dataclass(frozen=True)
class InstructionHelp:
    name: str                   # the looked-up upper-case name
    names: tuple[str, ...]      # every upper-case name the page documents
    title: str
    syntax: str                 # plain text of the syntax form for `name` ("" when the page has none)
    params: tuple[ParamHelp, ...]
    summary: str
    source_path: Path


# --- HTML reduction -------------------------------------------------------------------------------

_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li", "table", "pre"}
_HEADINGS = {"h1", "h2", "h3", "h4"}
_SKIP = {"script", "style"}


@dataclass
class _Anchor:
    href: str
    parts: list[str] = field(default_factory=list)


@dataclass
class _Block:
    tag: str
    text: str
    anchors: list[tuple[int, int, str, str]]    # (start, end, href, text) offsets into `text`


class _Reducer(HTMLParser):
    """Collects headings, paragraphs and table rows as plain text; anchors keep their offsets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self._tag: str | None = None
        self._buf: list[str] = []
        self._len = 0
        self._anchors: list[tuple[int, int, str, str]] = []
        self._open: list[tuple[int, str]] = []      # (start offset, href) of open anchors
        self._table = 0
        self._skip = 0

    def _emit(self, s: str) -> None:
        self._buf.append(s)
        self._len += len(s)

    def _close(self) -> None:
        if self._tag is not None:
            text = "".join(self._buf)
            self.blocks.append(_Block(self._tag, text, self._anchors))
        self._tag, self._buf, self._len, self._anchors, self._open = None, [], 0, [], []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        elif tag == "table":
            self._close()
            self._table += 1
        elif self._table and tag == "tr":
            self._close()
            self._tag = "tr"
        elif self._table and tag in ("td", "th"):
            if _flat("".join(self._buf)):
                self._emit(" | ")
        elif tag in ("p", "h1", "h2", "h3", "h4"):
            if not self._table:
                self._close()
                self._tag = tag
        elif tag == "br":
            if self._tag is not None:
                self._emit("\n")
        elif tag == "a" and self._tag is not None:
            self._open.append((self._len, dict(attrs).get("href") or ""))

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "table":
            self._close()
            self._table = max(0, self._table - 1)
        elif self._table and tag == "tr":
            self._close()
        elif tag in ("p", "h1", "h2", "h3", "h4"):
            if not self._table:
                self._close()
        elif tag == "a" and self._open:
            start, href = self._open.pop()
            self._anchors.append((start, self._len, href, "".join(self._buf)[start:self._len]))

    def handle_data(self, data):
        if self._tag is not None and not self._skip:
            self._emit(data)

    def close(self):
        super().close()
        self._close()


def _collapse(text: str) -> str:
    return re.sub(r"[ \t\r\f\v ]+", " ", re.sub(r"\s*\n\s*", "\n", text.replace(" ", " "))).strip()


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip()


def _blocks(html: str) -> list[_Block]:
    r = _Reducer()
    r.feed(html)
    r.close()
    return r.blocks


# --- page parsing ----------------------------------------------------------------------------------

_FORM = re.compile(r"^\s*(?:[A-Za-z_][\w.]*\s*=\s*)?([A-Za-z_]\w*)\s*\(")
_OPTIONAL = re.compile(r"[\[(]\s*optional\s*[\])]", re.I)
_NAME = re.compile(r"^[A-Za-z_]\w*$")
# Brackets around exactly one name: `[ModbusOption]`, `[ Dimension ]`; `/` joins alternative names.
_BARE = re.compile(r"\[\s*([A-Za-z_][\w/]*)\s*\]")
# A parenthesised note after a bare statement name: `EndSequence (optional)`, `Data (or DataLong)`.
_NOTE = re.compile(r"^\s*(?:optional|or\s+[A-Za-z_]\w*)\s*$", re.I)


def _close(text: str, start: int) -> int:
    """Index of the ')' closing a '(' that precedes `start`; len(text) when unclosed."""
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _spans(text: str, start: int, end: int, link_starts: set[int]) -> list[tuple[int, int]]:
    """Parameter spans of text[start:end].

    A span ends at a top-level ',' or ';', or where a parameter link starts after other name text
    (a separator the help omitted, e.g. `Count </a>TotalCalls`). Separators inside (), [] or double
    quotes do not split.
    """
    out: list[tuple[int, int]] = []
    depth = 0
    quoted = False
    piece = start
    for i in range(start, end):
        if i in link_starts and depth == 0 and not quoted and _bare(text[piece:i]):
            out.append((piece, i))
            piece = i
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            elif ch in ",;" and depth == 0:
                out.append((piece, i))
                piece = i + 1
    out.append((piece, end))
    return out


def _bare(raw: str) -> str:
    """Name text of a span with optional markers and bracket notation removed ('' if none)."""
    return re.sub(r"[\s\[\]]", "", _OPTIONAL.sub("", raw))


@dataclass
class _Form:
    name: str
    text: str
    params: list[ParamHelp]


def _fold_repeat(params: list[ParamHelp]) -> list[ParamHelp]:
    """A trailing `...` on the last name makes one repeatable parameter. A numbered run ending in
    that name (`Item1, Item2, Item3...`) collapses into one parameter named after the stem (`Item`).
    """
    if not params or not params[-1].name.endswith("..."):
        return params
    last = params[-1]
    stem = last.name[:-3].rstrip()
    m = re.fullmatch(r"(.*?)(\d+)", stem)
    base = m.group(1) if m else stem
    if not _NAME.match(base):
        return params
    run = [last]
    rest = list(params[:-1])
    if m:
        numbered = re.compile(re.escape(base) + r"\d+")
        while rest and numbered.fullmatch(rest[-1].name):
            run.insert(0, rest.pop())
    return rest + [ParamHelp(base, run[0].link, "", any(p.optional for p in run), True)]


def _parse_form(block: _Block) -> _Form | None:
    text = block.text
    m = _FORM.match(text)
    if not m:
        return None
    flat = _flat(text)
    start = m.end()
    end = _close(text, start)
    params: list[ParamHelp] = []
    if not _NOTE.match(text[start:end]):
        links = [a for a in sorted(block.anchors) if "parameters/" in a[2].lower().replace("\\", "/")]
        links = [a for a in links if not any(o is not a and a[0] <= o[0] and o[1] <= a[1] for o in links)]
        # Every span of the syntax line is a parameter, linked or not. A span takes the parameter
        # link that overlaps it: `Mult, Offse</a>t` is one link covering two names, and `Mult, Offset`
        # with a link per name is two spans over two links.
        previous_named = False
        for a, b in _spans(text, start, end, {s for s, _, _, _ in links}):
            raw = text[a:b]
            body = _flat(_OPTIONAL.sub("", raw)).strip(" ,;")
            bare = _BARE.fullmatch(body)
            if bare:
                # `[X]` around exactly one name: optional, brackets dropped.
                name, optional = bare.group(1), True
            else:
                # Other brackets stay as written (`Mode [A,B,C]`) and never set optional.
                name, optional = body, bool(_OPTIONAL.search(raw))
            if name:
                # The link whose overlap with this span holds name text (not just a separator).
                href = next((h for s, e, h, _ in links
                             if s < b and a < e and re.search(r"\w", text[max(s, a):min(e, b)])), None)
                params.append(ParamHelp(name, href, "", optional))
            elif previous_named and _OPTIONAL.search(raw):
                # `FileOption, [optional],`: a marker that is a list item of its own applies to the
                # parameter before it and creates no parameter.
                params[-1] = replace(params[-1], optional=True)
            previous_named = bool(name)
    return _Form(m.group(1).upper(), flat, _fold_repeat(params))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _heading_key(text: str) -> tuple[str, ...]:
    """Normalised candidates a parameter heading answers to: the text before ` (`, and each
    comma-separated piece of it."""
    head = _flat(text).split("(")[0]
    keys = [_norm(head)] + [_norm(p) for p in head.split(",")]
    return tuple(k for k in keys if k)


def _description_with_rows(blocks: Sequence[_Block], index: int) -> str:
    parts = [_collapse(blocks[index].text)]
    for block in blocks[index + 1:]:
        if block.tag != "tr":
            break
        if text := _flat(block.text):
            parts.append(text)
    return "\n".join(parts)


def _describe(blocks: Sequence[_Block], name: str) -> str:
    """Description paragraph under the heading paragraph answering to `name`, or ''."""
    want = _norm(name)
    if not want:
        return ""
    for i, b in enumerate(blocks):
        if b.tag != "p" or "\n" in b.text.strip() or len(b.text) > 120:
            continue
        if want in _heading_key(b.text) and i + 1 < len(blocks):
            for j in range(i + 1, min(i + 3, len(blocks))):
                nxt = blocks[j]
                t = _collapse(nxt.text)
                if nxt.tag == "p" and t and not t.lower().startswith("type:"):
                    return _description_with_rows(blocks, j)
            return ""
    return ""


@dataclass
class _Page:
    path: Path
    title: str
    summary: str
    names: tuple[str, ...]
    forms: dict[str, _Form]
    param_blocks: list[_Block]
    html_ok: bool = True


def _parse_page(path: Path) -> _Page | None:
    try:
        html = path.read_text(encoding="utf-8", errors="replace")
        blocks = _blocks(html)
        h1 = next((b for b in blocks if b.tag == "h1"), None)
        syn = next((i for i, b in enumerate(blocks) if b.tag == "h2" and _flat(b.text).lower() == "syntax"), None)
        if h1 is None or syn is None:
            return None
        title = _flat(h1.text)
        names: list[str] = []
        for part in title.split("(")[0].split("/"):
            part = part.strip()
            if _NAME.match(part):
                names.append(part.upper())
        forms: dict[str, _Form] = {}
        for b in blocks[syn + 1:]:
            if b.tag in ("h2", "h3"):
                break
            if b.tag != "p":
                continue
            if _flat(b.text).lower().startswith("example"):
                break
            f = _parse_form(b)
            if f is not None:
                forms.setdefault(f.name, f)
                if f.name not in names:
                    names.append(f.name)
        if not names:
            return None
        h1i = blocks.index(h1)
        summary = ""
        for b in blocks[h1i + 1:syn]:
            t = _collapse(b.text)
            if b.tag == "p" and t:
                summary = t
                break
        pi = next((i for i, b in enumerate(blocks) if b.tag == "h2" and _flat(b.text).lower() == "parameters"), None)
        pblocks: list[_Block] = []
        if pi is not None:
            for b in blocks[pi + 1:]:
                if b.tag == "h2":
                    break
                pblocks.append(b)
        return _Page(path, title, summary, tuple(names), forms, pblocks)
    except Exception:
        return None


# --- provider --------------------------------------------------------------------------------------

class HelpProvider:
    """Lazy index over `<directory>/Instructions/*.htm`; lookups never raise."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self._pages: dict[str, _Page] | None = None
        self._cache: dict[str, InstructionHelp | None] = {}

    def _index(self) -> dict[str, _Page]:
        if self._pages is None:
            pages: dict[str, _Page] = {}
            try:
                files = sorted((self.directory / "Instructions").glob("*.htm"))
            except OSError:
                files = []
            for f in files:
                page = _parse_page(f)
                if page is not None:
                    for n in page.names:
                        pages.setdefault(n, page)
            self._pages = pages
        return self._pages

    def names(self) -> tuple[str, ...]:
        """Upper-case names that have a parsable help page."""
        return tuple(sorted(self._index()))

    def lookup(self, name: str) -> InstructionHelp | None:
        key = name.upper()
        if key not in self._cache:
            try:
                self._cache[key] = self._build(key)
            except Exception:
                self._cache[key] = None
        return self._cache[key]

    def _build(self, key: str) -> InstructionHelp | None:
        page = self._index().get(key)
        if page is None:
            return None
        form = page.forms.get(key)
        params: list[ParamHelp] = []
        for p in (form.params if form else []):
            desc = _describe(page.param_blocks, p.name)
            if not desc and p.link:
                desc = self._linked_description(p.link, p.name)
            params.append(ParamHelp(p.name, p.link, desc, p.optional, p.repeatable))
        return InstructionHelp(key, page.names, page.title, form.text if form else "",
                               tuple(params), page.summary, page.path)

    def _linked_description(self, link: str, name: str) -> str:
        target = (self.directory / "Instructions" / link.replace("\\", "/")).resolve()
        try:
            if not target.is_file() or self.directory.resolve() not in target.parents:
                return ""
            blocks = _blocks(target.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return ""
        for i, nxt in enumerate(blocks[1:3], start=1):
            t = _collapse(nxt.text)
            if nxt.tag == "p" and t and not t.lower().startswith("type:"):
                return _description_with_rows(blocks, i)
        return ""


# --- pairing with catalog parameter lists ----------------------------------------------------------

@dataclass(frozen=True)
class PairedParam:
    label: str                          # help name, with "..." when repeatable; `argN` when unpaired
    type_code: int
    description: str
    optional: bool
    from_help: bool
    repeatable: bool = False


@dataclass(frozen=True)
class PairedSignature:
    name: str
    matched: bool                       # help parameter count equals the catalog count
    help_count: int | None              # None: no help
    catalog_count: int
    params: tuple[PairedParam, ...]     # friendly when matched; otherwise catalog positions only
    note: str                           # "" when matched; the mismatch reason otherwise


def pair_signature(name: str, type_codes: Sequence[int], help: InstructionHelp | None) -> PairedSignature:
    """Pair help names with catalog positions only when the counts agree; never guess otherwise."""
    n = len(type_codes)
    if help is None:
        return PairedSignature(name, False, None, n,
                               tuple(PairedParam(f"arg{i + 1}", t, "", False, False) for i, t in enumerate(type_codes)),
                               "no help")
    h = len(help.params)
    if h == n:
        return PairedSignature(name, True, h, n,
                               tuple(PairedParam(p.name + "..." if p.repeatable else p.name, t,
                                                 p.description, p.optional, True, p.repeatable)
                                     for p, t in zip(help.params, type_codes)), "")
    return PairedSignature(name, False, h, n,
                           tuple(PairedParam(f"arg{i + 1}", t, "", False, False) for i, t in enumerate(type_codes)),
                           f"help lists {h} parameters, catalog has {n}")


# --- rendering (separate from parsing) -------------------------------------------------------------

def render_signature_label(sig: PairedSignature) -> str:
    inner = ", ".join(p.label + (" (optional)" if p.optional else "") for p in sig.params)
    return f"{sig.name}({inner})"


def render_plain(help: InstructionHelp, sig: PairedSignature | None = None) -> str:
    lines = [help.title, ""]
    lines.append(render_signature_label(sig) if sig is not None and sig.matched else help.syntax)
    if help.summary:
        lines += ["", help.summary]
    if sig is not None and sig.matched:
        for p in sig.params:
            if p.description:
                lines.append(f"{p.label}: {p.description}")
    return "\n".join(lines).strip()


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|])")


def _md(text: str) -> str:
    return _MD_SPECIAL.sub(r"\\\1", text)


def render_markdown(help: InstructionHelp, sig: PairedSignature | None = None) -> str:
    syntax = render_signature_label(sig) if sig is not None and sig.matched else help.syntax
    out = [f"**{_md(help.title)}**", "", "```crbasic", syntax.replace("```", "'''"), "```"]
    if help.summary:
        out += ["", _md(help.summary)]
    if sig is not None and sig.matched:
        rows = [f"- `{p.label}`: {_md(p.description)}" for p in sig.params if p.description]
        if rows:
            out += [""] + rows
    return "\n".join(out)


# --- coverage --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageReport:
    total: int
    with_help: int
    matching: int
    mismatches: tuple[tuple[str, int, int], ...]    # (name, help parameter count, catalog count)
    without_help: tuple[str, ...]


def coverage_report(profile, provider: HelpProvider, names: Iterable[str] | None = None) -> CoverageReport:
    """Documentation coverage for `profile.instructions` (separate from compiler-rule coverage)."""
    table: Mapping[str, Mapping] = profile.instructions
    wanted = sorted(table) if names is None else sorted(n.upper() for n in names if n.upper() in table)
    with_help = 0
    mism: list[tuple[str, int, int]] = []
    missing: list[str] = []
    for n in wanted:
        h = provider.lookup(n)
        if h is None:
            missing.append(n)
            continue
        with_help += 1
        sig = pair_signature(n, table[n]["params"], h)
        if not sig.matched:
            mism.append((n, len(h.params), len(table[n]["params"])))
    return CoverageReport(len(wanted), with_help, with_help - len(mism), tuple(mism), tuple(missing))


@dataclass(frozen=True)
class FunctionCoverageReport:
    total: int                                      # rows of `profile.functions` considered
    with_nargs: int                                 # rows whose function table has `nargs`
    with_help: int                                  # rows with a parsable help page
    checked: int                                    # rows with help and `nargs`: count compared
    matching: int                                   # checked rows whose help count equals nargs
    mismatches: tuple[tuple[str, int, int], ...]    # (name, help parameter count, nargs)
    without_help: tuple[str, ...]
    unchecked: tuple[str, ...]                      # help but no `nargs`: nothing to compare with


def function_coverage_report(profile, provider: HelpProvider, names: Iterable[str] | None = None
                             ) -> FunctionCoverageReport:
    """Documentation coverage for `profile.functions`, separate from `coverage_report` (instructions)
    and from compiler-rule coverage. Only rows with `nargs` are compared, by parameter count."""
    table: Mapping[str, Mapping] = profile.functions
    wanted = sorted(table) if names is None else sorted(n.upper() for n in names if n.upper() in table)
    with_nargs = with_help = 0
    mism: list[tuple[str, int, int]] = []
    missing: list[str] = []
    unchecked: list[str] = []
    for n in wanted:
        nargs = table[n].get("nargs")
        if nargs is not None:
            with_nargs += 1
        h = provider.lookup(n)
        if h is None:
            missing.append(n)
            continue
        with_help += 1
        if nargs is None:
            unchecked.append(n)
        elif len(h.params) != nargs:
            mism.append((n, len(h.params), nargs))
    checked = with_help - len(unchecked)
    return FunctionCoverageReport(len(wanted), with_nargs, with_help, checked, checked - len(mism),
                                  tuple(mism), tuple(missing), tuple(unchecked))


# `nargs` 6 is the 16-bit `argspec` maximum (low nibble 6, 2 bits per argument): docs/binary.md.
_PACKING_CAP = 6


@dataclass(frozen=True)
class ArgCountRow:
    name: str
    help_count: int
    help_required: int      # help parameters not marked optional
    nargs: int
    category: str           # "packing_cap" | "optional_tail" | "other"


def function_arg_count_classes(profile, provider: HelpProvider, names: Iterable[str] | None = None
                               ) -> tuple[ArgCountRow, ...]:
    """Classify `profile.functions` whose help parameter count differs from `nargs`.

    `packing_cap`: `nargs` is 6, the packed `argspec` maximum, so the table cannot list more.
    `optional_tail`: not capped, and the help's required count equals `nargs` (the rest is optional).
    `other`: neither. A classification only; it says nothing about which counts the compiler accepts.
    """
    table: Mapping[str, Mapping] = profile.functions
    wanted = sorted(table) if names is None else sorted(n.upper() for n in names if n.upper() in table)
    rows: list[ArgCountRow] = []
    for n in wanted:
        nargs = table[n].get("nargs")
        h = provider.lookup(n)
        if nargs is None or h is None or len(h.params) == nargs:
            continue
        required = sum(1 for p in h.params if not p.optional)
        cat = ("packing_cap" if nargs == _PACKING_CAP
               else "optional_tail" if required == nargs and len(h.params) > nargs else "other")
        rows.append(ArgCountRow(n, len(h.params), required, nargs, cat))
    return tuple(rows)
