"""Completion before the first analysis (LSP-R4b, host record `ccba3666` T-09 "Member
completion"): a `Status.` / `Public.` typed right after opening an entry, before any program
exists, waits up to `featureWaitMs` for the first program instead of answering an empty,
complete list. Real stdio servers through `lspclient.py`, and an in-process `Session`."""

import asyncio
import time
from pathlib import Path

import pytest

from opencrbasic.lsp.config import Config
from opencrbasic.lsp.session import Session

from .lspclient import LspClient
from .test_server import make_client, project, started  # noqa: F401

pytestmark = pytest.mark.server

VALID = "Public Temp\nBeginProg\n  Scan(1,Sec,0,0)\n    Temp = 1\n  NextScan\nEndProg\n"


def with_member(name: str) -> str:
    return VALID.replace("    Temp = 1\n", f"    Temp = 1\n    x = {name}.\n")


def complete_member(client: LspClient, path: Path, name: str) -> dict:
    return client.at("textDocument/completion", path, 4, len(f"    x = {name}."))


@pytest.mark.parametrize("name, member", [("Status", "SkippedScan"), ("Public", "Temp")])
def test_member_completion_right_after_open_and_change(make_client, tmp_path, name, member):
    # the host's sequence: open a valid entry, type `x = <name>.` at once, ask without waiting
    root = project(tmp_path, **{"E.CR6": VALID})
    entry = root / "E.CR6"
    client = started(make_client, root, {"debounceMs": 300, "entries": ["E.CR6"]})
    client.open(entry, VALID)
    client.change(entry, with_member(name), 2)
    answer = complete_member(client, entry, name)
    labels = [i["label"] for i in answer["items"]]
    assert member in labels and answer["isIncomplete"] is False, answer
    assert client.shutdown() == 0


def test_member_completion_when_only_the_broken_text_was_ever_analysed(make_client, tmp_path):
    text = with_member("Status")
    root = project(tmp_path, **{"E.CR6": text})
    entry = root / "E.CR6"
    client = started(make_client, root, {"entries": ["E.CR6"]})
    client.open(entry, text)
    answer = complete_member(client, entry, "Status")
    assert "SkippedScan" in [i["label"] for i in answer["items"]], answer
    assert client.shutdown() == 0


def test_without_a_program_in_time_the_list_is_incomplete(make_client, tmp_path):
    # featureWaitMs bounds the wait; the empty answer then tells the client to ask again
    root = project(tmp_path, **{"E.CR6": VALID})
    entry = root / "E.CR6"
    client = started(make_client, root, {"debounceMs": 3000, "featureWaitMs": 0, "entries": ["E.CR6"]})
    client.open(entry, VALID)
    client.change(entry, with_member("Status"), 2)  # replaces the open's analysis
    start = time.monotonic()
    answer = complete_member(client, entry, "Status")
    assert time.monotonic() - start < 2.5
    assert answer["items"] == [] and answer["isIncomplete"] is True, answer
    client.on_file("textDocument/documentSymbol", entry)  # waits for the first program
    answer = complete_member(client, entry, "Status")
    assert "SkippedScan" in [i["label"] for i in answer["items"]]
    assert answer["isIncomplete"] is False
    assert client.shutdown() == 0


def test_analysed_for_first_waits_only_for_a_missing_program(tmp_path):
    root = project(tmp_path, **{"E.CR6": VALID})
    entry = root / "E.CR6"

    async def scenario() -> None:
        session = Session(lambda *_: None, workers=1)
        await session.configure(Config(debounce_ms=0, analysis_timeout_ms=60000, feature_wait_ms=30000), [root])
        session.did_open(entry, entry.as_uri(), 1, VALID)
        assert await session.analysed_for(entry, wait=False) is None  # never waits
        first = await session.analysed_for(entry, wait=False, first=True)
        assert first is not None and session.fresh(entry, first)
        # a program exists: a newer version pending is not waited for
        session.did_change(entry, 2, with_member("Status"))
        loop = asyncio.get_running_loop()
        start = loop.time()
        again = await session.analysed_for(entry, wait=False, first=True)
        assert again is first and not session.fresh(entry, again)
        assert loop.time() - start < 0.5
        session.close()

    asyncio.run(scenario())
