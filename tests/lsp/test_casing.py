"""On-type case correction rules (`casing`), without a server."""

from opencrbasic import casing


class _Help:
    def lookup(self, name):
        if name == "ABS":
            return type("H", (), {"syntax": "x = Abs( Source )"})()
        return None


def test_spelling_sources_in_order():
    assert casing.spelling("myvar", "cr6", ["MyVar"]) == "MyVar"  # a declaration wins
    assert casing.spelling("beginprog", "cr6") == "BeginProg"  # structure keyword
    assert casing.spelling("nextscan", "cr6") == "NextScan"
    assert casing.spelling("float", "cr6") == "Float"  # data type
    assert casing.spelling("true", "cr6") == "True"
    assert casing.spelling("and", "cr6") == "AND"  # operator word
    assert casing.spelling("voltse", "cr6") == "VoltSe"  # mixed-case table spelling
    assert casing.spelling("cs616", "cr6") == "CS616"  # an all-upper instruction name is kept
    assert casing.spelling("cr1000x", "cr6") == "CR1000X" and casing.spelling("table", "cr6") is None
    assert casing.spelling("abs", "cr6") is None  # functions: only with help
    assert casing.spelling("abs", "cr6", (), _Help()) == "Abs"
    assert casing.spelling("nosuchname", "cr6") is None


def test_word_before_skips_strings_comments_members_and_radix():
    lines = ['x = y + "abc" \' rem now', "  t.field = &hff ", "beginprog", "  "]
    assert casing.word_before(lines, 0, 2) == (0, 0, 1)
    assert casing.word_before(lines, 0, 13) is None  # just after a closing quote: no name
    assert casing.word_before(lines, 0, 12) is None  # inside the string
    assert casing.word_before(lines, 0, 23) is None  # inside the comment
    assert casing.word_before(lines, 1, 10) is None  # a member name after `.`
    assert casing.word_before(lines, 1, 17) is None  # radix literal `&hff`
    assert casing.word_before(lines, 3, 2, newline=True) == (2, 0, 9)
    assert casing.word_before(lines, 0, 0) is None


def test_correction_only_when_it_changes_something():
    lines = ["Public Counter As Long", "  counter = 1"]
    assert casing.correction(lines, 1, 10, "cr6", ["Counter"]) == casing.Correction(1, 2, 9, "Counter")
    assert casing.correction(lines, 0, 15, "cr6", ["Counter"]) is None  # already right
