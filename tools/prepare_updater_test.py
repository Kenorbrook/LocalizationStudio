"""Exercise the compiled updater on an isolated, neutral Windows installation."""

import ctypes
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from update_package import reject_links
from version import VERSION


def close_window(pid):
    api = ctypes.WinDLL("user32")
    api.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    api.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(handle, _):
        owner = ctypes.c_ulong()
        api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid:
            api.PostMessageW(handle, 0x10, None, None)
        return True

    api.EnumWindows(callback(visit), None)


def wait_file(path, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return
        time.sleep(0.25)
    raise RuntimeError("Timed out: " + str(path))


def fixture_processes(home):
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='LocalizationStudio.exe'\" | Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )
    values = json.loads(result.stdout.lstrip("\ufeff") or "[]")
    if isinstance(values, dict):
        values = [values]
    return [
        value["ProcessId"]
        for value in values
        if value.get("ExecutablePath") == str(home / "LocalizationStudio.exe")
    ]


def main():
    parent = ROOT / "qa-updater"
    home = parent / str(time.time_ns())
    home.mkdir(parents=True)
    stage = ROOT / "release/LocalizationStudio"

    def copy_bundle(destination):
        manifest = json.loads(
            (stage / "release-manifest.json").read_text(encoding="utf-8")
        )
        destination.mkdir(parents=True, exist_ok=True)
        for name in [*manifest["files"], "release-manifest.json"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stage / name, target)

    copy_bundle(home)
    data = home / "data"
    data.mkdir()
    database = data / "studio.sqlite3"
    store = Store(database)
    (home / "neutral-game").mkdir()
    project = store.project(str(home / "neutral-game"))["id"]
    corpus = home / "neutral.json"
    corpus.write_text(
        json.dumps([{"source": "Hello, world.", "translation": "Привет, мир."}]),
        encoding="utf-8",
    )
    store.import_files(project, [corpus])
    (data / "desktop_preferences.json").write_text('{"close_choice":"exit"}')
    (data / "sentinel.txt").write_text("preserve user data")
    # The older runtime marker must be replaced, while unrelated files survive.
    (home / "README.md").write_text("old runtime marker")
    session = data / "updates/test"
    session.mkdir(parents=True)
    copy_bundle(session / "bundle")
    helper = session / "LocalizationUpdater.exe"
    shutil.copy2(stage / "LocalizationUpdater.exe", helper)
    report = home / "probe.json"
    probe = home / "probe.js"
    probe.write_text(
        "(async()=>{try{const state=await window.pywebview.api.get_update_state();await refreshUpdates();window.__featureProbe={passed:state.supported&&state.current==='"
        + VERSION
        + "'&&$('updatesMenu').firstElementChild.tagName==='SUMMARY',state};}catch(e){window.__featureProbe={passed:false,error:e.message};}})()",
        encoding="utf-8",
    )
    gui = subprocess.Popen(
        [
            str(home / "LocalizationStudio.exe"),
            "--db",
            str(database),
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(probe),
        ]
    )
    updater = None
    try:
        wait_file(report)
        initial = json.loads(report.read_text(encoding="utf-8"))
        assert initial["passed"], initial
        plan = {
            "home": str(home),
            "database": str(database),
            "bundle": str(session / "bundle"),
            "backup": str(data / "backups/update-test"),
            "version": VERSION,
            "gui_pid": gui.pid,
            "resume_jobs": [],
        }
        plan_path = session / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        updater = subprocess.Popen(
            [str(helper), "--plan", str(plan_path)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(1)
        assert (
            home / "README.md"
        ).read_text() == "old runtime marker", "Replaced files before GUI exited"
        close_window(gui.pid)
        gui.wait(timeout=20)
        updater.wait(timeout=60)
        wait_file(session / "result.json")
        result = json.loads((session / "result.json").read_text(encoding="utf-8"))
        assert result["installed"], result
        end = time.monotonic() + 40
        while time.monotonic() < end:
            log = home / "logs/desktop.log"
            urls = re.findall(
                r"Localization Studio: (http://127\.0\.0\.1:\d+/)",
                log.read_text(encoding="utf-8"),
            )
            if len(urls) >= 2 and not (session / "bundle").exists():
                break
            time.sleep(0.25)
        assert len(urls) >= 2, "Updated app did not restart"
        token = (data / "server.token").read_text().strip()
        message = urllib.request.Request(
            urls[-1] + "api/state", headers={"X-Studio-Token": token}
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            message, timeout=10
        ) as response:
            state = json.load(response)
        with store.db() as db:
            assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 1
            assert (
                db.execute("SELECT text FROM records").fetchone()[0] == "Привет, мир."
            )
        assert (data / "sentinel.txt").read_text() == "preserve user data"
        assert (
            data / "backups/update-test/application/README.md"
        ).read_text() == "old runtime marker"
        assert not (session / "bundle").exists(), "Staged files were not cleaned"
        summary = {
            "passed": True,
            "native_bridge": True,
            "waits_for_gui_exit": True,
            "restarted": True,
            "records_preserved": True,
            "runtime_backup": True,
            "stage_cleaned": True,
            "version": VERSION,
        }
        (ROOT / "reports/updater-native-result.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary))
    finally:
        if gui.poll() is None:
            close_window(gui.pid)
            gui.wait(timeout=20)
        if updater and updater.poll() is None:
            updater.wait(timeout=60)
        for pid in fixture_processes(home):
            close_window(pid)
            from updater import wait_for_exit

            wait_for_exit(pid, 20)
        if home.resolve().parent != parent.resolve():
            raise RuntimeError("Unexpected fixture path")
        reject_links(home)
        shutil.rmtree(home)


if __name__ == "__main__":
    main()
