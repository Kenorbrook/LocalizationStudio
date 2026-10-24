"""Verify background EXE workers, crash status and cached resume on a neutral corpus."""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from app_inspection import discovery_path
from inspection_client import inspect_app
from project_lifecycle import stop_worker
from worker_supervision import process_alive
from update_package import reject_links


def until(action, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = action()
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError("Fixture condition timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="store_true")
    args = parser.parse_args()
    fixture = ROOT / "qa-knowledge" / ("worker-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    project = store.project(str(fixture))["id"]
    corpus = fixture / "corpus.json"
    corpus.write_text(json.dumps([{"source": "Hello."}, {"source": "Goodbye."}]))
    store.import_files(project, [corpus])
    settings = {
        "model": "fixture",
        "source_language": "English",
        "target_language": "Russian",
        "context": 8192,
        "max_output": 1200,
        "mcp_session": "worker-fixture",
        "auto_foreign": False,
    }
    bundle = ROOT / "release/LocalizationStudio"
    command = (
        [sys.executable, str(ROOT / "src/desktop.py")]
        if args.source
        else [str(bundle / "LocalizationStudio.exe")]
    )
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    foreground = user32.GetForegroundWindow()
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    probe = fixture / "probe.json"
    gui = subprocess.Popen(
        [
            *command,
            "--db",
            str(store.path),
            "--hidden",
            "--smoke-report",
            str(probe),
            "--ui-test-script",
            str(ROOT / "tools/worker_ui_probe.js"),
        ]
    )
    jid = None
    checks = {}

    def job():
        with store.db() as db:
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())

    def sampling():
        with store.db() as db:
            db.execute(
                "UPDATE mcp_connections SET heartbeat=? WHERE session='worker-fixture'",
                (time.time(),),
            )
            row = db.execute(
                "SELECT * FROM mcp_requests WHERE job=? AND state='pending' ORDER BY created DESC LIMIT 1",
                (jid,),
            ).fetchone()
        return dict(row) if row else None

    def respond(row, malformed=False):
        items = json.loads(
            json.loads(row["request"])["messages"][0]["content"]["text"]
        )["items"]
        translations = [
            {
                "id": item["id"],
                "text": "Привет." if item["english"] == "Hello." else "До свидания.",
            }
            for item in items
        ]
        response = {
            "model": "deterministic-offline-fixture",
            "content": {
                "type": "text",
                "text": json.dumps({"items": translations}, ensure_ascii=False),
            },
        }
        if malformed:
            response["content"]["text"] = '{"error":"neutral failed inference fixture"}'
        with store.db() as db:
            db.execute(
                "UPDATE mcp_requests SET state='done',response=? WHERE id=? AND state='pending'",
                (json.dumps(response, ensure_ascii=False), row["id"]),
            )

    def stop_fixture_worker():
        if jid:
            current = job()
            if current["pid"] and process_alive(current["pid"]) is True:
                with patch(
                    "project_lifecycle.APP_HOME", ROOT if args.source else bundle
                ):
                    if not stop_worker(store, current):
                        raise RuntimeError(
                            "Could not verify and stop the fixture worker"
                        )

    try:

        def ready():
            if gui.poll() is not None:
                raise RuntimeError(
                    f"Fixture GUI exited before it was ready (code {gui.returncode})"
                )
            try:
                return json.loads(probe.read_text()).get("passed")
            except (OSError, ValueError):
                return False

        until(ready, 70)
        endpoint = json.loads(discovery_path(store.path).read_text())
        token = store.path.with_name("server.token").read_text().strip()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def api(name, data):
            request = urllib.request.Request(
                f"http://127.0.0.1:{endpoint['port']}/api/{name}",
                data=json.dumps(data).encode(),
                headers={"X-Studio-Token": token, "Content-Type": "application/json"},
            )
            with opener.open(request, timeout=15) as response:
                return json.load(response)

        with store.db() as db:
            db.execute(
                "INSERT INTO mcp_connections(session,pid,client,version,heartbeat,connected,sampling) VALUES (?,?,?,?,?,1,1)",
                (
                    "worker-fixture",
                    os.getpid(),
                    "neutral worker fixture",
                    "1",
                    time.time(),
                ),
            )
        jid = api(
            "job",
            {
                "project": project,
                "stage": "translate",
                "provider": "mcp",
                "settings": settings,
            },
        )["job"]
        first = until(sampling)
        first_pid = job()["pid"]
        checks["direct_worker_pid"] = bool(first_pid) and first_pid != gui.pid

        def fixture_is_not_foreground():
            active_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(
                user32.GetForegroundWindow(), ctypes.byref(active_pid)
            )
            # The user can switch unrelated windows while this hidden test is running.
            return active_pid.value not in {
                gui.pid,
                endpoint["pid"],
                first_pid,
                job()["pid"],
            }

        checks["launch_did_not_activate_fixture"] = fixture_is_not_foreground()
        respond(first)
        until(lambda: job()["done"] == 1)
        until(sampling)
        with store.db() as db:
            saved = tuple(
                db.execute(
                    "SELECT id,text,revision FROM records ORDER BY id LIMIT 1"
                ).fetchone()
            )
        stop_fixture_worker()
        until(lambda: job()["state"] == "paused" and not job()["worker_active"], 8)
        checks["exit_detected_promptly"] = True
        checks["crash_not_shown_as_running"] = (
            job()["done"] == 1 and "завершился" in job()["error"]
        )
        with store.db() as db:
            checks["unfinished_queue_preserved"] = (
                db.execute(
                    "SELECT count(*) FROM queue WHERE job=? AND state='pending'", (jid,)
                ).fetchone()[0]
                == 1
            )
            checks["orphan_sampling_stopped"] = (
                db.execute(
                    "SELECT count(*) FROM mcp_requests WHERE job=? AND state IN ('pending','sent')",
                    (jid,),
                ).fetchone()[0]
                == 0
            )

        def paused_ui():
            snap = inspect_app(store.path)
            return snap if "завершился" in snap["ui"]["text"] else None

        paused = until(paused_ui, 10)
        checks["ui_explains_pause"] = "Продолжить" in paused["ui"]["text"]
        checks["ui_no_false_model_working_message"] = (
            "Модель работает…" not in paused["ui"]["text"]
            and "Результат этой строки ещё не сохранён." in paused["ui"]["text"]
        )
        picture = inspect_app(store.path, True).pop("image")
        import base64

        (ROOT / "reports/worker-paused.png").write_bytes(
            base64.b64decode(picture["data"])
        )
        api("control", {"id": jid, "mode": "resume", "preserve_project_settings": True})
        respond(until(sampling))
        until(lambda: job()["state"] == "done" and not job()["worker_active"])
        with store.db() as db:
            checks["saved_translation_unchanged"] = (
                tuple(
                    db.execute(
                        "SELECT id,text,revision FROM records ORDER BY id LIMIT 1"
                    ).fetchone()
                )
                == saved
            )
            checks["resume_finished_remaining_only"] = (
                job()["done"] == 2
                and db.execute(
                    "SELECT count(*) FROM records WHERE status='translated'"
                ).fetchone()[0]
                == 2
            )
        checks["resume_did_not_activate_fixture"] = fixture_is_not_foreground()
        # A record failure must remain reviewable while the following record still runs.
        extra = fixture / "errors.json"
        extra.write_text(
            json.dumps([{"source": "Broken request."}, {"source": "Goodbye."}])
        )
        store.import_files(project, [extra])
        jid = api(
            "job",
            {
                "project": project,
                "stage": "translate",
                "provider": "mcp",
                "settings": settings,
            },
        )["job"]
        for _ in range(2):
            respond(until(sampling), malformed=True)
        respond(until(sampling))
        until(lambda: job()["state"] == "incomplete" and not job()["worker_active"])
        with store.db() as db:
            rows = [
                tuple(row)
                for row in db.execute(
                    "SELECT status,text FROM records WHERE file=(SELECT id FROM files WHERE path=?) ORDER BY position",
                    (str(extra),),
                )
            ]
            checks["failed_record_does_not_stop_following_record"] = rows == [
                ("empty", ""),
                ("translated", "До свидания."),
            ]
            checks["failed_record_is_reviewable"] = (
                db.execute(
                    "SELECT count(*) FROM errors WHERE job=? AND resolved=0 AND budget_json<>'{}'",
                    (jid,),
                ).fetchone()[0]
                == 1
            )
        checks["incomplete_is_not_claimed_as_done"] = (
            job()["done"] == 2 and job()["error"] == "1 строк требуют внимания"
        )
        result = {
            "passed": all(checks.values()),
            "checks": checks,
            "source": args.source,
            "database": "disposable neutral corpus; no model requests",
            "foreground_changed_outside_fixture": user32.GetForegroundWindow()
            != foreground,
        }
        (ROOT / "reports/worker-lifecycle-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        assert result["passed"], checks
    finally:
        stop_fixture_worker()
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=15)
        if fixture.resolve().parent != (ROOT / "qa-knowledge").resolve():
            raise RuntimeError("Unexpected fixture path")
        reject_links(fixture)
        for attempt in range(60):
            try:
                shutil.rmtree(fixture)
                break
            except PermissionError:
                if attempt == 59:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
