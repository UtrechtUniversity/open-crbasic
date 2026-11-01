#!/usr/bin/env python3
"""Hashes that pin a release (M7, `docs/release-1.0.md`): the reference compilers, every corpus and
the gate baselines, each as one SHA-256 over its sorted (relative path, bytes) pairs.

    uv run python tools/release_manifest.py
"""

import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT
sys.path.insert(0, str(ROOT / "src"))
import opencrbasic  # noqa: E402
from opencrbasic import analysis, checks, profiles  # noqa: E402

CORPORA = {
    "crbasic (repository programs)": (REPO / "tests" / "corpus" / "firnmelt", ("*.CR6", "*.crb", "*.csv")),
    "CRBasic help examples": (ROOT / "tests" / "corpus" / "chm", ("*.CR6",)),
    "GEUS programs": (REPO / "examples" / "Logger Programs", ("**/*",)),
    "fuzz replay reproducers": (ROOT / "tests" / "corpus" / "fuzz", ("**/*",)),
    "fixtures": (ROOT / "tests" / "fixtures", ("**/*",)),
}
FILES = [
    "tests/replay_baseline.json",
    "tests/artifact_baseline.json",
    "docs/inventory-cr6.json",
    "src/opencrbasic/data/notices_cr6.json",
    "uv.lock",
    "vscode/package-lock.json",
]


def tree_hash(base: Path, patterns: tuple[str, ...]) -> tuple[str, int]:
    files = sorted({p for pattern in patterns for p in base.glob(pattern) if p.is_file()})
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(base).as_posix().encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest(), len(files)


def main() -> None:
    out = {
        "version": opencrbasic.__version__,
        "analysis_api_version": analysis.API_VERSION,
        "report_schema_version": checks.SCHEMA_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "compilers": {
            name: {
                "exe": p.exe_name,
                "version": p.version,
                "sha256": hashlib.sha256((REPO / "compiler" / p.exe_name).read_bytes()).hexdigest(),
            }
            for name, p in profiles.PROFILES.items()
        },
        "corpora": {
            name: dict(zip(("sha256", "files"), tree_hash(base, patterns)))
            for name, (base, patterns) in CORPORA.items()
        },
        "files": {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in FILES},
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
