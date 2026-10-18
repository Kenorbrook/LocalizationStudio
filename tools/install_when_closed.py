"""Wait for installed processes to exit naturally, then install the verified build."""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from update_package import validate_bundle
from version import VERSION


def installed_processes():
    command = "Get-CimInstance Win32_Process -Filter \"Name LIKE 'Localization%.exe'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Cannot verify application processes: " + result.stderr)
    rows = json.loads(result.stdout.lstrip("\ufeff")) if result.stdout.strip() else []
    rows = rows if isinstance(rows, list) else [rows]
    if any(not row.get("ExecutablePath") for row in rows):
        raise RuntimeError("Cannot verify executable path of an application process")
    return [row for row in rows if Path(row["ExecutablePath"]).resolve().parent == ROOT]


def main():
    validate_bundle(ROOT / "release/LocalizationStudio", VERSION)
    lock = ROOT / "reports/local-install.lock"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(VERSION)
    try:
        deadline = time.monotonic() + 12 * 3600
        processes = installed_processes()
        print(
            json.dumps({"waiting_for_exit": processes, "version": VERSION}), flush=True
        )
        while processes:
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "Installation timed out; application was left untouched"
                )
            time.sleep(5)
            processes = installed_processes()
        subprocess.run(
            [sys.executable, str(ROOT / "tools/install_build.py")], check=True
        )
        subprocess.Popen(
            [str(ROOT / "LocalizationStudio.exe"), "--hidden"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print("Installed; restarted in tray without activation", flush=True)
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
