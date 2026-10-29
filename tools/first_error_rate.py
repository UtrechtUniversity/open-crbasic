"""First-error rate on reference-rejected replay cases, per profile (PLAN.md M2 exit criterion).

    python tools/fuzz_replay.py --recorded --json now.json
    python tools/first_error_rate.py now.json [baseline.json]

A case counts when the reference rejects it and our first error (file, line, message) is the same.
With a baseline, both reports are also compared on the reproducers they share.
"""

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import impact  # noqa: E402

SAME_FIRST = {impact.EXACT, impact.LATER, impact.ORDER}


def rates(rows: list[dict], only: set[str] | None = None) -> str:
    total, same = Counter(), Counter()
    for r in rows:
        if r.get("their_exit") != 1 or (only is not None and _key(r) not in only):
            continue
        cls = r.get("impact") or impact.impact_class(r)
        if cls == impact.OPERATIONAL:
            continue
        total[r["logger"]] += 1
        same[r["logger"]] += cls in SAME_FIRST
    return "  ".join(f"{lg} {same[lg]}/{total[lg]} = {same[lg] / total[lg]:.1%}" for lg in sorted(total))


def _key(row: dict) -> str:
    return row["file"].split("opencrbasic/")[-1]


def main() -> None:
    reports = [json.loads(Path(p).read_text())["rows"] for p in sys.argv[1:3]]
    print("now     ", rates(reports[0]))
    if len(reports) == 2:
        shared = {_key(r) for r in reports[0]} & {_key(r) for r in reports[1]}
        print("shared  ", rates(reports[0], shared))
        print("baseline", rates(reports[1], shared))


if __name__ == "__main__":
    main()
