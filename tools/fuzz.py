#!/usr/bin/env python3
"""Differential fuzzer for opencrbasic (PLAN.md phase 4).

Seeds are programs the real compiler ACCEPTS: `crbasic/*.CR6` (with their sibling `.crb`
libraries copied alongside) and the accepted programs in `tests/corpus/chm/` (acceptance decided
by actually asking the oracle, cache-backed -- see `tools/oracle.py`). Each mutant applies 1-2
random text-level mutation operators (`OPERATORS` below) to a seed's lines, seeded so a given
`--seed`/index always produces the same mutant. Both opencrbasic and the real compiler (through
`tools/oracle.py`) compile the mutant; the pair is classified as one of:

  exact         identical normalised output (not saved)
  false_reject  real compiler accepts, we reject               (most important)
  false_accept  real compiler rejects, we accept
  message_diff  both reject, different text
  mode_diff     both accept, different text (mode line or warnings)
  crash         opencrbasic raised a Python exception

Non-exact mutants are deduplicated by a coarse signature (class + the first differing output
line with digits/quoted text generalised) because a single systematic gap can otherwise produce
thousands of near-identical mutants; `--max-per-signature` bounds how many exemplars of each
signature are kept (crashes are never deduplicated away). Each kept exemplar is shrunk (delta
debugging over lines, bounded by `--shrink-budget` real compiles) and written to
`tests/corpus/fuzz/<logger>/<class>/<hash>.CR6`, with a sibling `<hash>.json` recording the seed,
the operators applied, and both compilers' final verdicts. A reproducer that still needs its
`crbasic/` library siblings (its shrunk text still has an `IncludeSection(...)` call) carries a
first-line marker comment `' fuzz-needs-siblings: crbasic` that `tools/fuzz_replay.py` looks for.

Usage:
    python3 tools/fuzz.py --logger cr6 --count 1500 --seed 1
    python3 tools/fuzz.py --logger all               # cr6 1500, others 300, seed 1

Budget: real compiles run under Wine at ~2s each; a 4-worker pool overlaps them (`--workers`).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import random
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from opencrbasic import profiles  # noqa: E402
from opencrbasic.compiler import compile_file  # noqa: E402
import oracle  # noqa: E402

CRBASIC_DIR = REPO / "crbasic"
CHM_DIR = ROOT / "tests" / "corpus" / "chm"
FUZZ_CORPUS = ROOT / "tests" / "corpus" / "fuzz"

DEFAULT_COUNTS = {"cr6": 1500, "cr1000x": 300, "cr300": 300, "cr350": 300}
CLASSES = ["crash", "false_reject", "false_accept", "message_diff", "mode_diff"]
# Crashes and false rejects are the important ones; keep more exemplars of those.
SIGNATURE_CAP_OVERRIDE = {"crash": 20, "false_reject": 10}

SIBLINGS_MARKER = "' fuzz-needs-siblings: crbasic"


# --------------------------------------------------------------------------------------
# Seeds
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Seed:
    group: str            # "repo" or "chm"
    path: Path            # the accepted source file
    siblings: Path | None  # directory to copy alongside it, or None


def _accepted(path: Path, siblings: Path | None, exe: Path) -> bool:
    try:
        r = oracle.run_oracle(path, siblings, compiler_path=exe)
    except oracle.OracleError as exc:
        print(f"oracle error on {path}: {exc}", file=sys.stderr)
        return False
    return r["exit"] == 0


def load_seeds(profile: profiles.Profile, exe: Path, workers: int) -> list[Seed]:
    candidates: list[Seed] = []
    if CRBASIC_DIR.is_dir():
        for p in sorted(CRBASIC_DIR.glob("*.CR6")):
            candidates.append(Seed("repo", p, CRBASIC_DIR))
    for p in sorted(CHM_DIR.glob("*.CR6")):
        candidates.append(Seed("chm", p, None))

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        ok = list(pool.map(lambda s: _accepted(s.path, s.siblings, exe), candidates))
    seeds = [s for s, good in zip(candidates, ok) if good]
    return seeds


# --------------------------------------------------------------------------------------
# Mutation operators
# --------------------------------------------------------------------------------------
# Each operator takes (lines: list[str], rng: random.Random) and returns a new list of lines,
# or None if it found nothing to mutate in this file.

_STRING_RE = re.compile(r'"(?:[^"]|"")*"')
_NUMBER_RE = re.compile(
    r'(?<![A-Za-z0-9_.])(&[Hh][0-9A-Fa-f]+|&[Bb][01]+|\d+\.\d+(?:[Ee][+-]?\d+)?'
    r'|\.\d+(?:[Ee][+-]?\d+)?|\d+[Ee][+-]?\d+|\d+)(?![A-Za-z0-9_.])'
)
_IDENT_RE = re.compile(r'\b[A-Za-z_][A-Za-z0-9_]*\$?\b')
_CALL_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*\$?)\s*\(([^()]*)\)')

REPLACEMENT_NUMBERS = ["0", "-1", "999999999999", "1.5", "&HFF"]
REPLACEMENT_ARGS = ['"fuzz"', "UndeclaredFuzzName123", "(1+2)"]
TYPE_NAMES = ["Long", "Boolean", "String * 16", "Float"]
KEYWORDS = [
    "If", "Then", "Else", "ElseIf", "EndIf", "For", "To", "Step", "Next", "Dim", "Public",
    "Const", "Scan", "NextScan", "SlowSequence", "EndSequence", "DataTable", "EndTable", "Sub",
    "EndSub", "Function", "EndFunction", "Do", "Loop", "While", "Wend", "Select", "Case",
    "EndSelect", "True", "False", "And", "Or", "Not", "As", "Long", "Boolean", "String", "Units",
    "Alias", "BeginProg", "EndProg", "DataInterval", "Average", "Sample",
]
FOREIGN_STATEMENTS = [
    "FuzzVar = FuzzVar + 1",
    "Delay(0,1,Sec)",
    "Public FuzzVar As Long",
    "FuzzVar = 1",
]
TWO_WORD_PAIRS = [("EndIf", 3), ("EndSelect", 3), ("NextScan", 4)]  # (one-word form, split index)


def _string_spans(line: str) -> list[tuple[int, int]]:
    return [m.span() for m in _STRING_RE.finditer(line)]


def _outside_strings(line: str, pattern: re.Pattern) -> list[re.Match]:
    spans = _string_spans(line)
    out = []
    for m in pattern.finditer(line):
        if not any(s <= m.start() < e for s, e in spans):
            out.append(m)
    return out


def _candidate_lines(lines: list[str], pattern: re.Pattern) -> list[tuple[int, re.Match]]:
    out = []
    for i, line in enumerate(lines):
        for m in _outside_strings(line, pattern):
            out.append((i, m))
    return out


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def op_delete_line(lines, rng):
    if len(lines) < 2:
        return None
    i = rng.randrange(len(lines))
    return lines[:i] + lines[i + 1:]


def op_duplicate_line(lines, rng):
    if not lines:
        return None
    i = rng.randrange(len(lines))
    return lines[: i + 1] + [lines[i]] + lines[i + 1:]


def op_swap_adjacent(lines, rng):
    if len(lines) < 2:
        return None
    i = rng.randrange(len(lines) - 1)
    new = list(lines)
    new[i], new[i + 1] = new[i + 1], new[i]
    return new


def op_rename_identifier(lines, rng):
    cands = _candidate_lines(lines, _IDENT_RE)
    if not cands:
        return None
    i, m = rng.choice(cands)
    line = lines[i]
    suffix = rng.choice(["_Fz", "2", "X", "_Mut"])
    new_line = line[: m.end()] + suffix + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_replace_numeric_literal(lines, rng):
    cands = _candidate_lines(lines, _NUMBER_RE)
    if not cands:
        return None
    i, m = rng.choice(cands)
    line = lines[i]
    repl = rng.choice(REPLACEMENT_NUMBERS)
    new_line = line[: m.start()] + repl + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def _split_call_args(argtext: str) -> list[str]:
    return [a for a in argtext.split(",")] if argtext.strip() != "" else []


def _find_calls(lines):
    out = []
    for i, line in enumerate(lines):
        spans = _string_spans(line)
        for m in _CALL_RE.finditer(line):
            if not any(s <= m.start() < e for s, e in spans):
                out.append((i, m))
    return out


def op_replace_argument(lines, rng):
    calls = [(i, m) for i, m in _find_calls(lines) if _split_call_args(m.group(2))]
    if not calls:
        return None
    i, m = rng.choice(calls)
    args = _split_call_args(m.group(2))
    j = rng.randrange(len(args))
    args[j] = rng.choice(REPLACEMENT_ARGS)
    line = lines[i]
    new_line = line[: m.start()] + m.group(1) + "(" + ",".join(args) + ")" + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_drop_argument(lines, rng):
    calls = [(i, m) for i, m in _find_calls(lines) if _split_call_args(m.group(2))]
    if not calls:
        return None
    i, m = rng.choice(calls)
    args = _split_call_args(m.group(2))
    j = rng.randrange(len(args))
    del args[j]
    line = lines[i]
    new_line = line[: m.start()] + m.group(1) + "(" + ",".join(args) + ")" + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_add_argument(lines, rng):
    calls = _find_calls(lines)
    if not calls:
        return None
    i, m = rng.choice(calls)
    args = _split_call_args(m.group(2))
    args.append(rng.choice(REPLACEMENT_ARGS))
    line = lines[i]
    new_line = line[: m.start()] + m.group(1) + "(" + ",".join(args) + ")" + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_change_keyword_case(lines, rng):
    cands = []
    for kw in KEYWORDS:
        pat = re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
        for i, m in _candidate_lines(lines, pat):
            cands.append((i, m))
    if not cands:
        return None
    i, m = rng.choice(cands)
    word = m.group(0)
    variants = [word.upper(), word.lower(), word.swapcase()]
    variants = [v for v in variants if v != word]
    if not variants:
        return None
    repl = rng.choice(variants)
    line = lines[i]
    new_line = line[: m.start()] + repl + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_insert_whitespace(lines, rng):
    nonblank = [i for i, l in enumerate(lines) if l.strip()]
    if not nonblank:
        return None
    i = rng.choice(nonblank)
    line = lines[i]
    pos = rng.randrange(len(line) + 1)
    new_line = line[:pos] + "  " + line[pos:]
    new = list(lines)
    new[i] = new_line
    return new


def op_insert_comment(lines, rng):
    i = rng.randrange(len(lines) + 1)
    indent = _indent_of(lines[i]) if i < len(lines) else ""
    new = list(lines)
    new.insert(i, f"{indent}' fuzz comment")
    return new


def op_insert_continuation(lines, rng):
    cands = [i for i, l in enumerate(lines) if "," in l.split("'", 1)[0]]
    if not cands:
        return None
    i = rng.choice(cands)
    line = lines[i]
    head = line.split("'", 1)[0]
    commas = [p for p, c in enumerate(head) if c == ","]
    if not commas:
        return None
    pos = rng.choice(commas) + 1
    new = list(lines)
    new[i:i + 1] = [line[:pos] + " _", line[pos:]]
    return new


def op_insert_foreign_statement(lines, rng):
    i = rng.randrange(len(lines) + 1)
    indent = _indent_of(lines[i]) if i < len(lines) else "  "
    new = list(lines)
    new.insert(i, indent + rng.choice(FOREIGN_STATEMENTS))
    return new


def op_toggle_two_word(lines, rng):
    cands = []
    for i, line in enumerate(lines):
        for word, split_at in TWO_WORD_PAIRS:
            for m in re.finditer(r'\b' + word + r'\b', line, re.IGNORECASE):
                cands.append((i, m, word, split_at, "join_to_split"))
            two = word[:split_at] + r'\s+' + word[split_at:]
            for m in re.finditer(r'\b' + two + r'\b', line, re.IGNORECASE):
                cands.append((i, m, word, split_at, "split_to_join"))
    if not cands:
        return None
    i, m, word, split_at, direction = rng.choice(cands)
    line = lines[i]
    text = m.group(0)
    if direction == "join_to_split":
        repl = text[:split_at] + " " + text[split_at:]
    else:
        repl = re.sub(r'\s+', "", text)
    new_line = line[: m.start()] + repl + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_change_declared_type(lines, rng):
    pat = re.compile(r'\bAs\s+(Long|Float|Boolean|String\s*\*\s*\d+|String)\b', re.IGNORECASE)
    cands = []
    for i, line in enumerate(lines):
        if not re.search(r'\b(Dim|Public)\b', line, re.IGNORECASE):
            continue
        for m in _outside_strings(line, pat):
            cands.append((i, m))
    if not cands:
        return None
    i, m = rng.choice(cands)
    new_type = rng.choice(TYPE_NAMES)
    line = lines[i]
    new_line = line[: m.start()] + f"As {new_type}" + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


def op_change_interval(lines, rng):
    interval_kw = re.compile(r'\b(Scan|DataInterval|DataTable)\s*\(', re.IGNORECASE)
    cands = []
    for i, line in enumerate(lines):
        km = interval_kw.search(line)
        if not km:
            continue
        depth = 0
        end = None
        for p in range(km.end() - 1, len(line)):
            if line[p] == "(":
                depth += 1
            elif line[p] == ")":
                depth -= 1
                if depth == 0:
                    end = p
                    break
        if end is None:
            end = len(line)
        for m in _NUMBER_RE.finditer(line, km.end(), end):
            cands.append((i, m))
    if not cands:
        return None
    i, m = rng.choice(cands)
    repl = rng.choice(REPLACEMENT_NUMBERS)
    line = lines[i]
    new_line = line[: m.start()] + repl + line[m.end():]
    new = list(lines)
    new[i] = new_line
    return new


OPERATORS = [
    ("delete_line", op_delete_line),
    ("duplicate_line", op_duplicate_line),
    ("swap_adjacent", op_swap_adjacent),
    ("rename_identifier", op_rename_identifier),
    ("replace_numeric_literal", op_replace_numeric_literal),
    ("replace_argument", op_replace_argument),
    ("drop_argument", op_drop_argument),
    ("add_argument", op_add_argument),
    ("change_keyword_case", op_change_keyword_case),
    ("insert_whitespace", op_insert_whitespace),
    ("insert_comment", op_insert_comment),
    ("insert_continuation", op_insert_continuation),
    ("insert_foreign_statement", op_insert_foreign_statement),
    ("toggle_two_word", op_toggle_two_word),
    ("change_declared_type", op_change_declared_type),
    ("change_interval", op_change_interval),
]


def apply_mutation(lines: list[str], rng: random.Random) -> tuple[list[str], list[str]]:
    n_ops = 1 if rng.random() < 0.6 else 2
    applied: list[str] = []
    order = list(OPERATORS)
    for _ in range(n_ops):
        rng.shuffle(order)
        for name, fn in order:
            result = fn(lines, rng)
            if result is not None:
                lines = result
                applied.append(name)
                break
    return lines, applied


# --------------------------------------------------------------------------------------
# Running both compilers
# --------------------------------------------------------------------------------------

def normalise(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def run_ours(path: Path, profile: profiles.Profile) -> tuple[int, str]:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = compile_file(path, profile)
        return (0 if result.ok else 1), "\n".join(result.result_lines())
    except Exception as exc:  # a crash is a result, not a harness failure
        return 2, f"CRASH {type(exc).__name__}: {exc}"


def run_theirs(path: Path, profile: profiles.Profile, siblings: Path | None) -> tuple[int, str]:
    exe = REPO / "compiler" / profile.exe_name
    r = oracle.run_oracle(path, siblings, compiler_path=exe)
    return r["exit"], r["stdout"]


def classify(our_exit: int, our_out: str, their_exit: int, their_out: str) -> str | None:
    if our_exit == 2:
        return "crash"
    if normalise(our_out) == normalise(their_out):
        return None
    if their_exit == 0 and our_exit != 0:
        return "false_reject"
    if their_exit != 0 and our_exit == 0:
        return "false_accept"
    if their_exit != 0 and our_exit != 0:
        return "message_diff"
    return "mode_diff"


Quad = tuple[int, str, int, str]


def materialise(seed: Seed, lines: list[str], tmp_dir: Path, name: str) -> Path:
    if seed.siblings is not None:
        for entry in sorted(seed.siblings.iterdir()):
            if entry.is_file():
                shutil.copy2(entry, tmp_dir / entry.name)
    out_path = tmp_dir / name
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def run_mutant(seed: Seed, lines: list[str], profile: profiles.Profile, name: str | None = None) -> Quad:
    name = name or seed.path.name
    with tempfile.TemporaryDirectory(prefix="fuzz-") as tmp:
        tmp_dir = Path(tmp)
        path = materialise(seed, lines, tmp_dir, name)
        our_exit, our_out = run_ours(path, profile)
        siblings = tmp_dir if seed.siblings is not None else None
        their_exit, their_out = run_theirs(path, profile, siblings)
    return our_exit, our_out, their_exit, their_out


# --------------------------------------------------------------------------------------
# Shrinking (delta debugging over lines)
# --------------------------------------------------------------------------------------

def shrink(seed: Seed, lines: list[str], profile: profiles.Profile, target_class: str,
           initial_quad: Quad, budget: int) -> tuple[list[str], int, Quad]:
    calls = 0
    best_quad = initial_quad

    def test(candidate: list[str]) -> str | None:
        nonlocal calls, best_quad
        if calls >= budget or not candidate:
            return None
        calls += 1
        quad = run_mutant(seed, candidate, profile)
        cls = classify(*quad)
        if cls == target_class:
            best_quad = quad
        return cls

    chunk = max(1, len(lines) // 2)
    while True:
        i = 0
        removed_any = False
        while i < len(lines):
            if calls >= budget:
                return lines, calls, best_quad
            candidate = lines[:i] + lines[i + chunk:]
            if test(candidate) == target_class:
                lines = candidate
                removed_any = True
            else:
                i += chunk
        if chunk == 1:
            break
        chunk = max(1, chunk // 2) if not removed_any else max(1, min(chunk, len(lines) // 2))
        if chunk == 0:
            break
    return lines, calls, best_quad


# --------------------------------------------------------------------------------------
# Saving reproducers
# --------------------------------------------------------------------------------------

def _signature(cls: str, our_out: str, their_out: str) -> tuple[str, str]:
    a = normalise(our_out).splitlines()
    b = normalise(their_out).splitlines()
    first_diff = ""
    for x, y in zip(a + [""] * max(0, len(b) - len(a)), b + [""] * max(0, len(a) - len(b))):
        if x != y:
            first_diff = y or x
            break
    generic = re.sub(r"\d+", "#", first_diff)
    generic = re.sub(r'"[^"]*"', '"…"', generic)
    return cls, generic


def save_reproducer(out_dir: Path, logger: str, cls: str, seed: Seed, lines: list[str],
                     ops: list[str], profile: profiles.Profile) -> dict | None:
    text = "\n".join(lines)
    needs_siblings = seed.siblings is not None and re.search(r"(?i)includesection\s*\(", text)
    header = []
    if needs_siblings:
        header.append(SIBLINGS_MARKER + f" -- from crbasic/{seed.path.name}, needs its sibling "
                       ".crb library files alongside this file to compile")
    header.append(f"' fuzz: seed={seed.group}/{seed.path.name} ops={'+'.join(ops)} class={cls}")
    full_text = "\n".join(header) + "\n" + text + "\n"

    h = hashlib.sha1(full_text.encode("utf-8")).hexdigest()[:12]
    dest_dir = out_dir / logger / cls
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{h}.CR6"

    # Re-run on the exact saved bytes (header comments shift line numbers but never the verdict)
    # so the recorded verdict matches what fuzz_replay.py will see.
    with tempfile.TemporaryDirectory(prefix="fuzz-save-") as tmp:
        tmp_dir = Path(tmp)
        siblings = CRBASIC_DIR if needs_siblings else None
        fake_seed = Seed(seed.group, seed.path, siblings)
        final_lines = full_text.splitlines()
        our_exit, our_out, their_exit, their_out = run_mutant(fake_seed, final_lines, profile, name=f"{h}.CR6")
    final_class = classify(our_exit, our_out, their_exit, their_out)
    if final_class is None:
        return None  # header/renaming happened to make it exact after all; not worth saving

    dest.write_text(full_text, encoding="utf-8")
    meta = {
        "file": str(dest.relative_to(ROOT)),
        "logger": logger,
        "class": final_class,
        "seed": f"{seed.group}/{seed.path.name}",
        "needs_siblings": bool(needs_siblings),
        "operators": ops,
        "our_exit": our_exit,
        "their_exit": their_exit,
        "ours": our_out,
        "theirs": their_out,
    }
    (dest_dir / f"{h}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logger", default="cr6", help="cr6 | cr1000x | cr300 | cr350 | all")
    ap.add_argument("--count", type=int, default=None, help="mutants to generate (default per logger)")
    ap.add_argument("--seed", type=int, default=1, help="RNG seed base, for reproducibility")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-per-signature", type=int, default=3,
                     help="exemplars kept per (class, generic-message) signature")
    ap.add_argument("--shrink-budget", type=int, default=12,
                     help="max real compiles spent shrinking one reproducer")
    ap.add_argument("--shrink-budget-total", type=int, default=400,
                     help="max real compiles spent shrinking across the whole run")
    ap.add_argument("--out", type=Path, default=FUZZ_CORPUS)
    args = ap.parse_args()

    loggers = list(DEFAULT_COUNTS) if args.logger == "all" else [args.logger]
    overall_ok = True
    for logger in loggers:
        overall_ok &= run_logger(logger, args)
    return 0 if overall_ok else 1


def run_logger(logger: str, args: argparse.Namespace) -> bool:
    profile = profiles.get(logger)
    exe = REPO / "compiler" / profile.exe_name
    if not exe.is_file():
        print(f"{logger}: compiler {exe} not found, skipping", file=sys.stderr)
        return False
    count = args.count if args.count is not None else DEFAULT_COUNTS.get(logger, 300)

    t0 = time.time()
    seeds = load_seeds(profile, exe, args.workers)
    if not seeds:
        print(f"{logger}: no accepted seeds found", file=sys.stderr)
        return False
    print(f"{logger}: {len(seeds)} accepted seeds "
          f"({sum(1 for s in seeds if s.group == 'repo')} repo, "
          f"{sum(1 for s in seeds if s.group == 'chm')} chm), generating {count} mutants "
          f"(seed={args.seed})")

    mutants = []
    for i in range(count):
        mrng = random.Random(f"{args.seed}:{logger}:{i}")
        sd = mrng.choice(seeds)
        original = sd.path.read_text(encoding="utf-8", errors="replace").splitlines()
        mutated, ops = apply_mutation(original, mrng)
        mutants.append((i, sd, mutated, ops))

    def work(item):
        i, sd, lines, ops = item
        quad = run_mutant(sd, lines, profile)
        return i, classify(*quad), quad

    results: list[tuple[str | None, Quad]] = [None] * count
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        for i, cls, quad in pool.map(work, mutants):
            results[i] = (cls, quad)

    counts = Counter(cls or "exact" for cls, _ in results)
    print(f"{logger}: classified {count} mutants in {time.time() - t0:.0f}s -> "
          + ", ".join(f"{k}={counts.get(k, 0)}" for k in ["exact"] + CLASSES))

    seen: Counter = Counter()
    remaining_shrink_budget = args.shrink_budget_total
    saved: list[dict] = []
    for i, (cls, quad) in enumerate(results):
        if cls is None:
            continue
        _, sd, lines, ops = mutants[i]
        our_exit, our_out, their_exit, their_out = quad
        sig = _signature(cls, our_out, their_out)
        cap = SIGNATURE_CAP_OVERRIDE.get(cls, args.max_per_signature)
        if seen[sig] >= cap:
            continue
        seen[sig] += 1

        budget = min(args.shrink_budget, remaining_shrink_budget)
        if budget > 0:
            shrunk_lines, used, quad = shrink(sd, lines, profile, cls, quad, budget)
            remaining_shrink_budget -= used
        else:
            shrunk_lines = lines

        meta = save_reproducer(args.out, logger, cls, sd, shrunk_lines, ops, profile)
        if meta is not None:
            saved.append(meta)

    print(f"{logger}: saved {len(saved)} reproducers to {args.out / logger}"
          f" (shrink budget used: {args.shrink_budget_total - remaining_shrink_budget})")

    summary_path = args.out / f"summary-{logger}.json"
    args.out.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps({
        "logger": logger, "count": count, "seed": args.seed,
        "counts": dict(counts), "saved": len(saved),
    }, indent=2) + "\n", encoding="utf-8")
    return True


if __name__ == "__main__":
    sys.exit(main())
