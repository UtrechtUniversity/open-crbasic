from pathlib import Path

from opencrbasic.signature import csi_signature, program_signature

CRBASIC = Path(__file__).resolve().parents[2] / "crbasic"


def test_csi_signature_empty_is_seed():
    assert csi_signature(b"") == 0xAAAA


def test_prog_signature_matches_cr6comp():
    # values printed by CR6Comp.exe -s on 2026-09-25
    expected = {"PT1000_tester.CR6": 53316, "Main.CR6": 60338, "TDR_tester.CR6": 6505,
                "SBD_tester.CR6": 42286, "FinApp_tester.CR6": 5733}
    for name, value in expected.items():
        assert program_signature(CRBASIC / name) == value, name
