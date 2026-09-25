"""Per-instruction argument checks that sit on top of the generic type-code parsing
(`docs/type-codes.md`) and are not expressible from the type code alone.

Currently just the analog-channel range check, which is per-logger data (`terminals.py`,
`data/<logger>/terminals.json`, evidence: `docs/loggers.md`); this module is the entry point
`checker/__init__.py` calls (`PLAN.md`'s file ownership keeps the channel logic itself, and the
rest of this module's oracle evidence, separate from `checker/__init__.py`'s own owner).
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import terminals


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    terminals.check(program, profile, diags, env)
