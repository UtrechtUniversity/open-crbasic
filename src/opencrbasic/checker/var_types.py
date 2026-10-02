"""Variable-data-type argument checks (pass 2, tier C). Rules, fixtures and oracle evidence:
`docs/instructions.md` "Variable data types" (`tests/fixtures/var_types.json`).

Not a uniform per-type-code rule: each instruction applies its own requirement, so this module is
a small table of them. Unknown names, functions and operand mixes the oracle was not probed on
have no type (`None`), and a check that needs a type it does not have stays silent.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

_NUMERIC = ("FLOAT", "LONG")


def _types(program: S.Program) -> tuple[dict[str, str], dict[str, str]]:
    """Declared variable types and `Alias` name -> target variable name (upper case)."""
    types = symbols.declared_variable_types(program)
    alias: dict[str, str] = {}
    for st in program.statements:
        if isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS" and st.args and \
                isinstance(st.args[0], (S.Name, S.Index)):
            for a in st.args[1:]:
                if isinstance(a, S.Name):
                    alias[a.name.upper()] = st.args[0].name.upper()
    return types, alias


class _Typer:
    def __init__(self, program: S.Program):
        self.types, self.alias = _types(program)
        self.consts = {st.name.upper(): st for st in program.statements
                       if isinstance(st, S.ConstDecl)}

    def base(self, name: str) -> str:
        return self.alias.get(name.upper(), name.upper())

    def variable(self, expr: S.Expr | None) -> str | None:
        """Upper-case declared-variable name an expression refers to, or None."""
        if isinstance(expr, (S.Name, S.Index)):
            name = self.base(expr.name)
            return name if name in self.types else None
        return None

    def of(self, expr: S.Expr | None, depth: int = 0) -> str | None:
        """FLOAT / LONG / STRING / BOOLEAN / DOUBLE, or None when not known."""
        if isinstance(expr, S.Number):
            text = expr.text.upper()
            if text.startswith("&"):
                return "LONG"
            return "FLOAT" if "." in text or "E" in text else "LONG"
        if isinstance(expr, S.String):
            return "STRING"
        var = self.variable(expr)
        if var is not None:
            return self.types[var]
        if isinstance(expr, S.Name):
            if expr.name.upper() in ("TRUE", "FALSE"):
                return "LONG"
            if expr.name.upper() == "NAN":
                return "FLOAT"
            const = self.consts.get(expr.name.upper())
            if const is None or depth > 8:
                return None
            if const.type:
                return const.type.upper()
            return self.of(const.value, depth + 1)
        if isinstance(expr, S.Unary) and expr.op in ("-", "+"):
            return self.of(expr.operand, depth + 1) if \
                self.of(expr.operand, depth + 1) in _NUMERIC else None
        if isinstance(expr, S.Binary) and expr.op in ("+", "-", "*", "/"):
            left, right = self.of(expr.left, depth + 1), self.of(expr.right, depth + 1)
            if left in _NUMERIC and right in _NUMERIC:
                return "LONG" if left == right == "LONG" and expr.op != "/" else "FLOAT"
        return None


def _same_type(dest: str, source: str) -> bool:
    return dest == source or (dest == "STRING" and source == "FLOAT")


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    typer = _Typer(program)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw, args = stmt.keyword.upper(), stmt.args
        if kw not in profile.instructions:
            continue
        message = None
        if kw == "ROUTERSNEIGHBORS" and args:
            kind = "STRING" if isinstance(args[0], S.String) else typer.of(args[0])
            if typer.variable(args[0]) is not None or isinstance(args[0], S.String):
                if kind != "LONG":
                    message = "Destination variable must be type LONG"
        elif kw == "SORTSPAINDEXED" and len(args) > 2:
            dest, source = typer.of(args[0]), typer.of(args[2])
            if typer.variable(args[0]) is not None and dest and source and \
                    not _same_type(dest, source):
                message = "Source and Dest types must be the same."
        if message:
            diags.error(stmt.line, message, stmt.file)

