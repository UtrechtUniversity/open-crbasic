"""tools/lsp_gate.py: classification of changed files into gates, the pyrefly include reader."""

from __future__ import annotations

import pytest

import lsp_gate as G

PYPROJECT = '''\
[project]
name = "x"

[tool.pyrefly]
# comment with "quotes" inside
preset = "default"
search-path = ["src"]
project-includes = [
    "src/opencrbasic/lsp/**/*.py",   # trailing comment
    "src/opencrbasic/analysis.py",
    "src/opencrbasic/editing.py",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''
INCLUDES = ["src/opencrbasic/lsp/**/*.py", "src/opencrbasic/analysis.py",
            "src/opencrbasic/editing.py"]


def commands(paths, includes=INCLUDES):
    return [c.split("--no-sync ", 1)[1] for _label, c in G.classify(paths, includes)]


def test_includes_reader_fallback_and_tomllib_agree():
    assert G.pyrefly_includes(PYPROJECT, force_fallback=True) == INCLUDES
    assert G.pyrefly_includes(PYPROJECT) == INCLUDES
    assert G.pyrefly_includes("[project]\nname = 'x'\n", force_fallback=True) == []


def test_real_pyproject_is_readable_both_ways():
    text = (G.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert G.pyrefly_includes(text) == G.pyrefly_includes(text, force_fallback=True)


@pytest.mark.parametrize("path", [
    "opencrbasic/src/opencrbasic/parser.py", "opencrbasic/src/opencrbasic/checker/calls.py",
    "opencrbasic/src/opencrbasic/data/functions.json", "opencrbasic/src/opencrbasic/tables_hook.py",
    "opencrbasic/src/opencrbasic/cli.py", "opencrbasic/tests/test_lexer.py",
    "opencrbasic/src/opencrbasic/some_new_module.py"])
def test_compiler_and_unclassified_changes_run_the_full_suite(path):
    got = commands([path])
    assert got[0].startswith("pytest -q -n") and "tests/lsp" not in got[0]
    assert any(c.startswith("python tools/selfdiff.py") for c in got)


@pytest.mark.parametrize("path", [
    "opencrbasic/src/opencrbasic/analysis.py", "opencrbasic/src/opencrbasic/rename.py",
    "opencrbasic/src/opencrbasic/lsp/session.py", "opencrbasic/tests/lsp/test_server.py",
    "crbasic/Main.CR6"])
def test_lsp_only_changes_run_selfdiff_and_the_lsp_tiers(path):
    got = commands([path])
    assert got[0].startswith("python tools/selfdiff.py")
    assert got[1] == "pytest tests/lsp -q -n {jobs}"
    assert not any(c.startswith("pytest -q") for c in got)


def test_a_compiler_file_beside_lsp_files_still_means_the_full_suite():
    got = commands(["opencrbasic/src/opencrbasic/lexer.py", "opencrbasic/src/opencrbasic/hover.py"])
    assert got[0].startswith("pytest -q -n")
    assert not any("tests/lsp" in c for c in got)


def test_vscode_only_runs_npm():
    assert [c for _l, c in G.classify(["opencrbasic/vscode/src/extension.ts"])] == [
            "npm --prefix vscode test"]


def test_docs_only_runs_the_link_check():
    gates = G.classify(["opencrbasic/docs/lsp-api.md", "opencrbasic/README.md"])
    assert [c for _l, c in gates] == [
        "uv run --no-sync python tools/lsp_gate.py --check-links README.md docs/lsp-api.md"]


def test_docs_beside_code_add_no_link_gate():
    gates = G.classify(["opencrbasic/docs/lsp-api.md", "opencrbasic/src/opencrbasic/hover.py"])
    assert not any("--check-links" in c for _l, c in gates)


def test_unrelated_files_outside_the_package_select_nothing():
    assert G.classify(["scripts/collect_data.py", "STATUS.md"]) == []


@pytest.mark.parametrize("path", [
    "opencrbasic/src/opencrbasic/lsp/deep/er/x.py", "opencrbasic/src/opencrbasic/lsp/x.py",
    "opencrbasic/src/opencrbasic/analysis.py", "opencrbasic/pyproject.toml"])
def test_pyrefly_follows_the_configured_includes_and_pyproject(path):
    assert "pyrefly check" in commands([path])


def test_pyrefly_is_not_selected_for_files_outside_the_includes():
    assert "pyrefly check" not in commands(["opencrbasic/src/opencrbasic/parser.py",
                                            "opencrbasic/src/opencrbasic/hover.py"])
    assert "pyrefly check" not in commands(["opencrbasic/src/opencrbasic/lsp/x.py"], includes=[])


def test_glob_matching():
    assert G.matches("a/b/c.py", ["a/**/*.py"]) and G.matches("a/c.py", ["a/**/*.py"])
    assert not G.matches("a/b/c.py", ["a/*.py"]) and not G.matches("ab/c.py", ["a/**"])


def test_link_check(tmp_path):
    (tmp_path / "other.md").write_text("# Some Heading\n", encoding="utf-8")
    doc = tmp_path / "doc.md"
    doc.write_text("[ok](other.md) [anchor](other.md#some-heading) [web](https://x.y/z) "
                   "[self](#top)\n# Top\n[bad](missing.md) [badanchor](other.md#nope)\n",
                   encoding="utf-8")
    broken = G.check_links([str(doc)])
    assert len(broken) == 2 and "missing.md" in broken[0] and "nope" in broken[1]
