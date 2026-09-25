"""Unit tests for `opencrbasic.lexer`."""

from __future__ import annotations

import pytest

from opencrbasic import lexer


def kinds(text: str) -> list[str]:
    return [t.kind for t in lexer.tokenize(text)][:-1]  # drop trailing EOF


def texts(text: str) -> list[str]:
    return [t.text for t in lexer.tokenize(text)][:-1]


def test_identifiers_leading_underscore_and_trailing_dollar():
    toks = lexer.tokenize("_60Hz CHR$ Foo_Bar1")
    assert [t.text for t in toks if t.kind == "IDENT"] == ["_60Hz", "CHR$", "Foo_Bar1"]
    assert [t.upper for t in toks if t.kind == "IDENT"] == ["_60HZ", "CHR$", "FOO_BAR1"]


def test_numbers_int_decimal_exponent():
    toks = lexer.tokenize("1 1.5 1.5E3 1.5e-3 5.")
    values = [t.value for t in toks if t.kind == "NUMBER"]
    assert values == [1, 1.5, 1500.0, 0.0015, 5.0]


def test_number_leading_dot():
    # Confirmed valid CRBasic (addprecise_1.CR6 in the CHM corpus): `.111111`. See docs/frontend.md.
    toks = lexer.tokenize(".111111")
    assert texts(".111111") == [".111111"]
    assert toks[0].value == pytest.approx(0.111111)


def test_number_hex_and_binary():
    toks = lexer.tokenize("&H1F &B1011")
    assert [t.value for t in toks if t.kind == "NUMBER"] == [0x1F, 0b1011]


def test_string_with_escaped_quote():
    toks = lexer.tokenize('"He said ""hi""" x')
    assert toks[0].kind == "STRING"
    assert toks[0].value == 'He said "hi"'


def test_string_unterminated_raises():
    with pytest.raises(lexer.LexError):
        lexer.tokenize('"unterminated')


def test_operators_and_punctuation():
    text = "+ - * / \\ ^ & = <> < > <= >= << >> ( ) , . : ; { } #"
    assert texts(text) == text.split()


def test_compound_assignments():
    assert texts("+= -= *= /= \\= ^= &=") == ["+=", "-=", "*=", "/=", "\\=", "^=", "&="]
    # Greedy: `+=` is one token, not `+` then `=`.
    toks = lexer.tokenize("X+=1")
    assert [t.text for t in toks if t.kind != "EOF"] == ["X", "+=", "1"]


def test_pointer_sigils():
    assert texts("! @") == ["!", "@"]


def test_columns_and_case_preserved():
    toks = lexer.tokenize("  Dim foo As Long")
    dim_tok = toks[0]
    assert dim_tok.text == "Dim"
    assert dim_tok.upper == "DIM"
    assert dim_tok.col == 2


def test_unrecognised_character_raises():
    with pytest.raises(lexer.LexError):
        lexer.tokenize("X = %")


def test_line_propagated_to_tokens():
    toks = lexer.tokenize("X = 1", line=42)
    assert all(t.line == 42 for t in toks)
