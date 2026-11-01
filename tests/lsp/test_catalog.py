import json
from pathlib import Path

import pytest

from opencrbasic import parser, profiles
from opencrbasic import syntax as S
from opencrbasic.catalog import EXTRA_KEYWORDS, KINDS, TABLE_KEYWORDS, CatalogEntry, InstructionCatalog

from .assertions import required

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "tests" / "corpus" / "firnmelt" / "Main.CR6").resolve()
LOGGERS = list(profiles.PROFILES)


def _rows(logger: str, table: str) -> list[dict]:
    prof = profiles.get(logger)
    return json.loads((prof.data_dir / f"{table}.json").read_text())


@pytest.mark.parametrize("logger", LOGGERS)
def test_counts_equal_profile_data(logger):
    cat = InstructionCatalog.for_profile(logger)
    for kind, table in (("instruction", "instructions"), ("function", "functions"), ("symbol", "symbols")):
        rows = _rows(logger, table)
        assert len({r["name"].upper() for r in rows}) == len(rows), "table names are unique"
        assert len(cat.names(kind)) == len(rows)
        assert sum(1 for e in cat if e.kind == kind) == len(rows)
        assert set(cat.names(kind)) == {r["name"] for r in rows}
    assert len(cat) == sum(1 for _ in cat)
    assert cat.profile == logger


def test_for_profile_is_cached_and_validates():
    assert InstructionCatalog.for_profile("cr6") is InstructionCatalog.for_profile("CR6")
    with pytest.raises(ValueError):
        InstructionCatalog.for_profile("cr9999")


def test_case_insensitive_lookup_keeps_profile_spelling():
    cat = InstructionCatalog.for_profile("cr6")
    assert cat is not None
    for text in ("beginprog", "BeginProg", "BEGINPROG"):
        entry = cat.get(text)
        assert entry is not None and entry.name == "BEGINPROG" and entry.kind == "instruction"
    assert required(cat.get("voltse")).name == "VoltSe"
    assert required(cat.get("voltse")).params == tuple(profiles.get("cr6").instructions["VOLTSE"]["params"])
    assert cat.get("nosuchname") is None
    assert "voltse" in cat and "nosuchname" not in cat and 5 not in cat


def test_dollar_suffix_names():
    cat = InstructionCatalog.for_profile("cr6")
    assert cat is not None
    entry = cat.get("chr$")
    assert entry is not None
    assert entry.name == "CHR$" and entry.kind == "function" and entry.code is not None
    assert required(cat.get("CHR")).name == "CHR"
    assert cat.get("CHR") is not cat.get("CHR$")


def test_entry_fields():
    cat = InstructionCatalog.for_profile("cr6")
    assert cat is not None
    assert (
        required(cat.get("U1")).kind == "symbol"
        and required(cat.get("U1")).value == profiles.get("cr6").symbols["U1"]["value"]
    )
    fn = cat.get("JSONVALUE")
    assert fn is not None
    row = profiles.get("cr6").functions["JSONVALUE"]
    assert (fn.code, fn.func_kind, fn.argspec, fn.nargs, fn.argtypes) == (
        row["code"],
        row["kind"],
        row["argspec"],
        row["nargs"],
        tuple(row["argtypes"]),
    )
    assert required(cat.get("NOT")).nargs is None
    with pytest.raises(Exception):
        setattr(fn, "name", "x")  # exercise the frozen field at runtime


def test_kinds_and_documented_overlaps():
    cat = InstructionCatalog.for_profile("cr6")
    assert cat is not None
    seen: dict[str, set[str]] = {}
    for e in cat:
        assert e.kind in KINDS and e.profile == "cr6"
        seen.setdefault(e.name.upper(), set()).add(e.kind)
    # every name appears at most once per kind
    assert len(list(cat)) == sum(len(k) for k in seen.values())
    # documented overlaps (docs/lsp-api.md "Instruction catalog"): a keyword entry always has a
    # table row, an extra keyword, or nothing else; the table kinds overlap only on these names.
    multi = {n: k - {"keyword"} for n, k in seen.items() if len(k - {"keyword"}) > 1}
    assert multi, "the CR6 tables do overlap (If, Select, Case, ...)"
    assert {"IF", "SELECT", "CASE", "SUB", "FUNCTION", "SCAN", "DO", "FOR"} <= set(multi)
    assert {frozenset(k) for k in multi.values()} <= {
        frozenset({"instruction", "symbol"}),
        frozenset({"function", "symbol"}),
        frozenset({"instruction", "function"}),
    }
    # get() priority: instruction, function, symbol, keyword
    assert required(cat.get("If")).kind == "instruction"
    assert required(cat.get("If", "symbol")).kind == "symbol"
    assert required(cat.get("If", "keyword")).source == "keyword:table"
    assert required(cat.get("To")).kind == "function"
    assert [e.kind for e in cat.entries("if")] == ["instruction", "symbol", "keyword"]


@pytest.mark.parametrize("logger", LOGGERS)
def test_keywords(logger):
    cat = InstructionCatalog.for_profile(logger)
    prof = profiles.get(logger)
    keywords = {e.name.upper(): e for e in cat if e.kind == "keyword"}
    for word in TABLE_KEYWORDS:
        if word.upper() in prof.instructions:
            e = keywords[word.upper()]
            assert e.name == prof.instructions[word.upper()]["name"] and e.source == "keyword:table"
        else:
            assert word.upper() not in keywords
    for word in EXTRA_KEYWORDS:
        assert word.upper() not in prof.instructions, "an extra keyword must be missing from the table"
        assert keywords[word.upper()].name == word and keywords[word.upper()].source == "keyword:parser"
    assert len(keywords) == len(cat.names("keyword"))


def test_extra_keywords_are_in_parser_source():
    text = (ROOT / "src" / "opencrbasic" / "parser.py").read_text()
    for word in EXTRA_KEYWORDS:
        assert f'is_ident("{word.upper()}")' in text, word


def _statements(entry: Path) -> list:
    program, _ = parser.parse_file(entry, profiles.get("cr6"))
    return program.statements


def test_main_cr6_instruction_keywords_are_catalog_instructions():
    cat = InstructionCatalog.for_profile("cr6")
    found = {s.keyword for s in _statements(MAIN) if isinstance(s, S.Instruction)}
    assert len(found) > 20
    missing = sorted(k for k in found if (cat.get(k, "instruction") is None))
    assert missing == []


def test_catalog_entry_is_dataclass_hashable():
    e = CatalogEntry("X", "symbol", "cr6", "symbols.json", value=1)
    assert hash(e) == hash(CatalogEntry("X", "symbol", "cr6", "symbols.json", value=1))
