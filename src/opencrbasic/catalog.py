"""Instruction catalog: the names a CRBasic program can use, per logger profile.

Built only from `profiles.get(name)` (`instructions`, `functions`, `symbols`, the tables extracted
from each logger's compiler by `tools/extract_tables.py`). There is no second instruction list.
`docs/lsp-api.md` ("Instruction catalog") describes the kinds, the overlaps and the keyword rule.
"""

import functools
from dataclasses import dataclass
from collections.abc import Iterator

from . import profiles

KINDS = ("instruction", "function", "symbol", "keyword")

# Words that structure a program or a declaration. Each is also a row of the instruction table of
# the profiles that have it (the parser dispatches on it in `Parser._parse_statement`, or reads it
# as an ordinary instruction row such as `NextScan`, `EndTable`). A profile gets the entry only
# when its own instruction table has the word, with the table's spelling.
TABLE_KEYWORDS = (
    "If", "ElseIf", "Else", "EndIf", "Select", "Case", "EndSelect",
    "For", "Next", "Do", "Loop", "While", "WEnd",
    "Exit", "ExitFor", "ExitDo", "ExitSub", "ExitFunction", "ExitScan", "ContinueScan", "Return",
    "Sub", "EndSub", "Function", "EndFunction", "Call", "End",
    "Const", "Public", "Dim", "Alias", "Units",
    "Include", "IncludeSection",
    "BeginProg", "EndProg", "Scan", "SubScan", "NextScan", "NextSubScan",
    "DataTable", "EndTable", "ConstTable", "EndConstTable",
)

# Language words the instruction table lacks. Each is matched by name in the parser
# (`parser.py`): `Then` (`_parse_if`, `_parse_elseif`), `Step` (`_parse_for`), `As` (declarations,
# `_parse_subdef`, `_parse_param`), `Optional` (`_parse_param`). They are the same on every profile.
EXTRA_KEYWORDS = ("Then", "Step", "As", "Optional")


@dataclass(frozen=True)
class CatalogEntry:
    name: str                       # the profile's spelling
    kind: str                       # one of KINDS
    profile: str                    # profile name ("cr6", ...)
    source: str                     # "instructions.json" | "functions.json" | "symbols.json" |
                                    # "keyword:table" | "keyword:parser"
    params: tuple[int, ...] = ()    # instruction: type codes (docs/type-codes.md)
    value: int | None = None        # symbol: the integer value
    code: int | None = None         # function: table `code`
    func_kind: int | None = None    # function: table `kind`
    argspec: int | None = None      # function: table `argspec`
    nargs: int | None = None        # function: present only for some rows
    argtypes: tuple[int, ...] | None = None


def _build(name: str) -> tuple[CatalogEntry, ...]:
    prof = profiles.get(name)
    out: list[CatalogEntry] = []
    for row in prof.instructions.values():
        out.append(CatalogEntry(row["name"], "instruction", prof.name, "instructions.json",
                                params=tuple(row.get("params", ()))))
    for row in prof.functions.values():
        argtypes = row.get("argtypes")
        out.append(CatalogEntry(row["name"], "function", prof.name, "functions.json",
                                code=row.get("code"), func_kind=row.get("kind"),
                                argspec=row.get("argspec"), nargs=row.get("nargs"),
                                argtypes=None if argtypes is None else tuple(argtypes)))
    for row in prof.symbols.values():
        out.append(CatalogEntry(row["name"], "symbol", prof.name, "symbols.json",
                                value=row.get("value")))
    for word in TABLE_KEYWORDS:
        row = prof.instructions.get(word.upper())
        if row is not None:
            out.append(CatalogEntry(row["name"], "keyword", prof.name, "keyword:table"))
    for word in EXTRA_KEYWORDS:
        out.append(CatalogEntry(word, "keyword", prof.name, "keyword:parser"))
    return tuple(out)


class InstructionCatalog:
    """Every name of one profile, by kind. Lookup is case-insensitive and keeps the profile's
    spelling. A name may exist under several kinds (`If` is an instruction row, a symbol and a
    keyword); `get` returns one entry by the priority instruction, function, symbol, keyword and
    `entries` returns all of them."""

    def __init__(self, profile: str):
        self.profile = profiles.get(profile).name
        self._entries = _build(self.profile)
        self._by_name: dict[str, list[CatalogEntry]] = {}
        for entry in self._entries:
            self._by_name.setdefault(entry.name.upper(), []).append(entry)

    @classmethod
    def for_profile(cls, name: str) -> InstructionCatalog:
        """The cached catalog of a profile name (`ValueError` for an unknown one)."""
        return _cached(profiles.get(name).name)

    def entries(self, name: str) -> tuple[CatalogEntry, ...]:
        return tuple(self._by_name.get(name.upper(), ()))

    def get(self, name: str, kind: str | None = None) -> CatalogEntry | None:
        found = self._by_name.get(name.upper(), ())
        if kind is not None:
            return next((e for e in found if e.kind == kind), None)
        for k in KINDS:
            for e in found:
                if e.kind == k:
                    return e
        return None

    def names(self, kind: str | None = None) -> tuple[str, ...]:
        """Profile spellings of one kind (all kinds when None), sorted case-insensitively and
        without repeats."""
        seen = {e.name for e in self._entries if kind is None or e.kind == kind}
        return tuple(sorted(seen, key=lambda n: (n.upper(), n)))

    def __iter__(self) -> Iterator[CatalogEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.upper() in self._by_name


@functools.cache
def _cached(name: str) -> InstructionCatalog:
    return InstructionCatalog(name)
