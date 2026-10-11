"""Help provider tests: synthetic fixtures only; the optional corpus test asserts no help text."""

from __future__ import annotations

from pathlib import Path

import pytest

from opencrbasic import helpdocs, profiles
from opencrbasic.helpdocs import HelpProvider, coverage_report, pair_signature

FIX = Path(__file__).parent / "fixtures" / "help"
CORPUS = Path(__file__).resolve().parents[3] / "extracted_chm"


@pytest.fixture(scope="module")
def prov() -> HelpProvider:
    return HelpProvider(FIX)


def test_normal_page(prov):
    h = prov.lookup("foomeasure")
    assert h is not None and h.name == "FOOMEASURE" and h.names == ("FOOMEASURE",)
    assert h.title == "FooMeasure (Invented Measurement)"
    assert h.summary == "Synthetic summary line for FooMeasure & friends, written for tests."
    assert h.syntax == "FooMeasure ( Dest, Reps, fN1, Mult, Offset )"
    assert [p.name for p in h.params] == ["Dest", "Reps", "fN1", "Mult", "Offset"]
    assert h.params[0].link == "../parameters/fooDest.htm"
    assert h.params[0].description == "Invented description of the destination <variable>. Second sentence."
    assert h.params[1].description == "Invented repetition count."
    assert h.params[2].description == "Invented notch text."
    assert h.params[3].description == h.params[4].description == "Invented scaling text."
    assert h.params[3].link == h.params[4].link == "../parameters/foomult.htm"
    assert h.source_path == FIX / "Instructions" / "foomeasure.htm"
    assert not any(p.optional for p in h.params)


def test_unknown_and_non_instruction_pages(prov):
    assert prov.lookup("NoSuchThing") is None
    assert prov.lookup("Invented") is None
    assert "FOOSETTING" not in prov.names()


def test_multi_name_page(prov):
    a, b = prov.lookup("FooStart"), prov.lookup("FOOSTOP")
    assert a.names == b.names == ("FOOSTART", "FOOSTOP")
    assert [p.name for p in a.params] == ["Dest", "Reps"]
    assert b.params == () and b.syntax.startswith("FooStop")
    assert a.params[0].description == ""    # no Parameters section; linked page absent


def test_split_glued_and_unlinked_names(prov):
    h = prov.lookup("FooSplit")
    assert [p.name for p in h.params] == [
        "Dest", "Mult", "Offset", "Delay", "Count", "TotalCalls", "Module", '"quoted, text"', "Size"]
    links = {p.name: p.link for p in h.params}
    assert links["Mult"] == links["Offset"] == "../parameters/foomult.htm"   # one link, two names
    assert links["Delay"] == "../parameters/foodelay.htm"                   # word glued after </a>
    assert links["Count"] == "../parameters/foocount.htm"
    assert links["TotalCalls"] == "../parameters/foototal.htm"              # no comma between links
    assert links["Module"] is None and links['"quoted, text"'] is None     # unlinked; comma in quotes
    assert [p.optional for p in h.params] == [False] * 3 + [True] + [False] * 4 + [True]


def test_link_overlap_takes_name_text(prov):
    h = prov.lookup("FooSep")
    assert [(p.name, p.link, p.optional) for p in h.params] == [
        ("Gain", "../parameters/foomult.htm", False), ("Return", "../parameters/fooret.htm", True)]


def test_trailing_prose_after_form_is_ignored(prov):
    assert [p.name for p in prov.lookup("FooNote").params] == ["Dest"]


def test_bare_statement_notes_are_not_parameters(prov):
    for name in ("FooBare", "FooAlias"):
        h = prov.lookup(name)
        assert h is not None and h.params == ()
    assert prov.lookup("FooAlias").syntax == "FooAlias (or FooOther) list of constants"


def test_nested_markup_in_syntax(prov):
    h = prov.lookup("FooNested")
    assert [p.name for p in h.params] == ["Dest", "Count", "Linked"]
    assert [p.optional for p in h.params] == [True, True, True]
    assert h.params[2].description == "Invented linked-page description."
    assert prov.lookup("fooMeasure").params[2].name == "fN1"


def test_bare_brackets_mark_optional_and_others_stay_as_written(prov):
    h = prov.lookup("FooBracket")
    assert [(p.name, p.link, p.optional) for p in h.params] == [
        ("Dest", "../parameters/foodest.htm", False),
        ("Mode [A,B,C]", None, False),                    # choice list: one parameter, not optional
        ("FooInit", "../parameters/foolinked.htm", True),  # `[ X ]` around one name
        ("Last", None, True),                              # `Name [optional]`
    ]


def test_optional_marker_item_applies_to_preceding_parameter(prov):
    h = prov.lookup("FooOpt")
    assert [(p.name, p.link, p.optional) for p in h.params] == [
        ("Dest", "../parameters/foodest.htm", False),
        ("Mode", "../parameters/foomode.htm", False),
        ("FileOption", "../parameters/foofileopt.htm", True),   # `FileOption, [optional],`
        ("TimeOut", "../parameters/footimeout.htm", True),      # `TimeOut [optional]`
        ("Tail", "../parameters/footail.htm", True),            # `Tail, (optional)`
    ]
    sig = pair_signature("FooOpt", [1, 2, 3, 4, 5], h)
    assert sig.matched
    assert helpdocs.render_signature_label(sig) == (
        "FooOpt(Dest, Mode, FileOption (optional), TimeOut (optional), Tail (optional))")


def test_leading_optional_marker_creates_nothing(tmp_path):
    (tmp_path / "Instructions").mkdir()
    (tmp_path / "Instructions" / "x.htm").write_text(
        '<h1>FooLead</h1><h2>Syntax</h2><p>FooLead ( [optional], <a href="../parameters/foodest.htm">Dest</a> )</p>')
    h = HelpProvider(tmp_path).lookup("FooLead")
    assert [(p.name, p.optional) for p in h.params] == [("Dest", False)]


def test_statement_brackets_are_not_parameters(prov):
    h = prov.lookup("FooStatement")
    assert h is not None and h.params == () and h.syntax == ""


def test_trailing_ellipsis_is_one_repeatable_parameter(prov):
    h = prov.lookup("FooMenu")
    assert [(p.name, p.link, p.repeatable) for p in h.params] == [
        ("Dest", "../parameters/foodest.htm", False),
        ("Item", "../parameters/foomenuitem.htm", True),
    ]
    assert h.params[1].description == "Invented item text."
    assert not any(p.optional for p in h.params)


def test_ellipsis_collapses_only_its_numbered_run(prov):
    assert [(p.name, p.repeatable) for p in prov.lookup("FooMix").params] == [
        ("Item1", False), ("Mode", True)]
    assert [(p.name, p.repeatable) for p in prov.lookup("FooRest").params] == [
        ("Dest", False), ("Extra", True)]


def test_pairing_labels_repeatable_with_ellipsis(prov):
    h = prov.lookup("FooMenu")
    sig = pair_signature("FooMenu", [1, 2], h)
    assert sig.matched and [p.repeatable for p in sig.params] == [False, True]
    assert helpdocs.render_signature_label(sig) == "FooMenu(Dest, Item...)"
    sig = pair_signature("FooMenu", [1], h)     # counts disagree: no pairing, no guessing
    assert not sig.matched and not any(p.repeatable for p in sig.params)


def test_missing_parameters_section_uses_linked_page(prov):
    h = prov.lookup("FooNoParams")
    assert [p.name for p in h.params] == ["Linked", "Gone"]
    assert h.params[0].description == "Invented linked-page description."
    assert h.params[1].description == ""


def test_syntax_without_links(prov):
    h = prov.lookup("FooNoLinks")
    assert [(p.name, p.link, p.optional) for p in h.params] == [
        ("First", None, False), ("Second", None, True), ("Third", None, False)]


def test_entities_decoded(prov):
    h = prov.lookup("FooEntities")
    assert h.title == "FooEntities (A & B)"
    assert h.summary == "Compare 1 < 2 > 0 & © done.".replace(" ", " ")
    assert h.params[0].description == "x < y && y > z"


def test_malformed_pages_never_raise(prov):
    for name in ("FooBroken", "FooGarbage", "FooEmpty"):
        prov.lookup(name)    # None or a partial result, but no exception
    assert prov.lookup("FooEmpty") is None
    assert prov.lookup("FooGarbage") is None


def test_missing_directory():
    p = HelpProvider(FIX / "nonexistent")
    assert p.lookup("VoltSE") is None and p.names() == ()


def test_lazy_index():
    p = HelpProvider(FIX)
    assert p._pages is None
    p.lookup("x")
    assert p._pages is not None


def test_link_escape_is_ignored(tmp_path):
    (tmp_path / "Instructions").mkdir()
    (tmp_path / "secret.htm").write_text("<p>Dest</p><p>leaked</p>")
    (tmp_path / "Instructions" / "x.htm").write_text(
        '<h1>FooX</h1><h2>Syntax</h2><p>FooX(<a href="../parameters/../../secret.htm">Dest</a>)</p>')
    assert HelpProvider(tmp_path).lookup("FooX").params[0].description == ""


def test_pairing_matches_by_count(prov):
    h = prov.lookup("FooStart")
    sig = pair_signature("FooStart", [1, 2], h)
    assert sig.matched and sig.note == "" and [p.label for p in sig.params] == ["Dest", "Reps"]
    assert [p.type_code for p in sig.params] == [1, 2]
    assert helpdocs.render_signature_label(sig) == "FooStart(Dest, Reps)"


def test_pairing_reports_mismatch_without_guessing(prov):
    h = prov.lookup("FooMeasure")
    sig = pair_signature("FooMeasure", [1, 2, 3, 4, 5, 6], h)
    assert not sig.matched and (sig.help_count, sig.catalog_count) == (5, 6)
    assert "5" in sig.note and "6" in sig.note
    assert [p.label for p in sig.params] == ["arg1", "arg2", "arg3", "arg4", "arg5", "arg6"]
    assert not any(p.from_help for p in sig.params)


def test_pairing_without_help():
    sig = pair_signature("Bar", [7], None)
    assert not sig.matched and sig.help_count is None and sig.params[0].label == "arg1"


def test_optional_only_when_stated(prov):
    sig = pair_signature("FooNoLinks", [1, 1, 1], prov.lookup("FooNoLinks"))
    assert helpdocs.render_signature_label(sig) == "FooNoLinks(First, Second (optional), Third)"


def test_rendering(prov):
    h = prov.lookup("FooEntities")
    sig = pair_signature("FooEntities", [1], h)
    md = helpdocs.render_markdown(h, sig)
    assert "```crbasic\nFooEntities(Dest)\n```" in md
    assert "x \\< y" in md and "- `Dest`:" in md
    plain = helpdocs.render_plain(h, sig)
    assert plain.splitlines()[0] == "FooEntities (A & B)" and "Dest: x < y && y > z" in plain
    assert "FooEntities ( Dest )" in helpdocs.render_plain(h)    # unpaired: help syntax text


def test_coverage_report_on_fixtures(prov):
    class P:
        instructions = {
            "FOOSTART": {"params": [1, 2]},
            "FOOMEASURE": {"params": [1, 2, 3, 4, 5, 6]},
            "FOONOLINKS": {"params": [1, 1, 1]},
            "ABSENT": {"params": []},
        }
    r = coverage_report(P, prov)
    assert (r.total, r.with_help, r.matching) == (4, 3, 2)
    assert r.mismatches == (("FOOMEASURE", 5, 6),)
    assert r.without_help == ("ABSENT",)


@pytest.mark.skipif(not (CORPUS / "Instructions").is_dir(), reason="local help corpus not present")
def test_local_corpus_voltse_parameters():
    h = HelpProvider(CORPUS).lookup("VoltSE")
    assert [p.name for p in h.params] == [
        "Dest", "Reps", "Range", "SEChan", "MeasOff", "SettlingTime", "fN1", "Mult", "Offset"]


@pytest.mark.skipif(not (CORPUS / "Instructions").is_dir(), reason="local help corpus not present")
def test_local_corpus_documented_syntax_rules():
    p = HelpProvider(CORPUS)
    menu = p.lookup("MenuPick")
    assert [(x.name, x.repeatable) for x in menu.params] == [("Item", True)]
    assert pair_signature("MenuPick", [1], menu).matched
    for name, optional in (("ModbusSlave", "ModbusOption"), ("ModbusServer", "ModbusOption"),
                           ("FFTSpa", "FFTSpaInit"), ("SortSpa", "Dimension")):
        assert [x.name for x in p.lookup(name).params if x.optional] == [optional]
    display = p.lookup("DisplayMenu")
    assert [x.name for x in display.params] == ['"MenuName"', "AddToSystem", "Cursor"]    # quotes kept
    assert [x.name for x in display.params if x.optional] == ["Cursor"]


@pytest.mark.skipif(not (CORPUS / "Instructions").is_dir(), reason="local help corpus not present")
def test_local_corpus_optional_marker_item():
    p = HelpProvider(CORPUS)
    # `FileOption, [optional],` in the syntax line: the marker item is not a parameter.
    for name in ("FTPClient", "EMailSend", "HTTPPost"):
        h = p.lookup(name)
        assert h is not None and not any("optional" in x.name for x in h.params)
        flags = {x.name: x.optional for x in h.params}
        assert flags["FileOption"] is True and flags["TimeOut"] is True


@pytest.mark.skipif(not (CORPUS / "Instructions").is_dir(), reason="local help corpus not present")
def test_local_corpus_parses_structurally():
    p = HelpProvider(CORPUS)
    n_pages = 0
    for f in sorted((CORPUS / "Instructions").glob("*.htm")):
        page = helpdocs._parse_page(f)    # must not raise
        n_pages += page is not None
    assert n_pages > 100
    cat = profiles.get("cr6")
    r = coverage_report(cat, p)
    assert r.total == len(cat.instructions) and r.with_help > 100
    assert 0 <= r.matching <= r.with_help and len(r.mismatches) == r.with_help - r.matching
    for name in list(cat.instructions)[:400]:
        h = p.lookup(name)
        if h is not None:
            assert h.title and h.source_path.is_file() and h.name == name
            assert all(x.name for x in h.params)
