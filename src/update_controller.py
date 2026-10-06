"""Desktop update orchestration, isolated from translation and HTTP routes."""

import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import sys
from pathlib import Path
from paths import APP_HOME
from update_feed import latest, download
from update_package import extract_bundle
from version import VERSION, REPOSITORY

PREFERENCES = {"auto_check": False, "auto_download": False, "auto_install": False}


class UpdateController:
    def __init__(self, store, closer, home=APP_HOME, frozen=None):
        self.store = store
        self.closer = closer
        self.home = Path(home).resolve()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.path = store.path.with_name("update_preferences.json")
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.preferences = dict(PREFERENCES)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.preferences.update(
                {
                    key: saved[key]
                    for key in PREFERENCES
                    if isinstance(saved.get(key), bool)
                }
            )
        except (OSError, ValueError, TypeError):
            pass
        self.state = {
            "phase": "idle",
            "current": VERSION,
            "repository": REPOSITORY,
            "message": "Проверка обновлений ещё не выполнялась",
            "downloaded": 0,
            "total": 0,
            "supported": self.frozen
            and store.path.resolve().is_relative_to(self.home / "data"),
        }
        self.release = None
        self.session = None

    def snapshot(self):
        with self.lock:
            return {**self.state, "preferences": dict(self.preferences)}

    def configure(self, values):
        if set(values) != set(PREFERENCES) or any(
            not isinstance(value, bool) for value in values.values()
        ):
            raise ValueError("Некорректные настройки обновлений")
        with self.lock:
            self.preferences = dict(values)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values), encoding="utf-8")
            temporary.replace(self.path)
        return self.snapshot()

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def _launch(self, phase, action):
        with self.lock:
            if self.state["phase"] in {"checking", "downloading", "installing"}:
                raise ValueError("Операция обновления уже выполняется")
            self.state.update(
                phase=phase,
                message={
                    "checking": "Проверяем GitHub Releases…",
                    "downloading": "Скачиваем и проверяем сборку…",
                    "installing": "Сохраняем очередь и готовим установку…",
                }[phase],
            )

        def run():
            try:
                action()
            except Exception as error:
                self._set(phase="error", message=str(error))

        threading.Thread(target=run, daemon=True).start()
        return self.snapshot()

    def check(self):
        def action():
            release = latest()
            self.release = release
            self._set(
                phase="available" if release["available"] else "current",
                available=release["available"],
                latest=release["latest"],
                url=release["url"],
                notes=release["notes"],
                message=(
                    "Доступна новая версия " + release["latest"]
                    if release["available"]
                    else "Установлена актуальная версия"
                ),
                checked_at=time.time(),
            )
            if release["available"] and self.preferences["auto_download"]:
                self.fetch()

        return self._launch("checking", action)

    def fetch(self):
        if not self.release or not self.release["available"]:
            raise ValueError("Сначала проверьте наличие новой версии")
        if not self.state["supported"]:
            raise ValueError(
                "Автоустановка доступна в установленной Windows-сборке с базой в data"
            )

        def action():
            session = self.home / "data/updates" / uuid.uuid4().hex
            session.mkdir(parents=True)
            self.session = session
            archive = session / "release.zip"
            try:
                download(
                    self.release,
                    archive,
                    lambda done, total: self._set(downloaded=done, total=total),
                )
                extract_bundle(archive, session / "bundle", self.release["latest"])
            except Exception:
                from update_package import reject_links

                reject_links(session)
                shutil.rmtree(session)
                self.session = None
                raise
            finally:
                archive.unlink(missing_ok=True)
            self._set(
                phase="ready",
                message="Сборка проверена и готова к установке. Проекты и настройки сохранятся.",
            )

        return self._launch("downloading", action)

    def idle(self):
        with self.store.db() as db:
            active = db.execute(
                "SELECT 1 FROM jobs WHERE state IN ('running','queued') OR worker_active=1 LIMIT 1"
            ).fetchone()
        from mcp_server import connections

        return not active and not connections(self.store)

    def _dirty(self):
        return bool(
            self.closer.window.evaluate_js('typeof dirty!=="undefined" && dirty.size>0')
        )

    def install(self):
        if self.state["phase"] != "ready" or not self.session:
            raise ValueError("Сначала скачайте обновление")
        if self._dirty():
            raise ValueError("Сохраните или отмените ручные правки перед установкой")
        from mcp_server import connections

        if connections(self.store):
            raise ValueError("Отключите MCP-клиент перед установкой обновления")

        def action():
            resumed = []
            waiting = []
            helper_process = None
            self.closer.pending = True
            try:
                with self.store.launch_lock:
                    self.store.closing = True
                    with self.store.db() as db:
                        rows = [
                            dict(row)
                            for row in db.execute(
                                "SELECT * FROM jobs WHERE state IN ('running','queued') OR worker_active=1"
                            )
                        ]
                        resumed = [
                            row["id"]
                            for row in rows
                            if row["state"] in {"running", "queued"}
                        ]
                        waiting = [
                            row[0]
                            for row in db.execute(
                                "SELECT id FROM jobs WHERE state='waiting'"
                            )
                        ]
                        db.execute(
                            "UPDATE jobs SET state='paused' WHERE state IN ('running','queued')"
                        )
                        db.execute("UPDATE jobs SET state='held' WHERE state='waiting'")
                self._set(
                    message="Ждём завершения текущих запросов. Очереди сохранены…"
                )
                from updater import wait_for_exit
                from project_lifecycle import matches_worker

                # Process identity must be checked before waiting on a saved PID.
                for row in rows:
                    if row.get("pid"):
                        command = f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(row['pid'])}' | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
                        result = subprocess.run(
                            [
                                "powershell.exe",
                                "-NoProfile",
                                "-NonInteractive",
                                "-Command",
                                command,
                            ],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            timeout=12,
                        )
                        if result.returncode:
                            raise ValueError(
                                "Не удалось проверить завершение обработчика"
                            )
                        info = (
                            json.loads(result.stdout.lstrip("\ufeff"))
                            if result.stdout.strip()
                            else {}
                        )
                        if matches_worker(info, self.store, row["id"]):
                            wait_for_exit(row["pid"], 180)
                if self._dirty():
                    raise ValueError(
                        "Появились несохранённые правки; установка отложена"
                    )
                backup = (
                    self.home
                    / "data/backups"
                    / (
                        "auto-update-"
                        + time.strftime("%Y%m%d_%H%M%S")
                        + "-"
                        + self.session.name[:8]
                    )
                )
                backup.mkdir(parents=True)
                with (
                    self.store.db() as source,
                    sqlite3.connect(backup / "studio.sqlite3") as target,
                ):
                    source.backup(target)
                plan = {
                    "home": str(self.home),
                    "database": str(self.store.path.resolve()),
                    "bundle": str(self.session / "bundle"),
                    "backup": str(backup),
                    "version": self.release["latest"],
                    "gui_pid": os.getpid(),
                    "resume_jobs": resumed,
                    "waiting_jobs": waiting,
                }
                plan_path = self.session / "plan.json"
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                helper = self.session / "LocalizationUpdater.exe"
                shutil.copy2(self.session / "bundle/LocalizationUpdater.exe", helper)
                helper_process = subprocess.Popen(
                    [str(helper), "--plan", str(plan_path)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                )
                self.closer.allow_exit = True
                self.closer.window.destroy()
            except Exception:
                if helper_process and helper_process.poll() is None:
                    helper_process.terminate()
                    helper_process.wait(timeout=10)
                self.closer.allow_exit = False
                self.store.closing = False
                # Do not revive a worker that is still completing its request.
                from job_runtime import launch

                with self.store.db() as db:
                    for jid in waiting:
                        db.execute(
                            "UPDATE jobs SET state='waiting' WHERE id=? AND state='held'",
                            (jid,),
                        )
                    for jid in resumed:
                        active = db.execute(
                            "SELECT worker_active FROM jobs WHERE id=? AND state='paused'",
                            (jid,),
                        ).fetchone()
                        if active and active[0]:
                            db.execute(
                                "UPDATE jobs SET state='running' WHERE id=?", (jid,)
                            )
                for jid in resumed:
                    with self.store.db() as db:
                        row = db.execute(
                            "SELECT worker_active FROM jobs WHERE id=? AND state='paused'",
                            (jid,),
                        ).fetchone()
                    if row and not row[0]:
                        launch(self.store, jid)
                raise
            finally:
                self.closer.pending = False

        return self._launch("installing", action)

    def initialize(self):
        def monitor():
            if self.preferences["auto_check"]:
                try:
                    self.check()
                except ValueError:
                    pass
            while not self.stop.wait(10):
                try:
                    if (
                        self.preferences["auto_install"]
                        and self.state["phase"] == "ready"
                        and self.idle()
                        and not self._dirty()
                    ):
                        self.install()
                except Exception as error:
                    self._set(phase="error", message=str(error))

        threading.Thread(target=monitor, daemon=True).start()

    def dispose(self):
        self.stop.set()


def resume_after_update(store, plan_path, home=APP_HOME):
    from update_installer import read_plan

    plan = read_plan(plan_path)
    if (
        Path(plan["home"]).resolve() != Path(home).resolve()
        or Path(plan["database"]).resolve() != store.path.resolve()
    ):
        raise ValueError("План относится к другому приложению")
    from commands.jobs import control

    with store.db() as db:
        for jid in plan.get("waiting_jobs", []):
            db.execute(
                "UPDATE jobs SET state='waiting' WHERE id=? AND state='held'", (jid,)
            )
    for jid in list(plan["resume_jobs"]):
        with store.db() as db:
            row = db.execute(
                "SELECT state,heartbeat FROM jobs WHERE id=?", (jid,)
            ).fetchone()
        if not row or row["state"] not in {"paused", "held"}:
            continue
        delay = max(0, 10 - (time.time() - row["heartbeat"]))
        if delay:
            time.sleep(delay)
        control(store, {"id": jid, "mode": "resume", "preserve_project_settings": True})
        plan["resume_jobs"].remove(jid)
        Path(plan_path).write_text(
            json.dumps(
                {key: value for key, value in plan.items() if not key.startswith("_")}
            ),
            encoding="utf-8",
        )
    plan["resume_jobs"] = []
    Path(plan_path).write_text(
        json.dumps(
            {key: value for key, value in plan.items() if not key.startswith("_")}
        ),
        encoding="utf-8",
    )
    result_path = Path(plan_path).with_name("result.json")
    if result_path.exists() and json.loads(result_path.read_text(encoding="utf-8")).get(
        "installed"
    ):
        from update_package import reject_links

        bundle = Path(plan["bundle"])
        reject_links(bundle)
        if (
            bundle.is_relative_to(Path(home).resolve() / "data/updates")
            and bundle.name == "bundle"
        ):
            shutil.rmtree(bundle)
