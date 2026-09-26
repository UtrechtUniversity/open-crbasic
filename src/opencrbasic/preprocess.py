"""`-p FILE` / `-i FILE`: CR6Comp's preprocessed-source output.

Independent of the compiler's own compile pipeline (`source.py`/`parser.py`), which always
comment-strips and fully expands `Include`/`IncludeSection` before symbol resolution: the exe's
`-p`/`-i` text dump is a much shallower transformation of the raw file. Oracle-verified
byte-for-byte on the repository's 16 programs and hand-written probes (`docs/cli.md`
"Preprocessed output (-p / -i)"; this replaces that section's earlier, unverified draft, which
wrongly assumed comments were stripped and includes expanded under `-p`):

- comments are kept verbatim (trailing `'...` and whole-line) -- NOT stripped.
- blank lines are kept.
- a line continuation (an unquoted, uncommented `_` whose only preceding characters on the line
  are whitespace) is resolved by deleting just the underscore and directly concatenating the
  following physical line's raw text: no separator is inserted and the continuing line's own
  leading whitespace is kept. A comment trailing the `_` on the same physical line is dropped
  along with the marker (oracle-confirmed: unlike an ordinary trailing comment, it does not
  survive into the output).
- `#If`/`#ElseIf`/`#Else`/`#EndIf`/`#If_no_remove`/`#UnDef` directive lines are removed; only the
  taken branch's own lines remain. Resolution uses the same Const environment as a real compile,
  so a Const defined via an `IncludeSection()` -- itself left unexpanded in `-p`'s own output --
  still resolves correctly (oracle-confirmed: `Main.CR6`'s `#If BatteryTempSensor > 0 Then` picks
  the `#Else` branch because `BatteryTempSensor` comes from `StationConfig.crb`).
- `-p`: a bare `Include(...)` or `IncludeSection(...)` statement is left as literal, unexpanded
  text.
- `-i`: both are expanded recursively, splicing in the target file's own preprocessed text in
  place (oracle-confirmed on both forms).
- output is CRLF throughout with one trailing CRLF, `latin-1`-decoded/encoded like `source.py`.

This reuses `parser._Parser`'s directive-handling and Const-folding (`_handle_directive`,
`_parse_const`, `_active`) so `#If`/Const resolution is the same oracle-verified engine the real
front end uses for compilation, not a second implementation of CRBasic's operator precedence
that could quietly drift from it. `parser.py` itself is not modified here, only called into.

Not yet verified against the oracle (documented gaps, `docs/cli.md`):
- a bare `Include(...)` nested inside an already-`-i`-expanded file (illegal for compilation,
  `Include file within an Include file is illegal.`) falls back to literal text rather than
  guessing at behaviour with no oracle sample.
- a dangling continuation marker on a file's last physical line (nothing follows to join).
"""

from __future__ import annotations

from pathlib import Path

from . import lexer, source
from .diagnostics import Diagnostics
from .parser import _DIRECTIVE_RE, _Parser, _TokStream, _split_text_segments
from .profiles import Profile


def _split_physical(raw: str) -> list[str]:
    """Physical lines, without the extra empty trailing element `str.split("\\n")` produces for
    input that (as is normal) ends with a line terminator -- unlike `source.py`'s own splitter,
    which can leave that artifact in because its caller always skips blank logical lines anyway.
    """
    parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def _is_continuation_raw(stripped: str) -> tuple[bool, str]:
    """Like `source._is_continuation`, but keeps the whitespace before the `_` -- oracle-
    confirmed the exe's own `-p`/`-i` join keeps it, where `source.py`'s own variant strips it
    further (harmless there: it only feeds the lexer, which doesn't see whitespace).
    """
    t = stripped.rstrip(" \t")
    if t.endswith("_") and (len(t) == 1 or t[-2] in " \t"):
        return True, t[:-1]
    return False, stripped


class _Group:
    """One continuation-joined logical line: `raw` is what `-p`/`-i` emits for it (verbatim
    except for the continuation markers themselves); `eval` is the comment-stripped, single-
    space-joined text used only to recognise directives/Const/Include, matching `source.py`'s own
    joining rule so `#If`/Const evaluation agrees with a real compile.
    """

    __slots__ = ("raw", "eval", "line")

    def __init__(self, raw: str, eval_text: str, line: int):
        self.raw = raw
        self.eval = eval_text
        self.line = line


def _read_groups(physical: list[str]) -> list[_Group]:
    groups: list[_Group] = []
    i = 0
    n = len(physical)
    while i < n:
        raw_parts: list[str] = []
        eval_text = source._strip_comment(physical[i])
        while True:
            cont, eval_prefix = source._is_continuation(eval_text)
            if not cont or i + 1 >= n:
                raw_parts.append(physical[i])
                if cont:
                    eval_text = eval_prefix
                break
            _, raw_prefix = _is_continuation_raw(source._strip_comment(physical[i]))
            raw_parts.append(raw_prefix)
            i += 1
            eval_text = eval_prefix + " " + source._strip_comment(physical[i]).lstrip(" \t")
        groups.append(_Group("".join(raw_parts), eval_text, i + 1))
        i += 1
    return groups


def _fold_consts_and_check_endprog(
    eval_text: str, line: int, filename: str, parser_instance: _Parser
) -> bool:
    """Folds any `Const` segment into `parser_instance.env` and returns True the moment an
    `EndProg` statement is seen. CR6Comp stops reading the file at the first top-level `EndProg`
    (`parser.py`'s own docstring, oracle-confirmed there); the same is true of `-p`/`-i`'s own
    text dump -- oracle-confirmed on the CHM corpus, several of whose examples concatenate a
    second, unrelated program (or bare prose) after a first program's `EndProg`, none of which
    appears in `-p`/`-i`'s output.
    """
    for seg in _split_text_segments(eval_text):
        if not seg.strip():
            continue
        try:
            tokens = lexer.tokenize(seg, line)
        except lexer.LexError:
            continue
        real = [t for t in tokens if t.kind != "EOF"]
        if not real or real[0].kind != "IDENT":
            continue
        if real[0].upper == "CONST":
            try:
                parser_instance._parse_const(_TokStream(tokens), line, filename)
            except Exception:
                pass
        elif real[0].upper == "ENDPROG":
            return True
    return False


def _walk_file(
    groups: list[_Group],
    filename: str,
    section: str | None,
    directory: Path | None,
    parser_instance: _Parser,
    expand_includes: bool,
    in_include: bool,
    seen: frozenset[str],
) -> list[str]:
    out: list[str] = []
    for g in groups:
        if parser_instance._done:
            break
        parser_instance.env.section = section
        m = _DIRECTIVE_RE.match(g.eval)
        if m:
            kind = m.group(1).upper()
            if kind == "IF_NO_REMOVE":
                kind = "IF"
            parser_instance._handle_directive(kind, m.group(2), g.line, filename)
            continue
        if not parser_instance._active():
            continue
        if _fold_consts_and_check_endprog(g.eval, g.line, filename, parser_instance):
            out.append(g.raw)
            parser_instance._done = True
            break
        m_sec = source._INCLUDE_SECTION_RE.match(g.eval)
        m_inc = None if m_sec else source._INCLUDE_RE.match(g.eval)
        if (m_sec or m_inc) and expand_includes:
            if m_sec:
                spec = m_sec.group(2)
                new_section, new_in_include = m_sec.group(1), in_include
            else:
                if in_include:
                    out.append(g.raw)
                    continue
                spec = m_inc.group(1)
                new_section, new_in_include = None, True
            target = source._resolve_include_path(spec, directory)
            if target is None:
                continue  # matches source.py: a missing include contributes nothing
            key = str(target.resolve())
            if key in seen:
                out.append(g.raw)
                continue
            try:
                data = target.read_bytes()
            except OSError:
                continue
            child_groups = _read_groups(_split_physical(data.decode("latin-1")))
            out.extend(_walk_file(
                child_groups, target.name, new_section, target.parent, parser_instance,
                expand_includes, new_in_include, seen | {key},
            ))
            if parser_instance._done:
                break
            continue
        out.append(g.raw)
    return out


def preprocess(path: Path, profile: Profile, *, expand_includes: bool) -> bytes:
    """The `-p` (`expand_includes=False`) or `-i` (`expand_includes=True`) output bytes for
    `path`: CRLF, one trailing CRLF, `latin-1`."""
    path = Path(path)
    data = path.read_bytes()
    groups = _read_groups(_split_physical(data.decode("latin-1")))
    parser_instance = _Parser(profile, Diagnostics())
    lines = _walk_file(
        groups, path.name, None, path.parent, parser_instance,
        expand_includes, False, frozenset({str(path.resolve())}),
    )
    return ("\r\n".join(lines) + "\r\n").encode("latin-1", "replace")
