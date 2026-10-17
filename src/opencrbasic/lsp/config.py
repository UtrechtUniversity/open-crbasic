"""Server configuration (`opencrbasic` section / initializationOptions) and entry discovery."""

import codecs
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from .. import profiles


@dataclass(frozen=True)
class Config:
    entries: tuple[str, ...] = ("**/*.CR6",)
    exclude: tuple[str, ...] = ()
    logger: str = "cr6"
    encoding: str = "utf-8"
    debounce_ms: int = 300
    analysis_timeout_ms: int = 10000
    help_directory: str = ""            # "" : no help text
    feature_wait_ms: int = 1500         # bounded wait for a fresh analysis (docs/lsp-api.md)
    max_file_bytes: int = 2 * 1024 * 1024   # larger files are not analysed (docs/lsp-api.md "Bounds")
    max_queued_jobs: int = 64           # entries with an analysis task at once; the rest wait
    max_cache_mb: int = 256             # parsed programs kept for the language features
    inlay_parameter_names: bool = True  # `inlayHints.parameterNames`

    def analysis_key(self) -> tuple[str, str, int]:
        """What changes every entry's analysis input or outcome."""
        return (self.logger, self.encoding, self.max_file_bytes)

    def entry_suffixes(self) -> frozenset[str]:
        """Extensions (lower case) that make an opened file an entry program."""
        return frozenset({".cr6"} | {ext for ext, name in profiles.EXTENSIONS.items()
                                     if name == self.logger})


def _str_list(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


def _int(value: Any, minimum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value if value >= minimum else None


def parse_config(options: Any, base: Config | None = None) -> tuple[Config, list[str]]:
    """`base` overlaid with the recognised, valid keys of `options`. Returns the config and one
    warning per rejected value (the base value is kept)."""
    cfg = base or Config()
    warnings: list[str] = []
    if not isinstance(options, Mapping):
        return cfg, warnings
    changes: dict[str, Any] = {}
    for key, attr in (("entries", "entries"), ("exclude", "exclude")):
        if key in options:
            value = _str_list(options[key])
            if value is None:
                warnings.append(f"ignoring {key!r}: expected a list of strings")
            else:
                changes[attr] = value
    if "logger" in options:
        value = options["logger"]
        if isinstance(value, str) and value in profiles.PROFILES:
            changes["logger"] = value
        else:
            warnings.append(f"ignoring 'logger': expected one of {', '.join(profiles.PROFILES)}")
    if "encoding" in options:
        value = options["encoding"]
        try:
            if not isinstance(value, str):
                raise LookupError(value)
            codecs.lookup(value)
            changes["encoding"] = value
        except LookupError:
            warnings.append(f"ignoring 'encoding': unknown encoding {value!r}")
    for key, attr, minimum in (("debounceMs", "debounce_ms", 0), ("analysisTimeoutMs",
                                                                  "analysis_timeout_ms", 1),
                                    ("featureWaitMs", "feature_wait_ms", 0),
                                    ("maxFileBytes", "max_file_bytes", 1),
                                    ("maxQueuedJobs", "max_queued_jobs", 1),
                                    ("maxCacheMb", "max_cache_mb", 1)):
        if key in options:
            value = _int(options[key], minimum)
            if value is None:
                warnings.append(f"ignoring {key!r}: expected an integer >= {minimum}")
            else:
                changes[attr] = value
    nested = options.get("inlayHints")
    flat = "inlayHints.parameterNames"
    if isinstance(nested, Mapping) and "parameterNames" in nested:
        present, value = True, nested["parameterNames"]
    else:
        present, value = flat in options, options.get(flat)
    if present:
        if isinstance(value, bool):
            changes["inlay_parameter_names"] = value
        else:
            warnings.append("ignoring 'inlayHints.parameterNames': expected a boolean")
    if "helpDirectory" in options:
        value = options["helpDirectory"]
        if value is None or isinstance(value, str):
            changes["help_directory"] = (value or "").strip()
        else:
            warnings.append("ignoring 'helpDirectory': expected a string")
    return (Config(**{**cfg.__dict__, **changes}) if changes else cfg), warnings


def glob_regex(pattern: str) -> re.Pattern[str]:
    """Case-insensitive regex for a workspace-relative glob: `*`, `?`, `**`, `**/`, `{a,b}`."""
    pat = pattern.replace("\\", "/")
    while pat.startswith("./"):
        pat = pat[2:]
    out: list[str] = []
    i = 0
    depth = 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            out.append("(?:")
            depth += 1
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    out.append(")" * depth)
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


_SKIP_DIRS = {"node_modules", "__pycache__"}


def discover(folders: list[Path], entries: tuple[str, ...], exclude: tuple[str, ...]) -> list[Path]:
    """Files under `folders` matching an `entries` glob and no `exclude` glob (workspace-relative,
    case-insensitive). Directories starting with `.` and `node_modules` are not walked."""
    include = [glob_regex(p) for p in entries]
    skip = [glob_regex(p) for p in exclude]
    found: dict[Path, None] = {}
    for folder in folders:
        folder = Path(folder).absolute()
        for root, dirs, files in os.walk(folder):
            rel_root = Path(root).relative_to(folder).as_posix()
            prefix = "" if rel_root == "." else rel_root + "/"
            dirs[:] = sorted(d for d in dirs
                             if not d.startswith(".") and d not in _SKIP_DIRS
                             and not any(rx.fullmatch(f"{prefix}{d}/_") for rx in skip))
            for name in sorted(files):
                rel = prefix + name
                if any(rx.fullmatch(rel) for rx in include) and \
                        not any(rx.fullmatch(rel) for rx in skip):
                    found[Path(root, name).absolute()] = None
    return sorted(found)


def watch_pattern() -> str:
    """The `workspace/didChangeWatchedFiles` glob: library files and the program extensions of
    every logger profile, each in lower and upper case (LSP globs are case-sensitive; the server
    filters by the configured logger). A mixed-case extension such as `.Cr6` is not watched."""
    suffixes = {".crb", ".cr6", *profiles.EXTENSIONS}
    names = sorted({v for s in suffixes for v in (s.lower()[1:], s.upper()[1:])})
    return "**/*.{" + ",".join(names) + "}"
