"""Corpus tests: parse every program in `crbasic/` and `tests/corpus/chm/`.

The parser must never raise (checked for every file, regardless of the oracle's verdict). For a
program the real compiler *accepts*, parsing must additionally produce zero `Unparsed`
statements -- this is the corpus's main signal, since a rejected program may legitimately use
syntax this front end doesn't (yet) model. Oracle acceptance is read from `tools/oracle.py`'s
on-disk cache (`.oracle-cache/`); populate it first with a warm-up run (see PLAN.md/PROGRESS.md)
-- an uncached run takes ~2s per file under Wine, so a cold 500-file run is slow but a warm one
is instant. A file oracle.py cannot run at all (no Wine available) is skipped, not failed.

A small, explicit set of oracle-accepted CHM examples is excused (`KNOWN_GAPS`): each is a
narrow, rare, documented language feature this front end does not implement. See
docs/frontend.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opencrbasic import parser
from opencrbasic import syntax as S

OPENCRBASIC_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = OPENCRBASIC_ROOT.parent
CRBASIC_DIR = REPO_ROOT / "crbasic"
CHM_DIR = OPENCRBASIC_ROOT / "tests" / "corpus" / "chm"

# Entry programs (compiled directly, not pulled in via Include/IncludeSection) in `crbasic/`.
CRBASIC_ENTRY_PROGRAMS = sorted(CRBASIC_DIR.glob("*.CR6")) if CRBASIC_DIR.is_dir() else []
CHM_PROGRAMS = sorted(CHM_DIR.glob("*.CR6")) if CHM_DIR.is_dir() else []

# oracle-accepted CHM examples this front end knowingly does not fully parse; see docs/frontend.md.
KNOWN_GAPS = {
    "samplemaxmin_1.CR6",   # `WS_ms=WS_ms+=1`: a doc typo CR6Comp happens to still accept
    "snmpvariable_1.CR6",   # `1.3.6.1.4.1.43592.55.1.4`: a raw dotted-OID literal argument
}


def _unparsed(prog: S.Program) -> list[S.Unparsed]:
    return [s for s in prog.statements if isinstance(s, S.Unparsed)]


def _try_oracle():
    try:
        import oracle  # tools/oracle.py, on sys.path via pyproject.toml's pythonpath
    except ImportError:
        return None
    if sys.platform != "win32":
        try:
            oracle._find_wine()
        except oracle.OracleError:
            return None
    return oracle


ORACLE = _try_oracle()


def _oracle_accepts(path: Path) -> bool | None:
    """True/False if the oracle's cached verdict for `path` is available, else None (skip).

    No `siblings_dir`: the CHM examples are each self-contained (no cross-file Include), and the
    cache key must match how the corpus warm-up populated `.oracle-cache/` (also without
    `siblings_dir`) or every lookup here would miss and re-invoke Wine per file.
    """
    if ORACLE is None:
        return None
    try:
        result = ORACLE.run_oracle(path)
    except Exception:
        return None
    return result["exit"] == 0


# ------------------------------------------------------------------ never raises, anywhere


@pytest.mark.parametrize("path", CRBASIC_ENTRY_PROGRAMS, ids=lambda p: p.name)
def test_crbasic_entry_programs_never_raise(path):
    parser.parse_file(path)  # must not raise


@pytest.mark.parametrize("path", CHM_PROGRAMS, ids=lambda p: p.name)
def test_chm_programs_never_raise(path):
    parser.parse_file(path)


# ------------------------------------------------------------------ zero Unparsed where it matters


@pytest.mark.parametrize("path", CRBASIC_ENTRY_PROGRAMS, ids=lambda p: p.name)
def test_crbasic_entry_programs_fully_parse(path):
    """Every program this repository actually compiles (`scripts/verify.py`) must parse with
    zero `Unparsed` statements -- these are known-good, currently-compiling CRBasic."""
    prog, diags = parser.parse_file(path)
    bad = _unparsed(prog)
    assert not bad, [(u.line, u.file, u.text, u.reason) for u in bad]


@pytest.mark.parametrize("path", CHM_PROGRAMS, ids=lambda p: p.name)
def test_chm_programs_accepted_by_oracle_fully_parse(path):
    if path.name in KNOWN_GAPS:
        pytest.skip("known gap; see docs/frontend.md")
    accepted = _oracle_accepts(path)
    if accepted is None:
        pytest.skip("oracle unavailable (no Wine/compiler in this environment)")
    if not accepted:
        pytest.skip("oracle rejects this program; parsing it fully is not required")
    prog, diags = parser.parse_file(path)
    bad = _unparsed(prog)
    assert not bad, [(u.line, u.file, u.text, u.reason) for u in bad]


def test_corpus_summary(capsys):
    """Not an assertion -- prints aggregate parse statistics for the whole CHM corpus (all 507
    programs, accepted or not) so a human/agent can see the overall shape at a glance."""
    total_stmts = total_unparsed = 0
    crashed = 0
    for path in CHM_PROGRAMS:
        try:
            prog, _ = parser.parse_file(path)
        except Exception:
            crashed += 1
            continue
        total_stmts += len(prog.statements)
        total_unparsed += len(_unparsed(prog))
    with capsys.disabled():
        print(
            f"\nCHM corpus: {len(CHM_PROGRAMS)} files, {crashed} crashed, "
            f"{total_stmts} statements, {total_unparsed} unparsed "
            f"({100 * total_unparsed / total_stmts:.1f}%)" if total_stmts else ""
        )
    assert crashed == 0
