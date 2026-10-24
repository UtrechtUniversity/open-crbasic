# CR6 language server implementation plan

This request authorizes planning. No server, extension, or new compiler API has been implemented
by this document. The implementation sequence below is proposed; start it when implementation is
requested. Compiler parity remains a separate workstream under [PLAN.md](PLAN.md).
Since 2026-10-24 the compiler's v1.0 roadmap adds opencrbasic checks and coverage notices beside the
reference diagnostics; carrying those channels through the server is milestone M6 in
[PLAN.md](PLAN.md#three-diagnostic-channels).

Goal: a VS Code extension backed by a Python language server that imports opencrbasic, so fixes
to its parser, profiles, instruction rules, and checker also improve editor behaviour. Deliver
useful features in bounded rounds without sacrificing the existing compiler contract.

This file owns the language-server design, round sequence, and acceptance criteria. Live work,
measured results, and the next session's starting point belong in
[LSP_PROGRESS.md](LSP_PROGRESS.md). Compiler state and compiler-worker policy remain in
[PROGRESS.md](PROGRESS.md); do not duplicate its counts or backlog here. Station requirements
remain in [../architecture.md](../architecture.md).

## Scope and boundaries

- Initial editor target: CR6 programs and their `.crb` includes. Preserve existing compiler support
  for all four logger profiles when changing shared code. Additional editor profiles are later
  work, not an implicit expansion of the repository's CR6 station scope.
- Core release: highlighting, completion, instruction/variable hover, signature help, compiler
  diagnostics, definition navigation, references, outline, folding, and include links.
- Later release: safe rename, richer semantic highlighting, limited formatting and quick fixes.
  Advertise only capabilities with passing acceptance tests.
- Local analysis only: no flashing, serial access, airtime, or runtime simulation. Compiler
  agreement establishes static behaviour, not logger execution or sensor correctness.
- Do not implement another CRBasic parser or checker in TypeScript. A TextMate grammar may
  describe lexical colouring, but it must not become an independent semantic authority.
- Do not require complete compiler parity before a useful editor release. Keep known compiler
  limitations visible and keep their fixes in the compiler workstream. This plan does not change
  the current scheduling priority in PLAN.md or authorize implementation ahead of that priority.
- Start with a configured Python interpreter and an installable server extra. A self-contained
  runtime, other editors, remote hosts, and multi-profile UX require later acceptance rounds.

## Shared Python architecture

```text
VS Code extension (TypeScript)
  language registration, TextMate grammar, configuration, Python process launch
                  | LSP over stdio
Python server adapter (opencrbasic.lsp)
  document versions, requests, scheduling, LSP position/URI conversion
                  | public Python analysis API
opencrbasic analysis facade
  source snapshots -> source loader/preprocessor -> parser -> checker
                  |                           |
  source maps, dependencies            syntax, profiles, diagnostics
                  |                           |
  shared symbol binding/index and instruction catalog
                  |
  immutable analysis result consumed by CLI and server
```

Proposed package layout, created only in the applicable rounds:

- `src/opencrbasic/analysis.py`: supported facade and analysis request/result contracts.
- `src/opencrbasic/positions.py`, `bindings.py`, `catalog.py`: shared source mapping, symbol
  resolution, and instruction metadata, if these warrant separate modules.
- `src/opencrbasic/lsp/`: protocol and editor scheduling; no language rules.
- `tests/lsp/`: request transcripts, workspace fixtures, integration and edit-sequence tests.
- `vscode/`: TypeScript client, grammar, language configuration, extension tests and packaging.
- `docs/lsp-api.md`: implemented API contracts and extension setup, added as code becomes real.
- `docs/lsp-rounds/`: durable round briefs, worker handoffs, reviews, and gate summaries.

Use the existing opencrbasic uv project for package code and tests, with environments outside
the repository. Keep compiler installation dependency-free; server protocol dependencies go in
an optional extra, pinned through uv. Choose a maintained LSP library in round LSP-00 instead of
writing the JSON-RPC transport. Keep repository orchestration scripts under `../scripts/` and
its existing uv project. The TypeScript client has its own locked Node dependencies.

### Analysis facade and compatibility

Introduce a small additive API rather than importing private checker passes into request
handlers. Proposed contracts, to be finalized and documented in LSP-00/01:

- `SourceSnapshot`: canonical document identities, original text, versions/content hashes,
  disk fallback, and unsaved overlays for every included file.
- `AnalysisRequest`: entry program, explicit logger profile, snapshot, and existing compiler
  options. Include context and constant/preprocessor configuration form part of its identity.
- `AnalysisResult`: syntax tree, structured diagnostics, implementation failures, dependencies,
  source maps, binding index, and relevant existing compiler outputs. Record incomplete or
  unresolved analysis explicitly. Do not expose mutable checker internals as a stable API.
- `InstructionCatalog`: instruction/function names and parameter-kind metadata from the same
  `profiles.py` data used by the compiler, plus optional human-readable documentation.

Extract the current driver incrementally. The same driver must retain pass ordering, diagnostic
suppression, profile-specific reparsing, and compiler options; calling `parse_text()` followed by
`checker.check()` alone is not an equivalent replacement for `compile_file()`. Characterize
these differences before extracting code. Make CLI/disk compilation and editor/snapshot
analysis invoke the shared implementation. Keep signature and artifact work optional for editor
requests; preserve the existing CLI path and its outputs. Editor analysis must not write message
files, tables, or temporary source into the workspace.

Preserve original compiler diagnostic text, ordering and exit semantics. Keep rendered CLI
message-buffer limits separate from the structured collection available to the editor. Expose
implementation exceptions as analysis failures, not misleading CRBasic syntax errors. Tests
continue to fail on implementation exceptions. Incremental work must not silently remove
checker passes or invent alternate instruction validation.

Document the facade's compatibility version independently from package release versions.
In development, import the active checkout explicitly and report its path/revision at server
startup to stderr. A worktree test must never accidentally import the primary checkout through
an editable install. Release extension and Python package versions as a tested pair; fail clearly
on an incompatible API instead of using copied fallbacks.

### Source positions and include context

Keep two notions of location: compiler-reported lines for compatibility, and original physical
file ranges for editor operations. Existing token columns refer to transformed logical text;
most AST nodes currently lack complete ranges. Adding editor spans must not alter compiler
line numbering or require a broad AST rewrite.

Map original characters through comment removal, continuations, declaration initializer pulls,
colon statements, includes, and preprocessing. Retain occurrence identities for a file included
multiple times under different sections. Preserve mappings for inactive source even when it
does not contribute to the checked program. Canonicalize URIs separately from case-insensitive
include-name matching; do not lowercase all filesystem paths.

Convert positions at the protocol boundary using the negotiated LSP encoding, with UTF-16 as
the supported fallback. Test non-ASCII comments/strings and CRLF as well as LF. Preserve the
compiler's disk encoding behaviour; do not silently change Latin-1 decoding to UTF-8. Define how
editor Unicode text is represented and how unsupported characters are diagnosed.

Whole-line diagnostic ranges are acceptable initially. Use exact ranges only when mapping is
proven; never manufacture precise-looking positions. For diagnostics with no line or file,
use a documented entry-program anchor or related location. Clear obsolete included-file
diagnostics when dependencies or conditions change.

### Symbol resolution

Build a shared binding index from the canonical parsed program and profile. Use existing scope
fixtures as evidence; extend them through focused oracle probes where behaviour is unresolved.
The current global declared-name collection is not sufficient for safe navigation and rename.

Index declarations and references for Public/Dim/Static, constants, parameters, Sub/Function,
aliases, labels, tables, and supported structure members. Distinguish declarations that share
spelling by scope and include occurrence. Use case-insensitive symbol matching while retaining
source spelling. Never resolve a name by string search alone.

Add the index without changing compiler acceptance rules initially. Migrate a checker to shared
bindings only after oracle evidence and differential gates establish equivalent behaviour.
Unresolved or ambiguous bindings stay explicit: navigation may offer multiple proven targets;
rename must refuse ambiguous edits. Inactive branches and textual names inside strings require
defined policies, not speculative binding.

### Editing and workspace model

Configure entry programs rather than checking every `.crb` as a standalone program. The
repository's Main.CR6 and testers are useful configured entries; discovering a `.crb` alone must
not create spurious missing-BeginProg errors. Show the selected entry context for shared files.
When several entries use a library, maintain separate analysis results and identify their context
in diagnostics; rename considers all configured affected entries.

Start with full document synchronization and debounced whole-program analysis. Unsaved include
overlays take precedence over disk. Invalidate affected entries after text, include, profile,
configuration, or disk-dependency changes; closing a document restores disk content. Cache by
the whole request identity and all transitive dependency contents, including missing include
paths that might later appear, not just the entry file's timestamp.

Analyze immutable snapshots away from the protocol event loop. Prefer bounded process workers
initially for timeout/restart isolation; prototype and measure this in LSP-01. Cancel or discard
superseded work, and publish only results whose complete snapshot is still current. Limit queued
jobs, file sizes, include depth/cycles, and cache memory with clear failure reporting. Requests,
cancellation, shutdown, and subsequent edits must remain usable after malformed source or a
checker failure. Limits must not change compiler semantics or be reported as language errors.

Recover editor features from incomplete syntax using shared recovery nodes, token spans, and
partial argument lists. A lightweight token query can locate the current argument or prefix;
it must not implement independent expression semantics. Do not label stale semantic results
current. Begin with correctness and full reparsing; introduce incremental parsing only after
measured latency shows a need.

### Help and generated metadata

Read compiler names, symbols, parameter kinds, and supported profiles directly through the
catalog. Generate any packaged grammar name lists from that catalog and check regeneration in
CI. A compiler metadata improvement should need no manual edit to a second instruction list.

Parameter type codes alone do not supply friendly parameter names, complete signatures,
optionality, or prose. Build a separate documentation provider indexed by catalog identity.
It may read the local `extracted_chm/` and may copy its text (internal use only, human decision in
[docs/legal.md](docs/legal.md)).
Test the importer with permitted minimal fixtures and record provenance. Missing help falls
back to the metadata actually known. Help-only information does not override checker rules.
No public packaging is planned ([docs/legal.md](docs/legal.md)). Documentation coverage and compiler-rule coverage are separate measurements.

## Implementation rounds

Use IDs `LSP-00`, `LSP-01`, etc. They do not reuse compiler round numbers. A round is a bounded
acceptance milestone; split it into named chunks if it cannot fit one session. Each chunk owns
one behaviour family or contract change and includes its focused tests. Later rounds may be
split or reordered in LSP_PROGRESS.md when dependencies and evidence justify it.

| Round | Deliverable | Exit criterion |
|---|---|---|
| LSP-00 | Baseline and contracts | Record checkout/import identity, existing gates and failures; inventory driver semantics and scope evidence; select transport library; specify snapshot/facade contracts, configured entry format, test matrix and package boundaries. No compiler behaviour change. |
| LSP-01 | Shared analysis and minimal server | CLI and snapshot APIs use the same pipeline; disk vs equivalent snapshot results agree; unsaved entry/include diagnostics work; tested initialize/open/change/save/close/shutdown, version rejection and worker failure recovery. Client can launch locally. |
| LSP-02 | Physical source mapping | Verified ranges through continuations, colon segments, pulled initializers, includes and conditional branches; CRLF and Unicode position tests; compiler-rendered locations remain unchanged. |
| LSP-03 | Basic editing features | Grammar, language configuration, catalog completion, include links, outline and folding work on repository programs and incomplete edits; generated names stay synchronized with profiles. |
| LSP-04 | Hover and signature help | Instruction help provider, friendly parameter signatures, nested-call/current-argument selection, constants/types/units hover; missing documentation fallback and provenance checks. |
| LSP-05 | Binding and navigation | Evidence-backed scope/alias/table binding, definition, references, occurrence highlighting and semantic tokens; cross-include and same-name-local tests; ambiguity remains explicit. |
| LSP-06 | Workspace correctness and resilience | Multiple configured entries, conditional IncludeSection contexts, transitive invalidation, missing/created/renamed/deleted includes, stale result suppression, bounded jobs, timeouts and edit-sequence replay all pass. Earlier rounds already implement basic invalidation. |
| LSP-07 | Safe edits | Rename limited to proven bindings across affected entries, collision detection and versioned edits; refuse unsupported cases. Optional indentation/quick-fix families each have semantic-preservation tests. |
| LSP-08 | Release validation | Tested install and packaged VSIX/server pair, dependency and help provenance, host matrix, startup/latency/memory evidence, documented setup and limitations. Private release is distinct from public redistribution. |

Navigation depends on LSP-02 and LSP-05. Rename depends additionally on LSP-06. LSP-03/04 can
share a round only when they have disjoint ownership and the facade/catalog contracts are stable.
Do not schedule parallel source-loader, parser, or driver rewrites against the same base.

## Verification and compiler improvements

Keep three kinds of evidence separate:

1. **Compiler behaviour:** existing oracle fixtures, differential corpus/replay and artifact
   gates. Compiler progress and interpretation stay in their current homes.
2. **Shared API behaviour:** disk/snapshot equivalence, exact source mapping, bindings and
   dependency identity. These tests belong to the opencrbasic suite.
3. **Editor behaviour:** real stdio request transcripts and VS Code integration tests, including
   edit sequences and observable UI results. A passing parser test does not establish LSP UX.

After each edit, run the narrowest relevant tests. At each integrated round, run the full
opencrbasic pytest suite and the available server/client suites. For any shared loader/parser/
checker/profile/driver change, also run the established four-profile differential/replay gates,
audit new oracle fixtures, and run artifact comparisons when their outputs are affected. Compare
against the recorded base revision; require no new false rejects, false accepts, implementation
failures, hangs, or unexplained decreases in agreement. Known oracle faults remain separate from
language verdicts; a nonzero existing gate needs interpretation, never an automatic pass label.

Finish repository work with `cd scripts && uv run verify.py` as required by AGENTS.md. Record the
observed BUILD line. The opencrbasic-native alternate gate is additional evidence, not a
replacement for the reference compiler. Do not claim an unavailable gate passed.

Every compiler change affecting an editor feature adds or exercises a bridge regression:

| Compiler improvement | Expected server benefit | Bridge assertion |
|---|---|---|
| Parser/recovery fix | More accurate structure, navigation, hints during edits | The same source reaches the fixed AST through the facade and a real server request. |
| Instruction/profile rule fix | Improved diagnostics and parameter suggestions | Server diagnostics match structured compiler findings; catalog resolves the updated metadata. |
| Source/include fix | Correct cross-file ranges and dependency invalidation | Unsaved include edit updates the right entry and physical location. |
| Binding/type improvement | Better definitions, references and variable hover | Requests resolve the intended declaration/type without scope collisions. |

Do not copy complete diagnostic snapshots into every test. Compare structured results from the
same revision for adapter parity; retain oracle-backed fixtures separately to catch shared
regressions. Include at least one end-to-end regression proving a change in shared Python
behaviour is observable through the server, without patching protocol handlers.

The initial editor matrix covers clean and invalid programs, unfinished calls/strings/blocks,
arrays, same-name parameters, aliases, table/structure members, colon statements, continuation
lines, nested calls, conditional sections, unsaved libraries, missing includes, Unicode, CRLF,
file lifecycle events, server restart and cancellation. Use repo programs and minimized fixtures;
exclude historical `examples/`. Existing internal help/fuzz corpora may be used for local stress
testing but are not automatically permitted release assets.

Measure cold start, warm hover/completion, end-to-end diagnostics p50/p95, repeated-edit memory,
and a reproducible large-program stress case. Proposed local goals are warm hover/completion
p95 below 100 ms and fresh diagnostics below 500 ms after debounce on normal repo programs;
these are targets, not observations. Record host, fixture, revision and measurement procedure.
Correctness, bounded resource use and responsiveness are required even if targets need revision.

For LSP-08, verify a clean install on macOS, Windows and Linux, or explicitly narrow the release
claim to tested hosts. No Wine or reference compiler is needed by the installed language server;
they remain development oracle dependencies. Protocol and feature implementation follows the
[LSP 3.17 specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)
and [VS Code language extension API](https://code.visualstudio.com/api/language-extensions/programmatic-language-features).

## Round workflow and session handoff

Follow the established coordinator/worker/reviewer roles in PROGRESS.md "Work method" for shared
compiler implementation. Do not silently replace its designated worker models or tools when
unavailable. Record the unavailable role/tool and perform only work authorized for the current
agent. Use the same pattern for LSP chunks; do not infer new free-commit permission from this plan.
No parallel workers are necessary to write the plan itself.

### Start a round

1. Read AGENTS.md, this plan, LSP_PROGRESS.md, compiler PROGRESS.md and relevant owned code/docs.
   Reconcile recorded base revisions and local changes before touching files.
2. The coordinator writes `docs/lsp-rounds/LSP-NN.md`: objective, chunk IDs, dependencies,
   acceptance criteria, exact file ownership, base SHA, environment/import paths, gate commands,
   known baseline failures, integration order, and checkpoints. Write this before dispatch.
3. Create isolated worker worktrees on named branches, normally `codex/lsp-nn-<chunk>` unless
   the human specifies another convention. Resolve and record the actual integration branch;
   do not hard-code the compiler's present branch. One chunk per worker; maximum four workers
   including any limits imposed by the coordinating environment. Prefer one or two until
   contracts settle. Do not run concurrent mutating uv operations on a shared environment.
4. Coordinator owns LSP_PROGRESS.md and the round summary. Workers own disjoint implementation
   files and their own `LSP-NN-<chunk>-handoff.md`. Conflicts in shared compiler files are serialized.

### Implement and integrate

1. Reproduce the issue or encode the feature's acceptance case first. For semantic language
   uncertainty, minimize and probe the real compiler using the existing compiler workflow.
2. Make one small change; focused checks immediately. Save each regression case before moving
   to the next family. Report observations separately from expectations and inference.
3. After a milestone, update the worker handoff on disk with paths/revision, changed behaviour,
   commands and exit codes, saved evidence, remaining gaps and the exact next action.
4. When commits are authorized, make one verified commit per chunk using explicit file staging.
   Never use `git add -A`, stash, amend integrated commits, or destructive cleanup.
5. The coordinator independently verifies worker output, integrates by fast-forward, rebase or
   cherry-pick without merge commits, then runs the applicable round gates on the combined tree.
   Do not mark a chunk integrated because a worker merely reported success.
6. Perform a fresh read-only review after the batch using the existing review policy. Save it
   under `docs/lsp-rounds/`, triage each finding, reproduce actionable defects and fix them in a
   bounded follow-up chunk. Do not declare a release round accepted with unresolved correctness
   findings. Record deferred lower-priority findings with reasons.
7. Update the live ledger, accepted capabilities, observed gate results and next actions.
   Retain worker branches/worktrees until their work and evidence are safely integrated or saved.

### Usage limits and interruption safety

Chat history and ignored scratch files are not the sole record. Round briefs, handoffs and live
state are durable files. Review outputs and small reproductions needed to resume belong in
`docs/lsp-rounds/` or tests, not only `scratch/`. Large/private local evidence may remain ignored,
but the handoff records its exact path, content hash, retention requirements and a reproduction
command. Never include credentials, vendor binaries or restricted help in tracked handoffs.

- Check available usage at round start, before dispatch, after each chunk and before broad gates.
  Record the tool and window used; account windows may differ between providers. If unavailable,
  record that fact and use one small active chunk with milestone checkpoints.
- At about 70% consumed, dispatch no new chunks; finish or checkpoint active work. At about 80%,
  request worker handoffs and reserve the remaining budget for verification and preservation.
  By about 85%, stop implementation and leave an explicit resume point, matching the existing
  compiler wrap-up threshold. Stop earlier if estimated gate time exceeds the reserve.
- Checkpoint after every completed behaviour family and before a long oracle/test run. Long
  runs write results to durable files, with PID/session identifier and completion marker where
  available. A started command is not a completed or passing check.
- With commit authorization, preserve interrupted edits in a clearly marked WIP commit on the
  worker branch. Without it, retain the worktree and source, save a binary-capable tracked-file
  patch plus copies/inventory of untracked files under a durable local checkpoint directory,
  and verify that the checkpoint is readable. Do not rely on a patch to capture untracked files.
- Record base SHA, WIP SHA or patch path/hash, worktree path, untracked-file inventory, last
  passing gate, pending commands, worker state and the next concrete step in LSP_PROGRESS.md
  before ending. Preserve needed ignored files separately before any worktree archive/removal.
- On an abrupt cutoff, the next coordinator inventories branches, worktrees, diffs, untracked
  files and saved logs first. Preserve recovered work before editing. Treat partial commits and
  partial test logs as unverified; rerun only incomplete or invalidated checks.

### Resume in a new session

Read LSP_PROGRESS.md first, then the active round and worker handoffs. Confirm revision/import
identity and inspect actual worktree state. Resolve pending process completion, review findings
and unverified work before starting a new round. Continue an interrupted chunk under its existing
ID; do not recreate or overwrite it. After compiler-only integration, rerun the affected bridge
and server tests before claiming the editor benefits are verified.

## Completion definitions

A chunk is **accepted** only after focused checks and its required evidence are saved. A round
is **accepted** only after integration, applicable combined gates and review resolution. A
capability is **released** only after real protocol and VS Code acceptance, packaging/install
validation and documentation of its supported contexts.

If usage runs out before these conditions, record **checkpointed, unverified** or **verified,
not integrated** as appropriate. Preserve the work and list what remains; never rename an
unfinished milestone complete merely to end a session.
