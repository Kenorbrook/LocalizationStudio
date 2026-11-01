"""Verify the independent plugin on a neutral DB; optional real local Hermes run."""

import argparse
import base64
import ctypes
import json
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from plugin_backend import seed_module
from plugin_manager import PluginManager
from plugin_package import guard
from inspection_client import inspect_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    parent = ROOT / "qa-knowledge"
    fixture = guard(parent / ("hermes-resolution-" + uuid.uuid4().hex))
    fixture.mkdir(parents=True)
    process = None
    controller = None
    try:
        store = Store(fixture / "studio.sqlite3")
        store.launch_lock = threading.RLock()
        project = store.project(str(fixture))["id"]
        corpus = fixture / "neutral.json"
        corpus.write_text(
            json.dumps(
                [
                    {
                        "source": "My father was transferred at work.",
                        "translation": "Моего отца перевели по работе.",
                    },
                    {
                        "source": "The transfer happened suddenly.",
                        "translation": "Перевод произошёл неожиданно.",
                    },
                    {"source": "The door is closed.", "translation": "Дверь открыта."},
                    {"source": "The window is open.", "translation": "Окно открыто."},
                ]
            ),
            encoding="utf-8",
        )
        store.import_files(project, [corpus])
        settings = {
            "model": "neutral-fixture",
            "target_language": "Russian",
            "context": 8192,
        }
        with store.db() as db:
            db.execute("UPDATE projects SET settings=?", (json.dumps(settings),))
        job = store.create_job(project, "review", "local", settings)
        for record in (2, 3, 4):
            store.error(
                job, record, "Редактор не подтвердил результат: нужна проверка смысла"
            )
        with store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (job,))
        module = seed_module("hermes", "hermes_plugin")
        controller = module.HermesPlugin(store)
        controller.root.mkdir(parents=True, exist_ok=True)
        source = controller.root / "home/hermes-agent"
        source.mkdir(parents=True)
        (source / "run_agent.py").write_text("# neutral marker; never executed")
        (controller.root / "installed.json").write_text(
            json.dumps({"commit": module.COMMIT})
        )
        if args.live:
            with closing(
                sqlite3.connect(
                    (ROOT / "data/studio.sqlite3").as_uri() + "?mode=ro", uri=True
                )
            ) as live:
                if live.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('running','queued') OR worker_active=1"
                ).fetchone():
                    raise RuntimeError(
                        "A user worker is active; neutral inference was not started"
                    )
            owner = ROOT / "data/plugins/hermes/home"
            actual_source = owner / "hermes-agent"
            from hermes_install import environment

            env = environment(owner)
            code = "import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from hermes_cli._launchers import resolve_store_python;print(resolve_store_python(Path(sys.argv[1])) or '')"
            runtime = subprocess.check_output(
                [sys.executable, "-I", "-c", code, str(actual_source)],
                env=env,
                text=True,
            ).strip()
            if not runtime or not Path(runtime).is_file():
                raise RuntimeError("Install the Hermes runtime first")
            preferences = json.loads(
                (ROOT / "data/plugins/hermes/preferences.json").read_text(
                    encoding="utf-8"
                )
            )
            controller.configure({**preferences, "enabled": True})
            bridge = ROOT / "plugins/hermes/resources/hermes_bridge.py"
            facts = next((owner / "installs").glob("*/facts.json"))
            dependency = (
                Path(
                    json.loads(facts.read_text(encoding="utf-8"))["packages"]["venv"][
                        "environment"
                    ]
                )
                / "Lib/site-packages"
            )
            # Borrow installed libraries, while all sessions/config/logs stay in this fixture.
            entrypoint = "import sys,site,runpy;site.addsitedir(sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')"
            module.runner_command = lambda _root, request: [
                runtime,
                "-I",
                "-X",
                "utf8",
                "-c",
                entrypoint,
                str(dependency),
                str(bridge),
                str(request),
                str(actual_source),
            ]
            controller.run(project, "repair_errors")
            deadline = time.monotonic() + 3600
            while controller.busy:
                if time.monotonic() > deadline:
                    raise RuntimeError("Neutral live test exceeded one hour")
                time.sleep(1)
            saved = json.loads(
                (controller.root / f"project-{project}-report.json").read_text(
                    encoding="utf-8"
                )
            )
            saved["phase"] = controller.status["phase"]
            saved["message"] = controller.status["message"]
            log = controller.root / "activity.log"
            if log.exists():
                (ROOT / "reports/hermes-resolution-neutral-runtime.txt").write_text(
                    log.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
                )
            (ROOT / "reports/hermes-resolution-live.json").write_text(
                json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                json.dumps({"phase": saved["phase"], "counts": saved["counts"]}),
                flush=True,
            )
            assert (
                saved["phase"] == "ready"
                and saved["counts"]["fix"] >= 1
                and saved["counts"]["keep"] >= 1
            ), saved
            assert store.record(3)["text"] == "Дверь закрыта.", store.record(3)
            assert store.record(4)["text"] == "Окно открыто.", store.record(4)
            return
        controller.configure(
            {"enabled": True, "model": "neutral-fixture", "context": 65536}
        )
        # Seed a normal independent generation. The old EXE loads it from active.json.
        manager = PluginManager(store)
        manager.dispose()
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
                str(ROOT / "tools/hermes_resolution_probe.js"),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 75
        while not (report.exists() and report.stat().st_size):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Hidden plugin UI test did not complete")
            time.sleep(0.25)
        result = json.loads(report.read_text(encoding="utf-8"))
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        result["fixture_not_foreground"] = pid.value != process.pid
        (ROOT / "reports/hermes-resolution-ui.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        assert result.get("passed") and result["fixture_not_foreground"], result
        try:
            capture = inspect_app(store.path, True)
            (ROOT / "reports/hermes-resolution-ui.png").write_bytes(
                base64.b64decode(capture["image"]["data"])
            )
        except RuntimeError as error:
            print("Screenshot: " + str(error))
    finally:
        if controller and controller.busy:
            controller.cancel()
            deadline = time.monotonic() + 25
            while controller.busy and time.monotonic() < deadline:
                time.sleep(0.25)
            if controller.busy:
                raise RuntimeError("Owned neutral plugin still busy; fixture retained")
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid cleanup target")
        for path in fixture.rglob("*"):
            guard(path)
        for attempt in range(80):
            try:
                shutil.rmtree(fixture)
                break
            except (PermissionError, FileNotFoundError, OSError):
                if not fixture.exists():
                    break
                if attempt == 79:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
