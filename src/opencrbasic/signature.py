"""ProgSignature (`CR6Comp -s`): the PakBus signature of the program text.

CR6Comp seeds the signature with 0xAAAA, runs it over the main file's bytes, then continues it
over each included file's bytes in the order the includes are expanded (an `IncludeSection` of
the same file twice contributes the file twice). Verified against the exe: PT1000_tester.CR6
with its five IncludeSection expansions gives 53316 (docs/cli.md).
"""

from pathlib import Path


def csi_signature(data: bytes, seed: int = 0xAAAA) -> int:
    sig = seed
    for byte in data:
        prev = sig
        sig = (sig << 1) & 0x1FF
        if sig >= 0x100:
            sig += 1
        sig = (((sig + (prev >> 8) + byte) & 0xFF) | (prev << 8)) & 0xFFFF
    return sig


def program_signature(path: Path, source=None) -> int:
    from . import source as source_mod
    from .diagnostics import Diagnostics

    path = Path(path)
    loader = source_mod._Loader(path.parent, Diagnostics(), source=source)
    loader.load_file(path, section=None, in_include=False)
    sig = 0xAAAA
    for data in loader.files:
        sig = csi_signature(data, sig)
    return sig
