"""Pause/resume installed workers around an update without changing job settings."""

import argparse
import ctypes
import json
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "update-running-jobs.json"


def request(action, data):
    addresses = re.findall(
        r"Localization Studio: (http://127\.0\.0\.1:\d+/)",
        (ROOT / "logs/desktop.log").read_text(encoding="utf-8"),
    )
    if not addresses:
        raise RuntimeError("No installed server address")
    token = (ROOT / "data/server.token").read_text(encoding="ascii").strip()
    message = urllib.request.Request(
        addresses[-1] + "api/" + action,
        data=json.dumps(data).encode(),
        headers={"X-Studio-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        message, timeout=15
    ) as response:
        return json.load(response)


def exited(pid):
    if not pid:
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["pause", "resume"])
    args = parser.parse_args()
    if args.mode == "resume":
        jobs = json.loads(REPORT.read_text(encoding="utf-8"))
        for job in jobs:
            request(
                "control",
                {"id": job["id"], "mode": "resume", "preserve_project_settings": True},
            )
        print(json.dumps({"resumed": [job["id"] for job in jobs]}))
        return
    with sqlite3.connect(ROOT / "data/studio.sqlite3") as db:
        db.row_factory = sqlite3.Row
        jobs = [
            dict(row)
            for row in db.execute(
                "SELECT id,pid,settings FROM jobs WHERE state IN ('running','queued')"
            )
        ]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    for job in jobs:
        request("control", {"id": job["id"], "mode": "pause"})
    deadline = time.monotonic() + 60
    while not all(exited(job["pid"]) for job in jobs):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Worker is still completing its request; update has not been installed"
            )
        time.sleep(0.5)
    print(json.dumps({"paused": [job["id"] for job in jobs], "workers_exited": True}))


if __name__ == "__main__":
    main()
