"""Compile an older neutral baseline and exercise the native GitHub update flow."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from update_package import reject_links
from prepare_updater_test import (
    close_window,
    fixture_processes,
    fixture_browser_processes,
)
from updater import wait_for_exit


def main():
    parent = ROOT / "qa-update-flow"
    fixture = parent / str(time.time_ns())
    fixture.mkdir(parents=True)
    for name in ["src", "docs", "packaging"]:
        shutil.copytree(
            ROOT / name, fixture / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    (fixture / "src/version.py").write_text(
        'VERSION="0.0.0"\nREPOSITORY="Kenorbrook/LocalizationStudio"\n',
        encoding="utf-8",
    )
    reports = ROOT / "reports"
    log = reports / "update-flow-build.log"
    with log.open("w", encoding="utf-8") as output:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--distpath",
                str(fixture / "release"),
                "--workpath",
                str(fixture / "build"),
                str(fixture / "packaging/LocalizationStudio.spec"),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )
    home = fixture / "release/LocalizationStudio"
    data = home / "data"
    data.mkdir()
    (data / "desktop_preferences.json").write_text('{"close_choice":"exit"}')
    store = Store(data / "studio.sqlite3")
    game = home / "neutral-game"
    game.mkdir()
    pid = store.project(str(game))["id"]
    corpus = game / "text.json"
    corpus.write_text(
        json.dumps([{"source": "Hello.", "translation": "Привет."}]), encoding="utf-8"
    )
    store.import_files(pid, [corpus])
    (data / "sentinel.txt").write_text("preserve user data")
    script = fixture / "probe.js"
    script.write_text(
        """window.__featureProbe={started:true};
setTimeout(async()=>{try{
 await window.pywebview.api.check_updates();
 const until=async phase=>{for(let i=0;i<240;i++){const state=await window.pywebview.api.get_update_state();displayUpdateState(state);if(state.phase==='error')throw Error(state.message);if(state.phase===phase)return state;await new Promise(r=>setTimeout(r,1000));}throw Error('timeout');};
 await until('available');await window.pywebview.api.download_update();await until('ready');await window.pywebview.api.install_update();
}catch(error){window.__featureProbe={error:error.message};}},0);""",
        encoding="utf-8",
    )
    gui = subprocess.Popen(
        [
            str(home / "LocalizationStudio.exe"),
            "--smoke-report",
            str(fixture / "started.json"),
            "--ui-test-script",
            str(script),
        ]
    )
    try:
        end = time.monotonic() + 300
        result = None
        plan_path = None
        while time.monotonic() < end:
            paths = (
                list((data / "updates").glob("*/result.json"))
                if (data / "updates").exists()
                else []
            )
            if paths:
                result = json.loads(paths[0].read_text(encoding="utf-8"))
                plan_path = paths[0].with_name("plan.json")
                break
            time.sleep(0.5)
        assert result and result.get("installed"), (
            result or "Native installation did not finish"
        )
        gui.wait(timeout=20)
        end = time.monotonic() + 40
        while time.monotonic() < end:
            if not plan_path.with_name("bundle").exists() and fixture_processes(home):
                break
            time.sleep(0.5)
        with store.db() as db:
            assert db.execute("SELECT text FROM records").fetchone()[0] == "Привет."
        assert (data / "sentinel.txt").read_text() == "preserve user data"
        summary = {
            "passed": True,
            "native_check": True,
            "native_download": True,
            "native_install": True,
            "restart": True,
            "data_preserved": True,
            "version": result["version"],
        }
        (reports / "update-flow-native-result.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary))
    finally:
        browser_children = fixture_browser_processes(home)
        if gui.poll() is None:
            close_window(gui.pid)
            gui.wait(timeout=20)
        for owner in fixture_processes(home):
            close_window(owner)
            wait_for_exit(owner, 20)
        for owner in browser_children:
            wait_for_exit(owner, 20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Unexpected fixture path")
        reject_links(fixture)
        shutil.rmtree(fixture)


if __name__ == "__main__":
    main()
