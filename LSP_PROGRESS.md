# CR6 language server progress

Live implementation state and session handoff. Design and acceptance criteria:
[LSP_PLAN.md](LSP_PLAN.md). Compiler state and worker policy: [PROGRESS.md](PROGRESS.md).

## Current state (2026-10-16, first release ready to merge)

Human decisions 2026-10-16: help stays CR6-only; the first release is accepted on the macOS
validation alone (T-19, the Windows/WSL re-check after LSP-R6, is not a release gate).

- Implemented and integrated: LSP-00..09 (analysis facade and source providers, positions, catalog
  and generated grammar, help provider, binding index; stdio server with diagnostics, completion,
  hover, signature help, outline, folding, links, definition/references/highlights, semantic
  tokens, rename, formatting, quick fixes, inlay hints, "Show Compiler Output" and "Show Table
  Fields"; workspace watching, folders and limits; VS Code client and packaged VSIX; user guide,
  benchmarks, install check, test tiers, Pyrefly gate) and review/host fix rounds R1-R7.
- Reviews: rounds 1 (`39286384`), 2 (`f3069825`), 3 (`ccba3666`, astra and Fable), 4
  (`743da2ac`) and 5 (`de6affff`, R5/R6, no findings). Every finding
  is fixed; the last open ones (round-3 F2, F12, F15) in LSP-R7a: `Alias` junk tokens counted in
  the ordinals, `BeginBurstTrigger` pair, `TO` dropped from read-position quick fixes.
- Host: macOS T-01..T-17 and H-01..H-10 accepted (last record `e18cdf52`); T-18 passed on Windows
  and Remote-WSL (`73479946`, `f76901b2`). The raw host records under `docs/lsp-rounds/host-tests/`
  were removed in the pre-merge cleanup; their summaries stay in [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md).

## Remaining work (after the first release)

1. T-19 (Windows/WSL re-checks after LSP-R6), if Windows/Linux are to be claimed.
2. Help for CR1000X/CR300/CR350 needs their own extracted help files and a per-logger
   `helpDirectory`; not planned.
3. Known limits kept by design (documented in the user guide): whole-line diagnostic ranges, rename
   does not detect a file that appears after analysis, formatting refuses programs with errors,
   completion capped at 200, CR6-only help, references skip entries whose analysis is pending,
   Windows/Linux not claimed.
4. `scratch/lsp/selfdiff-base.json` is local to a checkout (untracked); recreate it at the round's
   base SHA before running selfdiff ("Resume here").
5. Compiler items found by this work are recorded in PROGRESS.md (not executed here).

## Human decisions (2026-10-11)

- Help parsing (display only; the instruction table stays authoritative): a bare `[X]` around one
  parameter in a call's parameter list marks it optional (`Name [optional]` already did); brackets
  outside call parameter lists are not parameters; a trailing `...` is one repeatable parameter
  (`MenuPick ( Item1, Item2, Item3... )`); quotes on quoted names are kept. Implemented in
  LSP-04a-rules.
- 2026-10-12: Windows and Linux cannot be tested in this work session; the release claim is
  macOS only. Windows/Linux stay untested and unclaimed (user guide sections 1 and 8, client README).
- 2026-10-16: help stays CR6-only for the first release (other loggers need their own extracted
  help; not available). The first release is accepted on the macOS validation alone; T-19
  (Windows/WSL re-checks after LSP-R6) is not a release gate. Remaining work before merging: one
  gpt-6.1-sol review round of the unreviewed LSP commits, its findings and the open earlier review
  items fixed, then a repository cleanup.

## Resume here

1. Read LSP_PLAN.md and this file.
2. If `scratch/lsp/selfdiff-base.json` is missing, recreate it at the round's base SHA.
