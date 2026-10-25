"""Impact classes of a differential result, the v1.0 measure (`PLAN.md` "Where v1.0 starts").

Each row (from `fuzz_replay.py` or `difftest.py`) is judged on independent dimensions: verdict,
execution mode, first error, warnings and later diagnostics. Operational failures (runner errors,
implementation exceptions, crashes) are kept apart and never count as a verdict. The first error is
the first non-warning diagnostic with its file, line and message.
"""

import re
from collections import Counter

EXACT = "exact"
LATER = "same first error, later diagnostics differ"
ORDER = "same first error, same diagnostics in another order"
MESSAGE = "same first location, different message"
MISSED = "we miss the reference's first error"
EARLIER = "we report an earlier first location"
NO_DIAG = "both reject, a side has no diagnostic"
WARNINGS = "both accept, warnings differ"
MODE = "both accept, execution mode differs"
FALSE_ACCEPT = "false accept"
FALSE_REJECT = "false reject"
OPERATIONAL = "operational failure"

CLASSES = [EXACT, LATER, ORDER, MESSAGE, MISSED, EARLIER, NO_DIAG, WARNINGS, MODE, FALSE_ACCEPT,
           FALSE_REJECT, OPERATIONAL]
FIRST_ERROR_OK = {EXACT, LATER, ORDER, WARNINGS, MODE}  # first error equal (or both accept)
GATED_LOGGERS = ("cr6", "cr1000x")   # human decision 2026-10-24; CR300/CR350 are best effort

_LINE = re.compile(r"^(?:<([^>]+)>\s*)?line (\d+):\s*(.*)$")


def diagnostics(text: str) -> list[tuple[str, int, str]]:
    """Non-warning diagnostics after the result line, as (file, line, message)."""
    out = []
    for ln in text.splitlines()[1:]:
        ln = ln.strip()
        if not ln:
            continue
        m = _LINE.match(ln)
        if m:
            if not m.group(3).startswith("Warning"):
                out.append((m.group(1) or "", int(m.group(2)), m.group(3).strip()))
        elif not ln.startswith("Warning"):
            out.append(("", -1, ln))
    return out


def _head(text: str) -> str:
    return text.splitlines()[0].split("--", 1)[-1].strip() if text else ""


def impact_class(row: dict) -> str:
    """The impact class of a row with `our_exit`, `ours`, `their_exit`, `theirs`, and optionally
    the `fuzz.classify` result as `now_class` (replay) or `class` (difftest)."""
    ours, theirs = row.get("our_exit"), row.get("their_exit")
    cls = row.get("now_class", row.get("class", ""))
    if theirs not in (0, 1) or ours not in (0, 1) or cls in (
            "oracle_error", "recorded_runner_failure", "stale_recording", "implementation_exception",
            "crash", "hang"):
        return OPERATIONAL
    if cls is None:
        return EXACT
    if cls in ("false_accept", "false_reject"):
        return FALSE_ACCEPT if cls == "false_accept" else FALSE_REJECT
    if ours == 0 and theirs == 1:
        return FALSE_ACCEPT
    if ours == 1 and theirs == 0:
        return FALSE_REJECT
    o_text, t_text = row.get("ours") or "", row.get("theirs") or ""
    norm = lambda s: "\n".join(line.rstrip() for line in s.strip().splitlines()[1:])
    if _head(o_text) == _head(t_text) and norm(o_text) == norm(t_text):
        return EXACT
    if ours == 0:
        return MODE if _head(o_text) != _head(t_text) else WARNINGS
    o, t = diagnostics(o_text), diagnostics(t_text)
    if not o or not t:
        return NO_DIAG
    if o[0] != t[0]:
        if o[0][:2] == t[0][:2]:
            return MESSAGE
        return EARLIER if o[0][:2] < t[0][:2] else MISSED
    return ORDER if sorted(o) == sorted(t) else LATER


def table(rows: list[dict]) -> str:
    """Impact classes by logger, as a text table."""
    by = Counter((r["logger"], r["impact"]) for r in rows)
    loggers = sorted({r["logger"] for r in rows})
    lines = ["class".ljust(52) + "".join(lg.rjust(9) for lg in loggers) + "total".rjust(9)]
    for cls in CLASSES:
        counts = [by[(lg, cls)] for lg in loggers]
        if any(counts):
            lines.append(cls.ljust(52) + "".join(str(n).rjust(9) for n in counts)
                         + str(sum(counts)).rjust(9))
    return "\n".join(lines)


def regressions(baseline: list[dict], rows: list[dict], loggers=GATED_LOGGERS) -> list[str]:
    """Gated regressions of `rows` against `baseline`, matched by (logger, file): a new false
    accept or false reject, a new mode difference, or a first error that matched and no longer
    does. Rows absent from the baseline are judged alone: any verdict or mode difference counts."""
    before = {(r["logger"], r["file"]): r["impact"] for r in baseline}
    out = []
    for r in rows:
        if r["logger"] not in loggers:
            continue
        now, was = r["impact"], before.get((r["logger"], r["file"]))
        if now in (FALSE_ACCEPT, FALSE_REJECT, MODE) and now != was:
            out.append(f"{now}: {r['logger']} {r['file']}")
        elif was in FIRST_ERROR_OK and now not in FIRST_ERROR_OK and now != OPERATIONAL:
            out.append(f"first error regressed ({was} -> {now}): {r['logger']} {r['file']}")
    return out


if __name__ == "__main__":
    def row(oe, o, te, t):
        return {"our_exit": oe, "ours": o, "their_exit": te, "theirs": t}
    F = "t -- Compile Failed!\n\n"
    assert impact_class(row(0, "t -- Compiled in PipelineMode.", 1, F + "line 2: x.")) == FALSE_ACCEPT
    assert impact_class(row(0, "t -- Compiled in PipelineMode.", 0, "t -- Compiled in SequentialMode.")) == MODE
    assert impact_class(row(1, F + "line 2: a.\nline 3: b.", 1, F + "line 2: a.\nline 4: c.")) == LATER
    assert impact_class(row(1, F + "line 3: b.\nline 2: a.", 1, F + "line 2: a.\nline 3: b.")) == MISSED
    assert impact_class(row(1, F + "line 2: z.", 1, F + "line 2: a.")) == MESSAGE
    assert impact_class(row(None, "", 1, F)) == OPERATIONAL
    base = [{"logger": "cr6", "file": "f", "impact": EXACT}]
    assert regressions(base, [{"logger": "cr6", "file": "f", "impact": MESSAGE}])
    assert not regressions(base, [{"logger": "cr300", "file": "f", "impact": FALSE_ACCEPT}])
    print("ok")
