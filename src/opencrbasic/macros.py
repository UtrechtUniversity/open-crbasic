"""`crbc --expand FILE`: opencrbasic's own macro step, not a reference-compiler feature.

The reference compilers reject `#Define` (`Unknown instruction`), so a program that uses macros is
expanded first into plain CRBasic, which any compiler (`crbc`, `CR6Comp.exe`) accepts and which is
the file to send to the logger. Macros expand inline, so they cost no call depth (the logger runs at
most four nested `Sub`/`Function` calls).

    #Define DEBUG 1                         object-like, one line
    #Define LOGD(msg) DebugMsg = msg        function-like, one line (an empty body is allowed)
    #Macro LOGV(name, value)                function-like, several lines
      DebugMsg = name & "=" & value
      CallTable Debug
    #EndMacro

- Names and parameters are case-insensitive, like CRBasic. A function-like name not followed by
  `(` is left alone; a macro is defined from its line on and cannot be redefined.
- Nothing inside a `"..."` string or a `'` comment is expanded. A one-line body loses its
  trailing comment.
- A macro body is expanded again after substitution, without expanding itself (no recursion).
- Every other line, `#If` and the other reference directives included, is kept as written apart
  from the expansion of macro names; the definition lines become comments so line numbers before
  the first multi-line expansion stay the same. The lines of a multi-line expansion take the
  indentation of the line that uses it.
- A macro call must close on its own line. Macros in `Include` files are not read or expanded.
- The language server expands macros the same way while analysing (`source.py`, the entry file
  only), so a program with macros gets the diagnostics of its expansion, each on the line of the
  macro use. `crbc` without `--expand` compiles like the reference and rejects `#Define`.
"""

import re
import textwrap

_DEFINE_RE = re.compile(r"^\s*#\s*(Define|Macro)\s+([A-Za-z_]\w*)(?:\(([^)]*)\))?(.*)$", re.IGNORECASE)
_END_RE = re.compile(r"^\s*#\s*EndMacro\b", re.IGNORECASE)
# strings and comments are copied whole; a number or `&H`/`&B` literal is never a name
_TOKEN_RE = re.compile(r'"[^"\n]*"?|\'[^\n]*|\d\w*|&[HhBb]\w*|[A-Za-z_]\w*|.', re.DOTALL)


class MacroError(Exception):
    def __init__(self, line: int, message: str):
        super().__init__(message)
        self.line = line


class _Macro:
    __slots__ = ("params", "body")

    def __init__(self, params: list[str] | None, body: str):
        self.params = params  # None: object-like
        self.body = body


def _strip_comment(text: str) -> str:
    return "".join(t for t in _TOKEN_RE.findall(text) if not t.startswith("'"))


def _call_args(text: str, i: int, line: int, name: str) -> tuple[list[str], int]:
    """The arguments of the call whose `(` is at `text[i]`, and the index after its `)`."""
    args, current, depth = [], [], 0
    for m in _TOKEN_RE.finditer(text, i + 1):
        tok = m.group()
        if tok == "\n":
            break
        if tok == ")" and depth == 0:
            args.append("".join(current).strip())
            return ([] if args == [""] else args), m.end()
        if tok == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue
        depth += {"(": 1, ")": -1}.get(tok, 0)
        current.append(tok)
    raise MacroError(line, f"Macro {name}: missing ).")


def _expand(text: str, macros: dict[str, _Macro], line: int, disabled: frozenset[str] = frozenset()) -> str:
    out: list[str] = []
    i = 0
    while m := _TOKEN_RE.match(text, i):
        tok, i = m.group(), m.end()
        key = tok.upper()
        macro = macros.get(key)
        if macro is None or key in disabled:
            out.append(tok)
            continue
        body = macro.body
        if macro.params is not None:
            j = i
            while j < len(text) and text[j] in " \t":
                j += 1
            if j >= len(text) or text[j] != "(":
                out.append(tok)
                continue
            args, i = _call_args(text, j, line, tok)
            if len(args) != len(macro.params):
                raise MacroError(line, f"Macro {tok} takes {len(macro.params)} argument(s), got {len(args)}.")
            values = dict(zip(macro.params, args, strict=True))
            body = "".join(values.get(t.upper(), t) for t in _TOKEN_RE.findall(body))
        body = _expand(body, macros, line, disabled | {key})
        head = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        indent = head[: len(head) - len(head.lstrip(" \t"))]
        out.append(body.replace("\n", "\n" + indent))
    return "".join(out)


class Macros:
    """The macros of one file, fed its lines in order (`expand` here, the loader for the editor)."""

    def __init__(self):
        self.table: dict[str, _Macro] = {}
        self._open: tuple[str, list[str] | None, int, list[str]] | None = None  # a #Macro being read

    def feed(self, text: str, line: int) -> str | None:
        """`text` with macros expanded, or None when it is part of a definition."""
        if self._open is not None:
            if _END_RE.match(text):
                name, params, start, body = self._open
                self._open = None
                self._define(name, params, textwrap.dedent("\n".join(body)).strip("\n"), start)
            else:
                self._open[3].append(text)
            return None
        d = _DEFINE_RE.match(text)
        if d is None:
            return _expand(text, self.table, line)
        kind, name, rest = d.group(1).upper(), d.group(2), d.group(4)
        params = None if d.group(3) is None else [p.strip().upper() for p in d.group(3).split(",")]
        if params == [""]:
            params = []
        if params is not None and (
            not all(re.fullmatch(r"[A-Za-z_]\w*", p) for p in params) or len(set(params)) < len(params)
        ):
            raise MacroError(line, f"Macro {name}: invalid parameter list.")
        if kind == "MACRO":
            if params is None or _strip_comment(rest).strip():
                raise MacroError(line, f"#Macro {name} needs a (parameter list) and its body on the following lines.")
            self._open = (name, params, line, [])
        else:
            self._define(name, params, _strip_comment(rest).strip(), line)
        return None

    def finish(self) -> None:
        if self._open is not None:
            raise MacroError(self._open[2], f"#Macro {self._open[0]} without #EndMacro.")

    def _define(self, name: str, params: list[str] | None, body: str, line: int) -> None:
        if name.upper() in self.table:
            raise MacroError(line, f"Macro {name} is already defined.")
        self.table[name.upper()] = _Macro(params, body)


def expand(text: str) -> str:
    """`text` with its `#Define`/`#Macro` definitions commented out and their uses expanded."""
    newline = "\r\n" if "\r\n" in text else "\n"
    macros = Macros()
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        expanded = macros.feed(raw, n)
        out.append("'" + raw if expanded is None else expanded.replace("\n", newline))
    macros.finish()
    return newline.join(out) + (newline if text.endswith(("\n", "\r")) else "")
