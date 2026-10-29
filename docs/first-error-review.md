# First-error review (M2)

Every CR6/CR1000X saved fuzz reproducer whose reference rejects and whose first error differs from
ours (same location with another message, a missed first error, or an earlier first location), as
the PLAN M2 exit criterion asks. Input: the round-57 saved replay (128 rows). Families and the
`kind` were proposed by Codex `gpt-6.1-sol` (read-only, `scratch/r58/codex-m2.log`) and spot-checked
by hand (8 of 77 families); they are a classification, not reference observations. `kind`:
`misleading` = the reference names a different cause, `location` = another line, `wording` = same
cause, other text. `now` is the impact class in the round-58 replay (`exact` = fixed).

Still differing in round 58, relevant area: 48 misleading, 19 location, 21 wording; outside it: 28, 4, 5. Fixed since the input: 3.

| relevant | kind | family | reproducers (now) |
|---|---|---|---|
| yes | location | CallTable: DataInterval table called outside Scan | `3c4c2a011112` (we miss the reference's first error), `e22f52a890f1` (we miss the reference's first error) |
| yes | location | Battery: expression supplied as destination before later arity error | `b850f32262af` (we miss the reference's first error) |
| yes | location | Inline ResetTable: unresolved table checked in one-line If | `cda46acf1c74` (exact) |
| yes | location | FieldClassify: diagnostic attributed to preceding output owner | `d406b0207cdc` (we miss the reference's first error) |
| yes | location | Array reference: four subscripts inside ASCII condition | `494a797d22ea` (we miss the reference's first error) |
| yes | location | SetSetting: placement before BeginProg | `d108f88aa075` (we miss the reference's first error) |
| yes | location | Preprocessor: unmatched #If has an unlocated diagnostic | `f4e404561ce1` (we miss the reference's first error) |
| yes | location | Oversized declaration: saved line-1 attribution requires verification | `dd9cc650482d` (we miss the reference's first error) |
| yes | location | EndSub: orphan closer before malformed later header | `04754b91e0a1` (we miss the reference's first error) |
| yes | location | Continued routine-call arguments: diagnose the interrupted logical line | `15c0ffa7f461` (we miss the reference's first error), `af36b61a5e8d` (we miss the reference's first error) |
| yes | location | Sample of structure array: unfinished program suppresses earlier output bounds | `1765297afceb` (we report an earlier first location) |
| yes | location | Assignment: before-BeginProg error before later builtin arity failure | `5ad6e2faf8d6` (we miss the reference's first error) |
| yes | location | Array declaration: malformed as2 tail reaches later SortSpaIndexed type check | `b8a536b0b626` (we report an earlier first location) |
| yes | location | Nested DataTable: later unresolved Scan units suppress pairing diagnostic | `c4e3b64bbaea` (we report an earlier first location) |
| yes | location | Const: unknown declared type before missing BeginProg | `de121180e9d3` (we miss the reference's first error) |
| yes | location | EndTable: orphan closer before later Sample bounds | `fee97b435d76` (exact) |
| yes | location | StructureType: nested declaration rejected immediately | `0c4ac9d4ea46` (we miss the reference's first error) |
| yes | location | Unknown statement inside table before nested-table pairing error | `35b82c7d3d73` (we miss the reference's first error) |
| yes | location | Pointer dereference: undeclared RHS pointer operand | `cabcfa049316` (we miss the reference's first error) |
| yes | misleading | Alias: split source identifier resolved before alias-shape rejection | `171af87a935f` (same first location, different message), `36ca5746ad3f` (same first location, different message), `fa08d71c788b` (same first location, different message), `fbcc75c138fb` (same first location, different message), `1112ca94caa1` (same first location, different message), `77c6b4106904` (same first location, different message), `9b464f8050ed` (same first location, different message), `ab571ca664c5` (same first location, different message) |
| yes | misleading | BrHalf: invalid Range before trailing required-argument shortage | `01fd06fdb670` (same first location, different message) |
| yes | misleading | SerialOpen: baud callback before malformed numeric tail | `33f655758088` (same first location, different message), `6484ab6457b8` (same first location, different message), `3c772bc81211` (same first location, different message) |
| yes | misleading | StationName: split raw name leaves excess parameter text | `395582d8224e` (same first location, different message) |
| yes | misleading | Measurement instruction: Pipeline placement before names or missing BeginProg | `70a657faa894` (same first location, different message), `71e8e0489a40` (same first location, different message) |
| yes | misleading | Average: unresolved source before missing BeginProg at EOF | `75a81e272b10` (same first location, different message) |
| yes | misleading | VoltSE: incomplete constant argument before undefined destination | `7d67e53a0e92` (same first location, different message) |
| yes | misleading | DataTable: split name tokenization | `876ca9e34605` (same first location, different message), `e2e261651e4b` (same first location, different message), `840dfab819a2` (same first location, different message) |
| yes | misleading | SerialOpen: malformed ComPort prefix reaches port validation | `8db9658226d1` (same first location, different message) |
| yes | misleading | String destination parameter: validation before missing/excess arguments | `a41eadfe9dff` (same first location, different message), `8830a5aedd4a` (same first location, different message) |
| yes | misleading | FieldNames: excess raw-string argument before unterminated-quote fallback | `af04b066eb87` (same first location, different message), `f8bc16ed60b6` (same first location, different message) |
| yes | misleading | SerialIn: trailing required parameter before unresolved destination | `b4eddc0242fe` (same first location, different message) |
| yes | misleading | One-line If: placement before inline-body symbol resolution | `cb4871f692f9` (same first location, different message), `faf19469b65a` (same first location, different message) |
| yes | misleading | Variable/return type: unsupported type precedes shape or dimension findings | `ce418b0107bc` (same first location, different message), `dc8ef8bccf71` (same first location, different message), `385e5195ea93` (same first location, different message), `c6ca4c0fceb6` (same first location, different message) |
| yes | misleading | SerialOpen: unknown TXDelay changes argument consumption before CommsMode check | `16b8e7a5e759` (exact) |
| yes | misleading | Function header: malformed return As tail uses declaration diagnostic | `0f2aa052111c` (same first location, different message) |
| yes | misleading | If condition: bounds error before EOF block/routine pairing | `3d412625d8c2` (same first location, different message) |
| yes | misleading | Malformed Expr statement: actual statement error before EOF structural fallback | `53e5dbd01845` (same first location, different message), `931794460026` (same first location, different message), `f691eb7afa7a` (same first location, different message) |
| yes | misleading | TimeIntoInterval: interval validation before excess fourth argument | `3a948311392e` (same first location, different message) |
| yes | misleading | DataTable: malformed trigger expression retains expression diagnostic | `4ce782a860c6` (same first location, different message) |
| yes | misleading | DataInterval: signed argument validation before missing BeginProg | `74e259c13199` (same first location, different message), `ec07e9b06cd8` (same first location, different message) |
| yes | misleading | Unknown bare routine call: fallback before nested builtin arity | `8b9b6c965c5a` (same first location, different message) |
| yes | misleading | Routine parameter: malformed Optional keyword exposes trailing declaration | `8c9bb8e61e00` (same first location, different message) |
| yes | misleading | Routine parameter: parenthesized numeric-leading name | `a5ce30c8f8f6` (same first location, different message) |
| yes | misleading | SplitStr: malformed source diagnosed before unresolved destination | `ba1b3ee92608` (same first location, different message) |
| yes | misleading | Sample: malformed output type before unresolved member source | `e84fe90a6711` (same first location, different message) |
| yes | misleading | Next: split Scan token follows ordinary For/Next handling | `ed1cc693d9fa` (same first location, different message), `faa80a7207d1` (same first location, different message) |
| yes | wording | Dotted table reference: missing base named specifically | `b08378af611b` (same first location, different message), `ae52bdd5ebf7` (same first location, different message), `eb43a31508be` (same first location, different message), `72e1588c966c` (same first location, different message), `de1a8dcac79f` (same first location, different message) |
| yes | wording | Diagnostic echo: UTF-8 degree sign becomes mojibake | `1848fd1d95d1` (same first location, different message), `32f0c233f130` (same first location, different message) |
| yes | wording | Declaration dimension diagnostic: first segment versus whole-line echo | `3e418cd0c877` (same first location, different message), `963aae58f08b` (same first location, different message) |
| yes | wording | Long continued call: diagnostic echo truncation | `2c0b869a0a14` (same first location, different message), `5ff16ab82ca0` (same first location, different message) |
| yes | wording | Declaration: numeric-leading invalid-name echo | `1d9d25657d36` (same first location, different message) |
| yes | wording | Implicit routine call: Call versus Expr placement label | `35ad04a0f94e` (same first location, different message), `e8fd3e681e0f` (same first location, different message), `e94ffbdfec4e` (same first location, different message) |
| yes | wording | Explicit Call: nested arity diagnostic echoes argument rather than whole call | `962ab269d293` (same first location, different message) |
| yes | wording | Control condition: condition-only versus whole-statement echo | `a069d5c59801` (same first location, different message), `b4d42d0eac01` (same first location, different message), `9da6844f83f4` (same first location, different message) |
| yes | wording | Inline Return: empty versus unresolved-operand echo | `a596f1d8cacd` (same first location, different message) |
| yes | wording | Detached continuation expression: generic syntax label | `ae171e341a7f` (same first location, different message) |
| no | location | GOESField: active GOESTable required | `10ca1aaf308e` (we miss the reference's first error) |
| no | location | I2CRead/I2CWrite: prior I2COpen for selected port | `30dcc2cd4892` (we miss the reference's first error) |
| no | location | CSAT3B: SDM Scan placement before unresolved table | `038558b1033f` (we miss the reference's first error) |
| no | location | CheckPort: port validation before earlier orphan EndIf | `835ef408b21e` (we report an earlier first location) |
| no | misleading | CheckPort: empty call reaches port-range validation | `4a74895f32cf` (same first location, different message), `6f002bbe6754` (same first location, different message), `6f002bbe6754` (same first location, different message), `9c21b8dfe265` (same first location, different message) |
| no | misleading | SendVariables: first destination argument before later source lookup | `5486bfe505f0` (same first location, different message) |
| no | misleading | Menu instructions: enclosing DisplayMenu before arguments | `84a221517963` (same first location, different message), `7f916ecbef37` (same first location, different message), `e77eaa3ef4cc` (same first location, different message) |
| no | misleading | DNPVariable: source resolution before event-group dependency | `875fb092d22d` (same first location, different message), `15389f40689c` (same first location, different message), `5cde0ec61f37` (same first location, different message), `70500b7417f7` (same first location, different message) |
| no | misleading | Matrix: source shape before unresolved result | `bb0c9b9e1683` (same first location, different message) |
| no | misleading | CType: malformed token consumption reaches Illegal type | `eb6fad797531` (same first location, different message), `1aa55f1705d5` (same first location, different message), `3b16c0d475d6` (same first location, different message), `7214e5b36e9d` (same first location, different message) |
| no | misleading | AcceptDataRecords: table lookup before missing BeginProg | `08fa9332d936` (same first location, different message), `961836f88a83` (same first location, different message) |
| no | misleading | MenuPick: ConstTable item's name required as first parameter | `533da3db60a5` (same first location, different message) |
| no | misleading | SDMCD16Mask: Scan placement before unresolved data arguments | `05d27ab01be2` (same first location, different message), `54e000367f53` (same first location, different message), `c8e968a6fac0` (same first location, different message) |
| no | misleading | SendData: parameter shortage before unresolved argument | `65de263ef89c` (same first location, different message) |
| no | misleading | SerialInRecord: byte count versus unopened port buffer before destination lookup | `8f0284a7b655` (same first location, different message) |
| no | misleading | TDR100: Points range before unresolved destination | `c2e533318796` (same first location, different message) |
| no | misleading | TCDiff: converted Reps positivity before destination capacity | `3da710901444` (same first location, different message) |
| no | misleading | DisplayLine: excess raw-string argument before malformed-quote fallback | `004369d312af` (same first location, different message) |
| no | wording | Gzip: malformed quote/argument boundary gets a different syntax label | `3cf5c9209ff1` (same first location, different message), `a94a7b1583b7` (same first location, different message) |
| no | wording | AcceptDataRecords: unresolved destination table named generically versus specifically | `7b4d465ac222` (same first location, different message) |
| no | wording | PortsConfig: invalid binary literal diagnostic label | `72bbbb1c7e9b` (same first location, different message), `c6fc3897c0ad` (same first location, different message) |

## Round-59 realism triage

Human decision 2026-10-29 ([PLAN](../PLAN.md#v10-milestones)): M2 fixes only mistakes a programmer
could realistically make. Each row above was re-applied to its whole seed program (the fuzz edit
kept, the shrinker's deletions undone; `scratch/r59/unshrink.py`) and compiled with both compilers in
round 59. 75 of the 128 rows match there (two emptied conditions, `b4d42d0eac01` and
`9da6844f83f4`, after the condition-echo fix): their difference came from the truncated program the shrinker
left. Realistic single-edit variants of the rest were probed and fixed
([checker notes](checker.md#round-59-realistic-first-errors)). These 53 rows still differ on the whole
program; none is a realistic single edit in the relevant area. Reasons are from the edit text
(`scratch/r59/classify.py`), checked by hand.

| reason | family | reproducer |
|---|---|---|
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Array reference: four subscripts inside ASCII condition | `494a797d22ea` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | DataInterval: signed argument validation before missing BeginProg | `74e259c13199` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | DataInterval: signed argument validation before missing BeginProg | `ec07e9b06cd8` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | FieldClassify: diagnostic attributed to preceding output owner | `d406b0207cdc` (cr1000x) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Long continued call: diagnostic echo truncation | `2c0b869a0a14` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Long continued call: diagnostic echo truncation | `5ff16ab82ca0` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Routine parameter: parenthesized numeric-leading name | `a5ce30c8f8f6` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | String destination parameter: validation before missing/excess arguments | `8830a5aedd4a` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | TimeIntoInterval: interval validation before excess fourth argument | `3a948311392e` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Variable/return type: unsupported type precedes shape or dimension findings | `dc8ef8bccf71` (cr1000x) |
| a statement inserted into a continued line | Continued routine-call arguments: diagnose the interrupted logical line | `15c0ffa7f461` (cr6) |
| a statement inserted into a continued line | Continued routine-call arguments: diagnose the interrupted logical line | `af36b61a5e8d` (cr6) |
| a statement inserted into a continued line | Control condition: condition-only versus whole-statement echo | `a069d5c59801` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `171af87a935f` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `36ca5746ad3f` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `fa08d71c788b` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `fbcc75c138fb` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `1112ca94caa1` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `77c6b4106904` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `9b464f8050ed` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `ab571ca664c5` (cr6) |
| a blank inside a token | DataTable: malformed trigger expression retains expression diagnostic | `4ce782a860c6` (cr6) |
| a blank inside a token | DataTable: split name tokenization | `876ca9e34605` (cr1000x) |
| a blank inside a token | DataTable: split name tokenization | `e2e261651e4b` (cr1000x) |
| a blank inside a token | DataTable: split name tokenization | `840dfab819a2` (cr6) |
| a blank inside a token | Declaration: numeric-leading invalid-name echo | `1d9d25657d36` (cr6) |
| a blank inside a token | Dotted table reference: missing base named specifically | `ae52bdd5ebf7` (cr1000x) |
| a blank inside a token | EndSub: orphan closer before malformed later header | `04754b91e0a1` (cr6) |
| a blank inside a token | Next: split Scan token follows ordinary For/Next handling | `ed1cc693d9fa` (cr1000x) |
| a blank inside a token | Next: split Scan token follows ordinary For/Next handling | `faa80a7207d1` (cr6) |
| a blank inside a token | Sample: malformed output type before unresolved member source | `e84fe90a6711` (cr6) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `33f655758088` (cr1000x) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `6484ab6457b8` (cr1000x) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `3c772bc81211` (cr6) |
| a blank inside a token | SerialOpen: malformed ComPort prefix reaches port validation | `8db9658226d1` (cr1000x) |
| a blank inside a token | StationName: split raw name leaves excess parameter text | `395582d8224e` (cr1000x) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `eb6fad797531` (cr1000x) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `1aa55f1705d5` (cr6) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `3b16c0d475d6` (cr6) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `7214e5b36e9d` (cr6) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `4a74895f32cf` (cr1000x) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `6f002bbe6754` (cr1000x) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `6f002bbe6754` (cr6) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `9c21b8dfe265` (cr6) |
| outside the relevant set | CheckPort: port validation before earlier orphan EndIf | `835ef408b21e` (cr1000x) |
| outside the relevant set | Gzip: malformed quote/argument boundary gets a different syntax label | `3cf5c9209ff1` (cr1000x) |
| outside the relevant set | Gzip: malformed quote/argument boundary gets a different syntax label | `a94a7b1583b7` (cr1000x) |
| outside the relevant set | Menu instructions: enclosing DisplayMenu before arguments | `e77eaa3ef4cc` (cr6) |
| outside the relevant set | MenuPick: ConstTable item's name required as first parameter | `533da3db60a5` (cr6) |
| outside the relevant set | PortsConfig: invalid binary literal diagnostic label | `72bbbb1c7e9b` (cr6) |
| outside the relevant set | PortsConfig: invalid binary literal diagnostic label | `c6fc3897c0ad` (cr6) |
| outside the relevant set | SendData: parameter shortage before unresolved argument | `65de263ef89c` (cr6) |
| outside the relevant set | TCDiff: converted Reps positivity before destination capacity | `3da710901444` (cr1000x) |
