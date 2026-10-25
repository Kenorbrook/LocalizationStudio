"""Explicit, resumable local Ren'Py preparation and build orchestration."""

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .filesystem import guard, tree, copy_game, owned, write_json
from .runtime import discover, command, environment
from .transfer import carry
from .build import build_plan, apply_plan, restore


class RenPyPlugin:
    def __init__(self, store):
        self.store = store
        self.root = store.path.resolve().parent / "plugins/renpy"
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.busy = False
        self.process = None
        self.thread = None
        self.status = {"phase": "idle", "message": "Модуль готов"}
        self.preferences = {"enabled": True}
        try:
            value = json.loads(
                (self.root / "preferences.json").read_text(encoding="utf-8")
            )
            if type(value.get("enabled")) is bool:
                self.preferences["enabled"] = value["enabled"]
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        try:
            self.index = json.loads(
                (self.root / "index.json").read_text(encoding="utf-8")
            )
            if not isinstance(self.index, dict):
                raise ValueError("Некорректный индекс")
        except (OSError, ValueError):
            self.index = {}

    def recover_startup(self):
        # Only the GUI owning the database mutex calls recovery.
        for project in set(self.index.values()):
            state = self.read(int(project))
            if state and state.get("active"):
                self.save(
                    {
                        **state,
                        "active": False,
                        "phase": "paused",
                        "message": "Работа прервана закрытием Studio. Очереди и копия сохранены.",
                    }
                )

    def read(self, project):
        source = int(self.index.get(str(int(project)), int(project)))
        try:
            return json.loads(
                guard(self.root / "projects" / str(source) / "state.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            return None

    def save(self, value):
        with self.lock:
            source = int(value["source_project"])
            write_json(self.root / "projects" / str(source) / "state.json", value)
            for project in [source, value.get("workspace_project")]:
                if project:
                    self.index[str(project)] = source
            write_json(self.root / "index.json", self.index)
            self.status = {
                key: value.get(key)
                for key in [
                    "phase",
                    "message",
                    "active",
                    "source_project",
                    "workspace_project",
                ]
            }

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Некорректный переключатель")
        with self.lock:
            write_json(self.root / "preferences.json", {"enabled": enabled})
            self.preferences["enabled"] = enabled

    def configure_settings(self, values):
        if values:
            raise ValueError("Параметры задаются в проекте игры")

    def summary(self):
        enabled = self.preferences["enabled"]
        return dict(
            installed=True,
            enabled=enabled,
            busy=self.busy,
            state="busy" if self.busy else "enabled" if enabled else "disabled",
            state_label=(
                "Работает" if self.busy else "Включён" if enabled else "Отключён"
            ),
            message=self.status.get("message", ""),
            can_toggle=True,
            attention=self.status.get("phase") == "error",
        )

    def project_root(self, project):
        with self.store.db() as db:
            row = db.execute(
                "SELECT root FROM projects WHERE id=? AND deleting=0", (int(project),)
            ).fetchone()
        if not row:
            raise ValueError("Проект не найден")
        return guard(row[0]).resolve()

    def idle(self, *projects):
        if getattr(self.store, "closing", False) or getattr(
            self.store, "hermes_inference", False
        ):
            raise ValueError(
                "Сначала завершите работу локального агента или закрытие приложения"
            )
        with self.store.db() as db:
            if db.execute(
                "SELECT 1 FROM jobs WHERE worker_active=1 OR state IN ('running','queued') LIMIT 1"
            ).fetchone():
                raise ValueError("Дождитесь завершения работающих задач Studio")
            for project in projects:
                if (
                    project
                    and db.execute(
                        "SELECT 1 FROM jobs WHERE project=? AND state IN ('paused','waiting','held') LIMIT 1",
                        (project,),
                    ).fetchone()
                ):
                    raise ValueError(
                        "Сначала продолжите или отмените незавершённую очередь проекта"
                    )

    def snapshot(self, project=0):
        state = self.read(project) if project else None
        result = dict(
            self.status,
            busy=self.busy,
            enabled=self.preferences["enabled"],
            project=project,
        )
        if state:
            result.update(
                {
                    key: state.get(key)
                    for key in [
                        "phase",
                        "message",
                        "copy",
                        "source_project",
                        "workspace_project",
                        "runtime",
                        "prepared",
                        "built",
                        "mode",
                        "transfer",
                        "progress",
                        "pipeline_stage",
                    ]
                }
            )
            result["rollback_available"] = bool(
                state.get("snapshot") and state.get("built")
            )
        return result

    def workflow(self, project):
        state = self.read(project)
        try:
            discover(self.project_root(state["source_project"] if state else project))
        except ValueError as error:
            return {
                "supported": False,
                "prepared": False,
                "built": False,
                "reason": str(error),
            }
        return {
            "supported": True,
            "prepared": bool(state and state.get("prepared")),
            "built": bool(state and state.get("built")),
            "busy": self.busy,
            "workspace_project": state.get("workspace_project") if state else None,
            "phase": state.get("phase") if state else "new",
            "reason": (
                state.get("message")
                if state
                else "Подготовьте отдельную копию и штатный слой перевода Ren’Py"
            ),
        }

    def inspect(self, project):
        state = self.read(project)
        source = self.project_root(state["source_project"] if state else project)
        found = discover(source)
        source = Path(found["root"])
        self.source_ready(source)
        paths = list(tree(source))
        return {
            **found,
            "files": len(paths),
            "bytes": sum(p.stat().st_size for p in paths),
            "destination": str(source.with_name(source.name + "-Studio-Russian")),
            "state": self.snapshot(project),
        }

    @staticmethod
    def source_ready(source):
        for relative in [
            ".studio-renpy-copy.json",
            "game/tl/studio_russian",
            "game/zz_studio_localization.rpy",
            "game/studio_localization",
        ]:
            if guard(Path(source) / relative).exists():
                raise ValueError(
                    "Папка уже содержит слой Studio или занято служебное имя. Выберите исходную распакованную игру"
                )

    def log(self, project):
        state = self.read(project)
        if not state:
            return "Подготовка ещё не запускалась"
        path = guard(
            self.root / "projects" / str(int(state["source_project"])) / "engine.log"
        )
        if not path.is_file():
            return "Журнал движка ещё не создан"
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            return stream.read().decode("utf-8", errors="replace")

    def launch_task(self, phase, project, action):
        with self.store.launch_lock, self.lock:
            if (
                not self.preferences["enabled"]
                or self.busy
                or getattr(self.store, "closing", False)
            ):
                raise ValueError("Модуль отключён, занят или Studio закрывается")
            self.idle(project)
            self.busy = True
            mutation = phase != "pipeline"
            if mutation:
                self.store.engine_mutation = self
            self.cancelled.clear()
            self.status = {
                "phase": phase,
                "message": "Подготавливаем операцию…",
                "project": project,
            }

        def run():
            try:
                action()
            except Exception as error:
                state = self.read(project)
                if state:
                    self.save(
                        {
                            **state,
                            "phase": "error",
                            "active": False,
                            "message": str(error),
                        }
                    )
                self.status = {
                    "phase": "error",
                    "message": str(error),
                    "project": project,
                }
            finally:
                with self.store.launch_lock:
                    if getattr(self.store, "engine_mutation", None) is self:
                        self.store.engine_mutation = None
                self.busy = False
                self.process = None

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return self.snapshot(project)

    def run_engine(self, root, home, task, *args):
        home = guard(home)
        home.mkdir(parents=True, exist_ok=True)
        log = home / "engine.log"
        argv = command(root, task, home / "saves", *args)
        with log.open("ab") as output:
            output.write(("\n--- " + task + " ---\n").encode())
            output.flush()
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=environment(home, root=root),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.process = process
            deadline = time.monotonic() + 900
            while process.poll() is None:
                if self.cancelled.wait(0.25) or time.monotonic() > deadline:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                    raise ValueError(
                        "Команда Ren’Py остановлена; подробности в журнале"
                    )
            if process.returncode:
                raise ValueError(
                    "Ren’Py завершился с ошибкой: " + task + ". Журнал: " + str(log)
                )
        self.process = None

    def prepare(self, project, destination):
        existing = self.read(project)
        source_project = existing["source_project"] if existing else int(project)
        source = Path(discover(self.project_root(source_project))["root"])
        self.source_ready(source)
        destination = str(destination).strip()
        self.idle(
            source_project, existing.get("workspace_project") if existing else None
        )

        def action():
            token = uuid.uuid4().hex
            state = dict(
                source_project=source_project,
                workspace_project=None,
                owner=token,
                copy=str(Path(destination).absolute()),
                prepared=False,
                built=False,
                active=True,
                phase="copying",
                message="Копируем игру; оригинал остаётся нетронутым",
            )
            # Validate destinations before replacing an earlier metadata record.
            from .filesystem import output_path

            _, target = output_path(source, destination)
            state["copy"] = str(target)
            self.save(state)
            home = self.root / "projects" / str(source_project)
            last = 0

            def progress(done, total, name):
                nonlocal last
                if time.monotonic() - last > 0.5 or done == total:
                    last = time.monotonic()
                    state.update(
                        progress={"done": done, "total": total},
                        message="Копируем: " + name,
                    )
                    self.save(state)

            manifest = copy_game(source, target, token, progress, self.cancelled)
            write_json(home / "source-manifest.json", manifest)
            state.update(
                phase="extracting", message="Ren’Py создаёт штатный слой перевода…"
            )
            self.save(state)
            self.run_engine(target, home, "translate", "studio_russian", "--empty")
            self.idle(source_project)
            from extraction import extract

            templates = []
            folder = target / "game/tl/studio_russian"
            if not folder.is_dir():
                raise ValueError("Движок не создал слой перевода")
            for path in tree(folder):
                if path.suffix == ".rpy" and extract(path)[0]:
                    templates.append(path)
            if not templates:
                raise ValueError("Не найдены переводимые строки штатного слоя")
            child = self.store.project(str(target))["id"]
            state["workspace_project"] = child
            self.save(state)
            result = self.store.import_files(child, templates)
            if result["errors"]:
                raise ValueError(
                    "Ошибка импорта подготовленного слоя: "
                    + result["errors"][0]["message"]
                )
            transfer = carry(self.store, source_project, child)
            with self.store.db() as db:
                settings = db.execute(
                    "SELECT settings FROM projects WHERE id=?", (source_project,)
                ).fetchone()[0]
                db.execute(
                    "UPDATE projects SET settings=?,keep_visible=1 WHERE id=?",
                    (settings, child),
                )
            from game_detection import inspect_project

            inspect_project(self.store, child)
            state.update(
                prepared=True,
                active=False,
                phase="prepared",
                runtime=discover(target)["version"],
                transfer=transfer,
                message=f"Копия подготовлена. Добавлено строк: {result['added']}; перенесено переводов: {transfer['transferred']}; неоднозначных: {transfer['ambiguous']}",
            )
            self.save(state)

        return self.launch_task("copying", source_project, action)

    def build(self, project, mode="menu"):
        state = self.read(project)
        if not state or not state.get("prepared"):
            raise ValueError("Сначала подготовьте копию игры")
        child = state["workspace_project"]
        self.idle(child)
        return self.launch_task(
            "building", project, lambda: self.build_now(child, state, mode)
        )

    def build_now(self, project, state, mode):
        with self.store.launch_lock:
            self.idle(project)
            self.store.engine_mutation = self
        try:
            return self._build_now(project, state, mode)
        finally:
            with self.store.launch_lock:
                if getattr(self.store, "engine_mutation", None) is self:
                    self.store.engine_mutation = None

    def _build_now(self, project, state, mode):
        previous_build = {
            key: state.get(key)
            for key in ["built", "mode", "snapshot", "built_hashes", "verification"]
        }
        root = owned(state["copy"], state["owner"])
        from .runtime import require_not_running

        require_not_running(root)
        home = self.root / "projects" / str(state["source_project"])
        state.update(
            active=True,
            phase="building",
            message="Собираем переведённый слой и переключение языка…",
        )
        self.save(state)
        output = build_plan(self.store, project, state, home, mode)
        snapshot, hashes = apply_plan(root, output, home)
        try:
            self.run_engine(root, home, "compile", "--keep-orphan-rpyc")
            self.run_engine(root, home, "lint", "--error-code")
            from .verification import expectation

            expected = expectation(self.store, project, mode, home)
            result = home / "verify-result.json"
            result.unlink(missing_ok=True)
            self.run_engine(
                root,
                home,
                "studio_verify",
                "--expected",
                str(expected),
                "--result",
                str(result),
            )
            verified = json.loads(result.read_text(encoding="utf-8"))
            if not verified.get("font") or not verified.get("checked"):
                raise ValueError("Не подтверждена загрузка перевода движком")
        except Exception:
            restore(root, snapshot, hashes)
            raise
        state.update(
            active=False,
            phase="built",
            built=True,
            mode=mode,
            snapshot=snapshot,
            built_hashes=hashes,
            verification=verified,
            previous_build=previous_build,
            message="Копия собрана. Проверены компиляция, lint, загрузка переводов, переключение языка и шрифт. Запустите копию для проверки сцен и внешнего вида.",
        )
        self.save(state)

    def rollback(self, project):
        with self.store.launch_lock, self.lock:
            if not self.preferences["enabled"]:
                raise ValueError("Модуль отключён")
            return self._rollback(project)

    def _rollback(self, project):
        state = self.read(project)
        if not state or not state.get("snapshot") or self.busy:
            raise ValueError("Нет доступного отката или модуль занят")
        self.idle(state["workspace_project"])
        root = owned(state["copy"], state["owner"])
        from .runtime import require_not_running

        require_not_running(root)
        snapshot = guard(state["snapshot"])
        if not snapshot.resolve().is_relative_to(self.root.resolve() / "projects"):
            raise ValueError("Некорректная резервная копия")
        restore(root, snapshot, state["built_hashes"])
        previous = state.get("previous_build") or {}
        state.update(
            **{
                key: previous.get(key)
                for key in ["mode", "built_hashes", "verification"]
            },
            built=bool(previous.get("built")),
            phase="built" if previous.get("built") else "prepared",
            snapshot=None,
            previous_build=None,
            message="Последняя сборка отменена. Переводы в Studio сохранены.",
        )
        self.save(state)
        return self.snapshot(project)

    def launch_game(self, project, language):
        state = self.read(project)
        if (
            self.busy
            or not self.preferences["enabled"]
            or not state
            or not state.get("built")
            or language not in {"russian", "original"}
        ):
            raise ValueError("Сначала соберите копию игры")
        root = owned(state["copy"], state["owner"])
        home = self.root / "projects" / str(state["source_project"])
        args = command(root, "run", guard(root / "game/saves"))
        with (home / "play.log").open("ab") as log:
            process = subprocess.Popen(
                args,
                cwd=root,
                env=environment(
                    home,
                    root=root,
                    headless=False,
                    language=language,
                ),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return {"launched": True, "pid": process.pid, "copy": str(root)}

    def start_pipeline(self, project, values, mode="menu"):
        state = self.read(project)
        if not state or not state.get("prepared"):
            raise ValueError("Сначала подготовьте копию игры")
        child = state["workspace_project"]
        self.idle(child)
        values = {**values, "provider": "local", "execution_kind": "local"}

        def action():
            from commands.jobs import job

            def counts():
                with self.store.db() as db:
                    return dict(
                        db.execute(
                            "SELECT r.status,count(*) FROM records r JOIN files f ON f.id=r.file WHERE f.project=? AND r.manual=0 GROUP BY r.status",
                            (child,),
                        ).fetchall()
                    )

            for stage in ["translate", "review"]:
                count = counts().get(
                    "empty" if stage == "translate" else "translated", 0
                )
                if not count:
                    continue
                if self.cancelled.is_set() or not self.preferences["enabled"]:
                    raise ValueError(
                        "Цикл приостановлен. Уже выполненные переводы сохранены"
                    )
                self.idle(child)
                started = job(
                    self.store,
                    {
                        "project": child,
                        "provider": "local",
                        "stage": stage,
                        "settings": values,
                        "persist_settings": False,
                    },
                )
                target = started.get("translation_job") or started["job"]
                state.update(
                    active=True,
                    phase="pipeline",
                    pipeline_stage=stage,
                    message="Локальный цикл: "
                    + ("перевод" if stage == "translate" else "редактура"),
                    pipeline_job=target,
                )
                self.save(state)
                while True:
                    if self.cancelled.wait(0.5) or not self.preferences["enabled"]:
                        raise ValueError(
                            "Продолжение цикла остановлено; текущая очередь управляется в Studio"
                        )
                    with self.store.db() as db:
                        current = db.execute(
                            "SELECT state,worker_active FROM jobs WHERE id=?", (target,)
                        ).fetchone()
                        paused = db.execute(
                            "SELECT 1 FROM jobs WHERE project=? AND state IN ('paused','held') LIMIT 1",
                            (child,),
                        ).fetchone()
                    if (
                        paused
                        or not current
                        or current["state"]
                        in {"incomplete", "cancelled", "error", "failed"}
                    ):
                        raise ValueError(
                            "Цикл остановлен на очереди. Разберите ошибки или продолжите её в Studio, затем запустите цикл снова"
                        )
                    if current["state"] == "done" and not current["worker_active"]:
                        break
            self.idle(child)
            self.build_now(child, state, mode)

        return self.launch_task("pipeline", project, action)

    def cancel(self):
        self.cancelled.set()
        return {
            "message": "Останавливаем операцию модуля. Очереди перевода управляются в Studio"
        }

    def dispose(self):
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
