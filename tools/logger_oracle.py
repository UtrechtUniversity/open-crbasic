"""Compile fixture cases on a real logger and compare its verdict with the PC compiler and crbc.

The logger's own compiler decides whether a program runs; the PC pre-compilers are what the
fixtures record. This sends a sample of fixture cases to a logger attached by USB, has it compile
each one (File Control 1, which also runs it), and puts the logger's verdict beside the recorded
one, crbc's, and -- when it is installed -- the PC compiler's own on the same bytes.

    uv run python tools/logger_oracle.py COM5 cr6 out.jsonl

Needs open-loggernet (https://github.com/UtrechtUniversity/open-loggernet) for PakBus. **It
replaces the logger's program, and so deletes its data tables**: use a bench logger only. Cases
using instructions that could cut the logger off (settings, routes, the USB port, networking) are
skipped. Results: docs/on-logger.md.
"""

import argparse
import glob
import json
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from openloggernet import flash, pakbus

ROOT = Path(__file__).resolve().parent.parent
EXT = {"cr6": ".CR6", "cr1000x": ".CR1X", "cr300": ".CR300", "cr350": ".CR350"}
PC_COMPILER = {"cr6": "CR6Comp.exe", "cr1000x": "cr1000xcomp.exe", "cr300": "cr300comp.exe", "cr350": "cr350comp.exe"}
PC_DIRS = [ROOT / "compiler", Path(r"C:\Campbellsci\Lib\Compilers")]  # the repo's, then LoggerNet's
UNSAFE = re.compile(
    r"SetSetting|SetStatus|PakBus|Route|PPP|TCPOpen|UDPOpen|IPNet|Email|ComUSB|Restart|FileManage|Encrypt|"
    r"NetworkTime|Settings\.|SerialOpen|USBPort|USR:|CardOut|ModbusServer|DNP|SDI12Sensor|WebPageBegin|HTTP|FTP|"
    r"MQTT|PortsConfig|SW12|PortSet|WriteIO|Include",
    re.I,
)


def sample(logger: str, accepted: int, rejected: int, seed: int) -> list[dict]:
    pool: dict[int, list] = {0: [], 1: []}
    for f in sorted(glob.glob(str(ROOT / "tests" / "fixtures" / "*.json"))):
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        for n, case in enumerate(data.get("cases", []) if isinstance(data, dict) else []):
            if case.get("logger") == logger and "source" in case and case.get("expected_exit") in (0, 1):
                if not UNSAFE.search(case["source"]):
                    case_id = case.get("id") or f"{Path(f).stem}#{n}"
                    pool[case["expected_exit"]].append({**case, "id": case_id, "fixture": Path(f).name})
    rng = random.Random(seed)
    return rng.sample(pool[0], min(accepted, len(pool[0]))) + rng.sample(pool[1], min(rejected, len(pool[1])))


def compile_on_host(command: list[str], data: bytes, ext: str) -> tuple[int, str]:
    """Run a compiler on exactly `data`: written as bytes, so CRLF stays CRLF on Windows."""
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / f"t{ext}").write_bytes(data)
        r = subprocess.run([*command, f"t{ext}"], capture_output=True, text=True, cwd=d, timeout=120)
        return r.returncode, r.stdout.strip()


def compile_on_logger(link: pakbus.PakBusLink, data: bytes, ext: str) -> tuple[int, str]:
    """(compile state, compile result): state 1 compiled, 2 refused."""
    name = f"CPU:t{ext}"
    if link.file_download(name, data) != 0:
        return -1, "download refused"
    _, hold_off = link.file_control(name, pakbus.FILECTL_COMPILE_RUN_POWERUP)
    if hold_off:
        time.sleep(hold_off)
        if flash.reconnect(link, link._port_name, timeout=120, model=link.model) is None:
            return -1, "the logger did not come back"
    stats = link.get_program_stats()
    return stats["compile_state"], stats["compile_result"].strip()


def lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port")
    ap.add_argument("logger", choices=sorted(EXT))
    ap.add_argument("out", type=Path)
    ap.add_argument("--accepted", type=int, default=40)
    ap.add_argument("--rejected", type=int, default=40)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    ext = EXT[args.logger]
    pc = next((d / PC_COMPILER[args.logger] for d in PC_DIRS if (d / PC_COMPILER[args.logger]).is_file()), None)
    crbc = [sys.executable, "-m", "opencrbasic.cli", "--logger", args.logger, "--no-message-file"]
    cases = sample(args.logger, args.accepted, args.rejected, args.seed)
    tally = {"cases": 0, "logger=pc": 0, "crbc=pc": 0, "logger=recorded": 0}
    with pakbus.PakBusLink(args.port) as link, args.out.open("w", encoding="utf-8") as out:
        if not link.hello():
            raise SystemExit(f"no answer on {args.port}")
        print(
            f"{args.logger} {link.get_program_stats()['os_version']}; PC compiler: {pc or 'none'}; {len(cases)} cases"
        )
        for i, case in enumerate(cases, 1):
            data = case["source"].encode("latin-1", "replace")
            try:
                state, logger_out = compile_on_logger(link, data, ext)
            except (pakbus.PakBusError, OSError) as exc:  # e.g. a write timeout while it restarts
                state, logger_out = -1, f"error: {exc}"
                flash.reconnect(link, link._port_name, timeout=120, model=link.model)
            row = {
                "id": case["id"],
                "fixture": case["fixture"],
                "recorded_exit": case["expected_exit"],
                "logger_exit": {1: 0, 2: 1}.get(state),
                "logger_out": logger_out,
            }
            row["crbc_exit"], row["crbc_out"] = compile_on_host(crbc, data, ext)
            if pc:
                row["pc_exit"], row["pc_out"] = compile_on_host([str(pc)], data, ext)
            out.write(json.dumps(row) + "\n")
            out.flush()
            reference = row.get("pc_exit", row["recorded_exit"])
            tally["cases"] += 1
            tally["logger=pc"] += row["logger_exit"] == reference
            tally["crbc=pc"] += row["crbc_exit"] == reference
            tally["logger=recorded"] += row["logger_exit"] == row["recorded_exit"]
            same = row["logger_exit"] == reference == row["crbc_exit"]
            print(
                f"{i:3d} {case['id'][:48]:48s} pc {reference} logger {row['logger_exit']} crbc {row['crbc_exit']}"
                + ("" if same else "  <-- differs"),
                flush=True,
            )
    print(json.dumps(tally))
    return 0


if __name__ == "__main__":
    sys.exit(main())
