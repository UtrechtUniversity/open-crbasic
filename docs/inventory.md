# CR6 evidence inventory and coverage notices (M4)

`tools/inventory.py` generates two files from the Ghidra decompile of `CR6Comp.exe`
(`scratch/re/CR6Comp.decomp.c`, local only), the opencrbasic sources and the repository's saved
observations:

- [`inventory-cr6.json`](inventory-cr6.json): per CR6 instruction, its evidence;
- `src/opencrbasic/data/notices_cr6.json`: the coverage-notice table the checker ships.

Both pin the SHA-256 of `CR6Comp.exe`, of the decompile and of `data/instructions.json`;
`tests/test_inventory.py` fails when the binary or the instruction table no longer matches. It is
an inventory, not a coverage percentage ([PLAN](../PLAN.md#v10-milestones), M4).

```bash
uv run python tools/inventory.py
```

Rerun it after changing a checker message, a fixture or the replay corpus: the notice table is
derived from them.

## Per-instruction record

| Field | Meaning |
|---|---|
| `builder` | the decompiled function `compile_fn` reaches through its incremental-link `jmp` thunks (as `tools/builder_map.py`); `null` when no function starts there (`CDM_PeriodAvg`, `ExciteV`, `CWB100`) |
| `messages` | each distinct message a reporter call in the builder (`via` null) or in one of its helpers (`via` names it) passes, with the decompile `line` of its first call |
| `helpers` | functions reachable from the builder without passing through shared infrastructure, each with its first call site (`called_from`, `line`) |
| `shared_helpers` | shared infrastructure the builder reaches; listed, not expanded |
| `unresolved_calls` | indirect calls (`(*(code *)...)`) in the builder and its helpers, by function and line: their targets are not followed |
| `python` | `module:line` sites that name the instruction in a string literal (count, first 20) |
| `observations` | saved CR6 reference outputs (fixture `file#case`, one per case; replay reproducer path) of programs whose code, sibling include files included, names the instruction outside strings and comments (count, first 20) |
| `state` | below |

**Reporters** are the functions that pass their first parameter to the message formatter
`FUN_0050aef0` (`docs/binary.md`), and their thunks: `FUN_005089c0` and `FUN_0049ce10` (errors),
`FUN_00508a00` (warning); the list is in the output. A string symbol is read whole, commas included
(`s_When_channel_<_0,_measurements_p_0065c788`). A reporter call whose argument is no string symbol
(a variable, or no argument in the decompile) is a message with `text` null and the argument in
`unresolved`: it is kept as uncertainty, never dropped.

A helper is **shared** when the builders of 15 or more instructions reach it (one pass over the
full call graph); traversal stops there, and the top-level `shared_helpers` object lists each
shared helper's own messages.

## States

For one message:

- **implemented** (`implemented` names the evidence): an opencrbasic string can produce it, that is,
  a Python literal or f-string (placeholders as wildcards, at least 10 literal characters) or a rule
  data string matches the whole format; or every literal piece of 10 or more characters occurs in
  the sources (a message assembled from pieces); or else opencrbasic prints it on a saved program
  where the reference printed it (up to three such programs compiled with their sibling files; a
  case whose files are not a plain name-to-text map is not compiled);
- **observed** (`observed` lists up to three): the format matches a message of a saved CR6 reference
  output (without its `line N:` location and `Warning:` prefix) for a program that uses the
  instruction.

`tested` = implemented and observed; `partially tested` = one of the two; `unknown` = neither, or
an unresolved message. An instruction is `unknown` without a builder or without any observation,
`tested` when it has at least one message and every message is tested, and `partially tested`
otherwise (also with no extracted message: its rules may all sit in shared helpers).

Limits:
- Matching is by message text, not by rule. A message opencrbasic emits for one instruction or
  trigger counts as implemented for every instruction that shares it (`Unrecognized port!`), and an
  implemented message can still miss a trigger. Both make notices fewer, not wrong.
- A message opencrbasic builds from pieces shorter than 10 characters matches neither a template
  nor the fragment rule; if no saved observation shows it either, it is noticed although modelled. `tests/test_inventory.py` keeps the
  templated cases found so far as controls.
- Messages passed in variables, indirect calls and shared helpers are not expanded per instruction.
- An observation counts for every instruction its code names; a name used as a variable counts too.

## Coverage notices

A notice names, at the first statement that uses an instruction, the resolved messages of its
builder and own helpers that are not implemented: reference rules opencrbasic does not model by the
evidence above. `checks.notices` computes them for the CR6 profile only (the inventory is CR6) when
the reference accepts the program; they are information, never a verdict. `crbc --report` writes
them as `notices: [{file, line, instruction, rules}]` (report schema 2); default stdout, `--strict`
stderr and the exit status are unchanged. The language server does not show them yet (M6).

## Coverage of the listed false accepts

Of the 25 replay false accepts outside the relevant set (PROGRESS, "Next steps", M1), 17 are CR6
reproducers. For 9 of them the reference's first error is a rule their notice names
(`tests/test_inventory.py`): `7b4b2177d56b`, `4156d89a014d`, `72f95eac103f`, `77636b10567c`,
`7f9efa8af42c`, `c060e9f1402f`, `d12785e05faf`, `52138d7bd179`, `f61f825392f2`. The other eight get
no notice for their rule and stay listed by identity: the message is implemented for another
trigger (`6ac24bd775f1` `Unrecognized port!`, `ecc509dd06de` `Invalid file type option.`,
`022d98dd3efc` `%s must be declared as a Const.`, `9c6367a2c598`, `0e23c61229f4`, `577a59ecb08c`),
comes from a shared helper (`f2f6e5d8b8e6`, `Too many parameters`), or has no format string in the
inventory (`778ea45e64b7`, `Settling time: ... out of range`).
