
import concurrent.futures
import multiprocessing
import os
import time

import fuzz

from opencrbasic import profiles


def _slow_worker(path_str: str, logger: str, result_pipe) -> None:
    """Spawn-safe stand-in which deliberately exceeds the parent deadline."""
    time.sleep(10)
    result_pipe.send((0, "unexpected completion"))


def _exit_without_result(path_str: str, logger: str, result_pipe) -> None:
    """Spawn-safe stand-in for a worker which dies before reporting."""
    os._exit(7)


def _large_result_worker(path_str: str, logger: str, result_pipe) -> None:
    """Send enough data to fill an ordinary pipe buffer if the parent waits for exit first."""
    result_pipe.send((0, "x" * (128 * 1024)))
    result_pipe.close()


def test_parallel_run_ours_compiles_tiny_programs_without_hanging(tmp_path):
    cases = [
        (
            "valid.CR6",
            "cr6",
            b"Public X\nBeginProg\nX = 1\nEndProg\n",
            0,
            "valid.CR6 -- Compiled in PipelineMode.",
        ),
        (
            "invalid.CR6",
            "cr6",
            b"Public\nBeginProg\nEndProg\n",
            1,
            "invalid.CR6 -- Compile Failed!\n\nline 1: Variable not named.",
        ),
        (
            "9df4271b08ac.CR6",
            "cr350",
            b"' fuzz: seed=chm/splitstr_1.CR6 ops=duplicate_line class=false_accept\n"
            b"Public SearchString As String * 50\n"
            b"Public SearchString As String * 50\n"
            b"BeginProg\n",
            1,
            "9df4271b08ac.CR6 -- Compile Failed!\n\n"
            "line 3: SearchString already declared as a variable.",
        ),
        (
            "afdfa27ff22f.CR6",
            "cr350",
            b"' fuzz: seed=chm/ipinfo_1.CR6 ops=change_interval+delete_line class=message_diff\n"
            b"Public IPInfoVar As String * 50\n\n"
            b"  Scan (1,Sec,3,999999999999)\n"
            b"    'return the IP address of the Ethernet interface, in Ipv4 format\n"
            b"    IPInfoVar = IPInfo (0,0)\n\n"
            b"  NextScan\n"
            b"EndProg\n",
            1,
            "afdfa27ff22f.CR6 -- Compile Failed!\n\n"
            "line 4: Scan illegally occurs before BeginProg.",
        ),
    ]

    work = []
    for name, logger, source, expected_exit, expected_stdout in cases:
        path = tmp_path / name
        path.write_bytes(source)
        work.append((path, profiles.get(logger), expected_exit, expected_stdout))

    # Ten repetitions exercise 40 spawned children while four parent threads wait concurrently.
    calls = work * 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: fuzz.run_ours(item[0], item[1]), calls))

    assert results == [(item[2], item[3]) for item in calls]


def test_run_ours_reports_worker_exit_without_result_as_crash(monkeypatch, tmp_path):
    program = tmp_path / "exit.CR6"
    program.write_bytes(b"BeginProg\nEndProg\n")
    monkeypatch.setattr(fuzz, "_run_ours_child", _exit_without_result)

    exit_code, output = fuzz.run_ours(program, profiles.get("cr6"), timeout=2)

    assert exit_code == 2
    assert output == "CRASH: worker process exited without a result (code 7)"


def test_run_ours_receives_large_result_without_waiting_for_child_exit(monkeypatch, tmp_path):
    program = tmp_path / "large.CR6"
    program.write_bytes(b"BeginProg\nEndProg\n")
    monkeypatch.setattr(fuzz, "_run_ours_child", _large_result_worker)

    exit_code, output = fuzz.run_ours(program, profiles.get("cr6"), timeout=2)

    assert exit_code == 0
    assert output == "x" * (128 * 1024)


def test_run_ours_hard_deadline_stops_slow_worker(monkeypatch, tmp_path):
    program = tmp_path / "slow.CR6"
    program.write_bytes(b"BeginProg\nEndProg\n")
    monkeypatch.setattr(fuzz, "_run_ours_child", _slow_worker)
    children_before = {child.pid for child in multiprocessing.active_children() if child.is_alive()}

    started = time.monotonic()
    exit_code, output = fuzz.run_ours(program, profiles.get("cr6"), timeout=0.6)
    elapsed = time.monotonic() - started

    assert exit_code == 3
    assert output.startswith("HANG: opencrbasic did not return within ")
    assert elapsed < 3.0
    children_after = {child.pid for child in multiprocessing.active_children() if child.is_alive()}
    assert children_after - children_before == set()
