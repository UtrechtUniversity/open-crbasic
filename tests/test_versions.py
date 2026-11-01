"""The version policy (README "Versions"): one package version for the Python package and
`__version__`; the analysis facade and the report schema have their own integers."""

import tomllib
from pathlib import Path

import opencrbasic
from opencrbasic import analysis, checks
from opencrbasic.lsp import server

ROOT = Path(__file__).resolve().parents[1]


def test_package_and_module_versions_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert opencrbasic.__version__ == project


def test_interface_versions_are_the_released_ones():
    # bump these, and record why in README "Versions", on an incompatible change
    assert (analysis.API_VERSION, server.EXPECTED_API_VERSION, checks.SCHEMA_VERSION) == (2, 2, 2)
