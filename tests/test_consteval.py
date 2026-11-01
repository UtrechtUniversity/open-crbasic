"""Unit tests for `opencrbasic.consteval`, driven through `parser.parse_text` so expressions are
built with the parser's real precedence rather than hand-assembled trees."""

import pytest

from opencrbasic import consteval, parser, profiles
from opencrbasic import syntax as S


def const_value(expr_text: str):
    """Parse `Const R = <expr_text>` and return R's folded value."""
    prog, diags = parser.parse_text(f"Const R = {expr_text}\n", "t.cr6")
    assert not diags.errors, diags.errors
    return prog.consts["R"]


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2 + 3 * 4", 14),  # `*` tighter than `+`
        ("10 - 4 - 3", 3),  # `-` left-associative
        ("2^3^2", 64),  # `^` left-associative
        ("-2^2", -4),  # unary minus looser than `^`
        ("1 OR 0 AND 0", 0),  # AND/OR same precedence, left-to-right (oracle-verified;
        # classic "AND binds tighter than OR" would give -1 here -- see docs/frontend.md)
        ("2 \\ 3", 0),  # integer division
        ("7 INTDV 2", 3),  # `INTDV` is a word spelling of `\\`
        ("5 MOD 3", 2),
        ("TRUE", -1),
        ("FALSE", 0),
        ("NOT TRUE", 0),
        ("1 = 1", -1),
        ("1 <> 1", 0),
        ("1 << 2", 4),
        ("8 >> 2", 2),
        ('"a" & "b"', "ab"),
        ('"a" + "b"', "ab"),
        ("ABS(-5)", 5),
        ("INT(3.7)", 3.0),
        ('LEN("hello")', 5),
        ("CHR(65)", "A"),
    ],
)
def test_const_fold(expr, expected):
    assert const_value(expr) == expected


def test_predefined_symbols_case_insensitive():
    assert const_value("mV200") == 804
    assert const_value("mv200") == 804
    assert const_value("U9") == 209
    assert const_value("_50Hz") == 50


def test_earlier_const_referenced():
    prog, diags = parser.parse_text("Const A = 2\nConst B = A * 3\n", "t.cr6")
    assert not diags.errors
    assert prog.consts["A"] == 2
    assert prog.consts["B"] == 6


def test_is_constant_true_and_false():
    env = consteval.Env(profiles.get("cr6"))
    five = S.Number(1, "t", 5, "5")
    assert consteval.is_constant(five, env)
    undefined = S.Name(1, "t", "SomeUndefinedThing")
    assert not consteval.is_constant(undefined, env)


@pytest.mark.parametrize("sign", [1, -1])
def test_huge_integer_is_not_foldable_as_float(sign):
    with pytest.raises(consteval.NotConstant):
        consteval._num(sign * 10**309)


@pytest.mark.parametrize(
    "expr",
    [
        "+H",
        "-H",
        "Not H",
        "Chr(H)",
        "Abs(H)",
        "Int(H)",
        "Sqr(H)",
        'H & ""',
        'H + ""',
        "1 To H",
        "H + 1",
        "H - 1",
        "H * 1",
        "H / 1",
        "H \\ 1",
        "H ^ 1",
        "H Mod 1",
        "H << 1",
        "H >> 1",
        "H And 1",
        "H Or 1",
        "H Xor 1",
        "H Eqv 1",
        "H Imp 1",
        "H = 1",
    ],
)
def test_huge_integer_numeric_callers_stop_folding(expr):
    prog, diags = parser.parse_text(f"Const H = {'1' + '0' * 309}\nConst R = {expr}\n", "t.cr6")
    assert not diags.errors
    assert "R" not in prog.consts
    env = consteval.Env(profiles.get("cr6"), prog.consts)
    decl = prog.statements[-1]
    assert isinstance(decl, S.ConstDecl)
    with pytest.raises(consteval.NotConstant):
        consteval.value_of(decl.value, env)


@pytest.mark.parametrize("sign", ["", "+", "-"])
def test_huge_integer_directive_truth_does_not_raise(sign):
    _, diags = parser.parse_text(f"#If {sign}{'1' + '0' * 309}\n#EndIf\n", "t.cr6")
    assert diags.errors


def test_section_only_bound_inside_includesection(tmp_path):
    # Referencing `Section` outside an `IncludeSection()` is an ordinary undefined-symbol error
    # (confirmed with the oracle: `Invalid symbol in expression: Section = "Foo" .`).
    lib = tmp_path / "Lib.crb"
    lib.write_text('#If Section = "Defs" Then\nConst FromLib = 1\n#EndIf\n')
    text = 'IncludeSection("Defs", "CPU:Lib.crb")\n'
    prog, diags = parser.parse_text(text, "t.cr6", directory=tmp_path)
    assert prog.consts.get("FROMLIB") == 1

    prog2, diags2 = parser.parse_text('#If Section = "Foo" Then\nConst X = 1\n#EndIf\n', "t.cr6")
    assert diags2.errors  # Section is undefined at top level
    assert "X" not in prog2.consts
