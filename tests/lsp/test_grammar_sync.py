from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GRAMMAR = ROOT / "vscode" / "syntaxes" / "crbasic.tmLanguage.json"
GEN = ROOT / "tools" / "gen_grammar.py"


def _load() -> dict:
    return json.loads(GRAMMAR.read_text(encoding="utf-8"))


def test_generator_check_passes():
    done = subprocess.run([sys.executable, str(GEN), "--check"], capture_output=True, text=True,
                          env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert done.returncode == 0, done.stderr


def test_check_detects_stale_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "tools"))
    try:
        import gen_grammar
    finally:
        sys.path.remove(str(ROOT / "tools"))
    stale = tmp_path / "g.json"
    stale.write_text("{}\n")
    monkeypatch.setattr(gen_grammar, "OUTPUT", stale.with_name("g.json"))
    monkeypatch.setattr(gen_grammar, "ROOT", tmp_path)
    assert gen_grammar.main(["--check"]) == 1
    gen_grammar.main([])
    assert gen_grammar.main(["--check"]) == 0
    assert stale.read_text() == gen_grammar.render() == GRAMMAR.read_text(encoding="utf-8")


def test_json_shape():
    text = GRAMMAR.read_text(encoding="utf-8")
    assert text.endswith("}\n") and not text.endswith("\n\n")
    grammar = json.loads(text)
    assert grammar["scopeName"] == "source.crbasic"
    assert text == json.dumps(grammar, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _rules(grammar: dict):
    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    return walk(grammar)


def _py(regex: str) -> re.Pattern:
    flags = 0
    if regex.startswith("(?i)"):
        regex, flags = regex[4:], re.IGNORECASE
    return re.compile(regex, flags)


def test_every_regex_compiles():
    count = 0
    for rule in _rules(_load()):
        for key in ("match", "begin", "end"):
            if key in rule:
                _py(rule[key])
                count += 1
    assert count >= 10


def _flat_rules(grammar: dict):
    """Top-level rules in match order, includes resolved. Only `match` rules are supported."""
    repo = grammar["repository"]

    def expand(patterns):
        for p in patterns:
            if "include" in p:
                yield from expand([repo[p["include"].lstrip("#")]])
            elif "patterns" in p:
                yield from expand(p["patterns"])
            else:
                assert "match" in p, "tiny matcher supports match rules only"
                yield p
    return list(expand(grammar["patterns"]))


def tokenize(line: str) -> list[tuple[str, str]]:
    """Earliest match wins, ties go to the earlier rule; unmatched text is skipped. This is a
    simplification of TextMate's scanner (single line, no begin/end, Python `re`, not Oniguruma)."""
    rules = [(_py(r["match"]), r) for r in _flat_rules(_load())]
    out: list[tuple[str, str]] = []
    pos = 0
    while pos <= len(line):
        best = None
        for rx, rule in rules:
            m = rx.search(line, pos)
            if m and (m.end() > m.start()) and (best is None or m.start() < best[0].start()):
                best = (m, rule)
        if best is None:
            break
        m, rule = best
        if "captures" in rule:
            for g, cap in sorted(rule["captures"].items()):
                out.append((cap["name"], m.group(int(g))))
        else:
            out.append((rule["name"], m.group(0)))
        pos = m.end()
    return out


@pytest.mark.parametrize("line,expected", [
    ("BeginProg", [("keyword.control.crbasic", "BeginProg")]),
    ("  voltse(Temp,1,mV5000,U1,1,1,0,True,0,_60Hz,1,0)", [
        ("support.function.instruction.crbasic", "voltse"),
        ("constant.numeric.decimal.crbasic", "1"),
        ("support.constant.crbasic", "mV5000"),
        ("support.constant.crbasic", "U1"),
        ("constant.numeric.decimal.crbasic", "1"),
        ("constant.numeric.decimal.crbasic", "1"),
        ("constant.numeric.decimal.crbasic", "0"),
        ("support.constant.crbasic", "True"),
        ("constant.numeric.decimal.crbasic", "0"),
        ("support.constant.crbasic", "_60Hz"),
        ("constant.numeric.decimal.crbasic", "1"),
        ("constant.numeric.decimal.crbasic", "0"),
    ]),
    ("x = Len(\"a\"\"b\") ' note", [
        ("support.function.builtin.crbasic", "Len"),
        ("string.quoted.double.crbasic", '"a""b"'),
        ("comment.line.apostrophe.crbasic", "' note"),
    ]),
    ("s = \"it's\" & Chr$(65)", [
        ("string.quoted.double.crbasic", '"it\'s"'),
        ("support.function.builtin.crbasic", "Chr$"),
        ("constant.numeric.decimal.crbasic", "65"),
    ]),
    ("#If LoggerType = CR6", [
        ("keyword.control.directive.crbasic", "#If"),
        ("support.constant.crbasic", "LoggerType"),
        ("support.constant.crbasic", "CR6"),
    ]),
    ("  #ElseIf X", [("keyword.control.directive.crbasic", "#ElseIf")]),
    ("v = &H1F + &b101 + .5 + 1.5E-3 + 42 + 2.15R", [
        ("constant.numeric.hex.crbasic", "&H1F"),
        ("constant.numeric.binary.crbasic", "&b101"),
        ("constant.numeric.decimal.crbasic", ".5"),
        ("constant.numeric.decimal.crbasic", "1.5E-3"),
        ("constant.numeric.decimal.crbasic", "42"),
        ("constant.numeric.decimal.crbasic", "2.15R"),
    ]),
    ("If a <> b And Not c Then", [
        ("keyword.control.crbasic", "If"),
        ("keyword.operator.word.crbasic", "And"),
        ("keyword.operator.word.crbasic", "Not"),
        ("keyword.control.crbasic", "Then"),
    ]),
    ("x += 1", [("keyword.operator.crbasic", "+="), ("constant.numeric.decimal.crbasic", "1")]),
    ("Scan1 = Tmp.001", []),   # identifiers that merely contain a keyword or end in digits
    ("MyIf = 3", [("constant.numeric.decimal.crbasic", "3")]),
])
def test_tokenize_samples(line, expected):
    got = tokenize(line)
    # symbol operators (`=`, `&`, `+=`, ...) are left out of the expectations
    got = [t for t in got if t[0] != "keyword.operator.crbasic" or t in expected]
    assert got == expected


def test_comment_inside_string_is_not_a_comment():
    scopes = [s for s, _ in tokenize("'only a comment")]
    assert scopes == ["comment.line.apostrophe.crbasic"]
    scopes = [s for s, _ in tokenize('x = "a \' b"')]
    assert "comment.line.apostrophe.crbasic" not in scopes
