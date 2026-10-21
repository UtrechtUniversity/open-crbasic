from pathlib import Path

from opencrbasic.signature import csi_signature, program_signature

CRBASIC = Path(__file__).resolve().parents[2] / "crbasic"


def test_csi_signature_empty_is_seed():
    assert csi_signature(b"") == 0xAAAA


def test_prog_signature_matches_cr6comp():
    # CR6Comp.exe -s: Main re-observed 2026-10-21 after Shunt removal; others 2026-10-16.
    expected = {"PT1000_tester.CR6": 53316, "Main.CR6": 16571, "TDR_tester.CR6": 6505,
                "SBD_tester.CR6": 42286, "FinApp_tester.CR6": 2012}
    for name, value in expected.items():
        assert program_signature(CRBASIC / name) == value, name
