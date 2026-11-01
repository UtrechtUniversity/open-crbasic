"""`opencrbasic.toml`: check severities and scoped suppressions (`PLAN.md` "Configuration",
`docs/checker.md` "Check configuration and report file"). Configuration never touches the reference
channel: every problem is a `ConfigError` that the caller reports on stderr."""

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles

FILE_NAME = "opencrbasic.toml"
SEVERITIES = ("error", "warning", "info", "off")


class ConfigError(Exception):
    pass


@dataclass
class Suppression:
    code: str
    file: str  # glob, relative to the configuration file, case-insensitive
    line: int | None
    reason: str


@dataclass
class Config:
    path: Path | None = None  # None: no file, built-in defaults
    checks: dict[str, str] = field(default_factory=dict)
    profile: dict[str, dict[str, str]] = field(default_factory=dict)
    entry: dict[str, dict[str, str]] = field(default_factory=dict)  # key: normalized relative path
    suppress: list[Suppression] = field(default_factory=list)

    @property
    def root(self) -> Path | None:
        return self.path.parent if self.path else None


def norm(path: str) -> str:
    return Path(os.path.normpath(path)).as_posix().lower()


def find(entry: Path) -> Path | None:
    """The workspace root: the nearest ancestor directory of the entry file holding the file."""
    for directory in entry.resolve().parents:
        if (directory / FILE_NAME).is_file():
            return directory / FILE_NAME
    return None


def _severities(table: object, codes, where: str) -> dict[str, str]:
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    out = {}
    for code, value in table.items():
        if code not in codes:
            raise ConfigError(f"{where}: unknown check code {code}")
        if value not in SEVERITIES:
            raise ConfigError(f"{where}: unknown severity {value!r} for {code}; choose from {', '.join(SEVERITIES)}")
        out[code] = value
    return out


def parse_overrides(items: list[str], codes) -> dict[str, str]:
    """`CODE=severity` strings (`--check`, an LSP setting) as a severity map."""
    out = {}
    for item in items:
        code, sep, value = item.partition("=")
        if not sep:
            raise ConfigError(f"--check {item}: expected CODE=SEVERITY")
        out.update(_severities({code: value}, codes, f"--check {item}"))
    return out


def load(path: Path, codes) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: cannot read: {e}") from None
    return loads(text, path, codes)


def loads(text: str, path: Path, codes) -> Config:
    """`load` of a file's text already read (the language server reads it through its sources)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    name = str(path)
    if unknown := set(data) - {"checks", "profile", "entry", "suppress"}:
        raise ConfigError(f"{name}: unknown key {sorted(unknown)[0]}")
    cfg = Config(path=path.resolve())
    cfg.checks = _severities(data.get("checks", {}), codes, f"{name} [checks]")
    for logger, table in _tables(data, "profile", name).items():
        if logger not in profiles.PROFILES:
            raise ConfigError(f"{name} [profile.{logger}]: unknown logger profile")
        cfg.profile[logger] = _severities(table.get("checks", {}), codes, f"{name} [profile.{logger}.checks]")
    for entry, table in _tables(data, "entry", name).items():
        cfg.entry[norm(entry)] = _severities(table.get("checks", {}), codes, f'{name} [entry."{entry}".checks]')
    suppress = data.get("suppress", [])
    if not isinstance(suppress, list):
        raise ConfigError(f"{name}: suppress must be an array of tables ([[suppress]])")
    for i, s in enumerate(suppress, 1):
        where = f"{name} [[suppress]] #{i}"
        if not isinstance(s, dict) or set(s) - {"code", "file", "line", "reason"}:
            raise ConfigError(f"{where}: keys are code, file, line, reason")
        if not isinstance(s.get("code"), str) or s["code"] not in codes:
            raise ConfigError(f"{where}: unknown check code {s.get('code')}")
        if not isinstance(s.get("file"), str) or not s["file"]:
            raise ConfigError(f"{where}: file (a glob) is required")
        if not isinstance(s.get("reason"), str) or not s["reason"].strip():
            raise ConfigError(f"{where}: reason is required")
        line = s.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ConfigError(f"{where}: line must be a positive integer")
        cfg.suppress.append(Suppression(s["code"], s["file"].lower(), line, s["reason"]))
    return cfg


def _tables(data: dict, key: str, name: str) -> dict[str, dict]:
    tables = data.get(key, {})
    if not isinstance(tables, dict) or not all(isinstance(t, dict) for t in tables.values()):
        raise ConfigError(f"{name}: {key} must hold tables ([{key}.<name>.checks])")
    return tables


def severity(
    cfg: Config, code: str, default: str, logger: str, entry: str, cli: dict[str, str], lsp: dict[str, str]
) -> str:
    """Highest layer that names `code`: CLI, LSP, entry, profile, `[checks]`, built-in default."""
    for layer in (cli, lsp, cfg.entry.get(entry, {}), cfg.profile.get(logger, {}), cfg.checks):
        if code in layer:
            return layer[code]
    return default


def suppression(cfg: Config, code: str, file: str, line: int) -> Suppression | None:
    return next(
        (s for s in cfg.suppress if s.code == code and fnmatch.fnmatchcase(file, s.file) and s.line in (None, line)),
        None,
    )
