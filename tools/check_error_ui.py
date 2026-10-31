"""Verify error grouping, repair actions and manual context in a hidden neutral EXE."""

import ctypes
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from inspection_client import inspect_app
from update_package import reject_links


def main():
    parent = ROOT / "qa-knowledge"
    fixture = parent / ("error-ui-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    project = store.project(str(fixture))["id"]
    corpus = fixture / "text.json"
    corpus.write_text(
        json.dumps([{"source": "I copy the note."}, {"source": "Goodbye."}])
    )
    store.import_files(project, [corpus])
    settings = {
        "model": "fixture",
        "source_language": "English",
        "target_language": "Russian",
        "context": 8192,
        "max_output": 1200,
        "auto_foreign": False,
    }
    with store.db() as db:
        db.execute("update projects set settings=?", (json.dumps(settings),))
    job = store.create_job(project, "translate", "local", settings)
    for _ in range(3):
        store.error(job, 1, "Смешаны латиница и кириллица внутри слова: перепиcываю")
    for _ in range(2):
        store.error(
            job,
            2,
            "Смешаны латиница и кириллица внутри слова: свиdания",
            candidate="До свиdания.",
        )
    with store.db() as db:
        db.execute(
            "update jobs set state='paused',error='Neutral fixture; no model requests' where id=?",
            (job,),
        )
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    before = user32.GetForegroundWindow()
    report = fixture / "probe.json"
    process = subprocess.Popen(
        [
            str(ROOT / "release/LocalizationStudio/LocalizationStudio.exe"),
            "--hidden",
            "--db",
            str(store.path),
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(ROOT / "tools/error_ui_probe.js"),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 75
        while not (report.exists() and report.stat().st_size):
            if time.monotonic() > deadline or process.poll() is not None:
                raise RuntimeError("Neutral error UI probe did not complete")
            time.sleep(0.25)
        result = json.loads(report.read_text(encoding="utf-8"))
        result["foreground_unchanged"] = user32.GetForegroundWindow() == before
        (ROOT / "reports/error-ui-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert result.get("passed"), result
        current_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), ctypes.byref(current_pid)
        )
        result["fixture_not_foreground"] = current_pid.value != process.pid
        assert result["fixture_not_foreground"], "Hidden probe activated a window"
        try:
            import base64

            captured = inspect_app(store.path, True)
            (ROOT / "reports/error-ui.png").write_bytes(
                base64.b64decode(captured["image"]["data"])
            )
        except RuntimeError as error:
            result["screenshot_error"] = str(error)
        (ROOT / "reports/error-ui-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid fixture path")
        reject_links(fixture)
        for attempt in range(80):
            try:
                shutil.rmtree(fixture)
                break
            except OSError as error:
                if not fixture.exists():
                    break
                if (
                    not isinstance(error, (PermissionError, FileNotFoundError))
                    and getattr(error, "winerror", None) != 145
                ):
                    raise
                if attempt == 79:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
