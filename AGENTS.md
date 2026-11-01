# Repository instructions

opencrbasic re-implements Campbell Scientific's CRBasic pre-compilers (CR6, CR1000X, CR300, CR350)
in Python, with a language server. It is MIT licensed and published to PyPI as `opencrbasic`.

Priorities: correctness, evidence, simplicity, consistency, minimal scope, maintainability.

## Sources of truth

| Fact | Home |
|---|---|
| Scope, channels, versions, usage | `README.md` |
| Design, milestones and remaining-work strategy | `PLAN.md` (language server: `LSP_PLAN.md`) |
| Measured state against the reference compilers | `PROGRESS.md` (language server: `LSP_PROGRESS.md`) |
| Behaviour of the reference compilers, with evidence | `docs/` |
| What may and may not be in this repository | `docs/legal.md` |
| Test corpora and how to run against local Campbell files | `tests/corpus/README.md` |

Do not copy a fact into several homes; link to its owner.

## Evidence

The reference compiler is the authority for a verdict. Prefer, in order: the human's current
instruction, an observed reference-compiler result (record the binary's SHA-256 and the date), the
CRBasic help, existing tests, general knowledge, hypothesis. Never report a compile, test or
oracle result that was not actually observed, and label inference explicitly.

## Campbell material

Never commit Campbell's compilers, help files or example programs, the GEUS programs, or test
inputs derived mostly from them (`docs/legal.md`). They live in the git-ignored `compiler/`,
`extracted_chm/`, `examples/` and `tests/corpus/chm/`. A new fixture case is a minimal program
written for the rule it tests, not an edited help example.

## Sibling repositories

The VS Code grammar and client settings live in
[open-crbasic-vsix](https://github.com/UtrechtUniversity/open-crbasic-vsix). With that repository
checked out next to this one, `tools/gen_grammar.py` regenerates its grammar and the contract tests
in `tests/lsp/test_grammar_sync.py` and `tests/lsp/test_server.py` run; without it they skip.
Change `analysis.API_VERSION` only on an incompatible change, together with the extension.

## Checks

```bash
uv sync
uv run pre-commit run --all-files     # ruff lint and format, pyrefly, file hygiene
uv run pytest -q -n auto              # full suite; -m 'not slow' for the fast tier
uv run python tools/fuzz_replay.py --recorded --baseline tests/replay_baseline.json --gate no-regressions
```

Run the narrowest useful test right after an edit (`tools/lsp_gate.py` picks the gates for a
change) and the full set before completion. CI runs the same commands without Wine; oracle-backed
tools need `compiler/` and Wine locally.

## Releases

`main` publishes to PyPI when `version` in `pyproject.toml` is new; `dev` publishes every push to
TestPyPI. `pyproject.toml` and `opencrbasic.__version__` move together. Bump only when the human
asks for a release.

## Version control

Never create a merge commit; integrate with rebase. Commit only when asked. Do not use destructive
reset or checkout commands without explicit approval.
