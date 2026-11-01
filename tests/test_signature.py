from pathlib import Path

from opencrbasic.signature import csi_signature, program_signature

CRBASIC = Path(__file__).resolve().parents[1] / "tests" / "corpus" / "firnmelt"


def test_csi_signature_empty_is_seed():
    assert csi_signature(b"") == 0xAAAA


def test_prog_signature_matches_cr6comp():
    # CR6Comp.exe -s: re-observed 2026-10-22 after GPS and shared bench changes.
    expected = {"PT1000_tester.CR6": 45535, "Main.CR6": 60721, "TDR_tester.CR6": 60752,
                "SBD_tester.CR6": 47622, "FinApp_tester.CR6": 11702}
    for name, value in expected.items():
        assert program_signature(CRBASIC / name) == value, name
