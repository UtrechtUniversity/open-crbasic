# CRBasic instruction parameter type codes

How `CR6Comp.exe` parses each instruction's argument list, decoded from `FUN_004baf00` (the
generic parameter parser, Ghidra decompilation) and confirmed against the real compiler under
Wine (`tools/oracle.py`). Every instruction's signature in `src/opencrbasic/data/instructions.json`
(`params`) is a list of these codes, one per declared parameter, taken from the table at `0x60c310`
(`docs/binary.md`).

Status: in progress, written incrementally. Sections below fill in as they are confirmed.
