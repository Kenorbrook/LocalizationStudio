"""Independent plugin generations, explicit updates, rollback and UI assets."""

import hashlib
import json
import threading
from pathlib import Path

from plugin_backend import load_backend, seed_root
from plugin_package import (
    atomic_json,
    guard,
    identity,
    manifest,
    read_package,
    safe_path,
)


class UiPlugin:
    def __init__(self, store, key):
        self.path = store.path.parent / "plugins" / key / "preferences.json"
        self.busy = False
        try:
            self.preferences = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.preferences = {"enabled": False}

    def summary(self):
        enabled = self.preferences.get("enabled") is True
        return dict(
            installed=True,
            enabled=enabled,
            busy=False,
            state="enabled" if enabled else "disabled",
            state_label="Включён" if enabled else "Отключён",
            attention=False,
            message="",
            can_toggle=True,
        )

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("Некорректный переключатель")
        values = {**self.preferences, "enabled": enabled}
        atomic_json(self.path, values)
        self.preferences = values

    def configure_settings(self, values):
        raise ValueError("У этого плагина нет дополнительных настроек")

    def dispose(self):
        pass


class UnavailablePlugin(UiPlugin):
    def __init__(self, store, key, error):
        super().__init__(store, key)
        self.error = str(error)

    def summary(self):
        return {
            **super().summary(),
            "enabled": False,
            "state": "error",
            "state_label": "Ошибка пакета",
            "attention": True,
            "message": self.error,
            "can_toggle": False,
        }


class PluginManager:
    def __init__(self, store):
        self.store = store
        self.root = store.path.resolve().parent / "plugin-packages"
        self.lock = threading.RLock()
        self.entries = {}
        self.updates = {}
        self.closed = False
        # Bundled packages seed the installation once; future app updates do not replace them.
        seeds = seed_root("hermes").parent
        if seeds.is_dir():
            for source in seeds.iterdir():
                if (source / "plugin.json").is_file():
                    key = identity(source.name)
                    if not (self.root / key / "active.json").exists():
                        self._seed(source)
        if self.root.exists():
            for path in self.root.iterdir():
                if path.is_dir() and (path / "active.json").is_file():
                    try:
                        key = identity(path.name)
                        self.entries[key] = self._load(
                            key, self._pointer(key)["active"]
                        )
                    except Exception as error:
                        try:
                            key = identity(path.name)
                        except ValueError:
                            continue
                        self.entries[key] = dict(
                            id=key,
                            name=key,
                            description="Пакет не удалось загрузить. Можно обновить или вернуть предыдущую версию.",
                            category="Требуют внимания",
                            version="0.0.0",
                            permissions=[],
                            generation="unavailable",
                            provider=UnavailablePlugin(store, key, error),
                            backend_module=None,
                            ui=None,
                        )
                        self.updates[key] = {"phase": "error", "message": str(error)}

    def _pointer(self, key):
        path = guard(self.root / identity(key) / "active.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def _seed(self, source):
        guard(source)
        value = manifest(
            json.loads((source / "plugin.json").read_text(encoding="utf-8"))
        )
        files = {}
        for path in source.rglob("*"):
            guard(path)
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            ):
                files[path.relative_to(source).as_posix()] = path.read_bytes()
        value["files"] = {
            name: hashlib.sha256(data).hexdigest()
            for name, data in files.items()
            if name != "plugin.json"
        }
        files["plugin.json"] = json.dumps(value, ensure_ascii=False).encode()
        generation = self._write(value, files)
        atomic_json(
            self.root / value["id"] / "active.json",
            {"active": generation, "previous": None},
        )

    def _write(self, value, files):
        digest = hashlib.sha256(files["plugin.json"]).hexdigest()[:20]
        generation = value["version"] + "-" + digest
        target = guard(self.root / value["id"] / generation)
        if not target.exists():
            target.mkdir(parents=True)
            for name, data in files.items():
                destination = guard(target / safe_path(name))
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        return generation

    def _load(self, key, generation):
        if (
            not isinstance(generation, str)
            or "/" in generation
            or "\\" in generation
            or generation in {".", ".."}
        ):
            raise ValueError("Некорректная версия установки")
        root = guard(self.root / identity(key) / generation)
        value = manifest(json.loads((root / "plugin.json").read_text(encoding="utf-8")))
        if value["id"] != key:
            raise ValueError("Другой идентификатор пакета")
        for name, digest in value["files"].items():
            path = guard(root / safe_path(name))
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Файлы плагина повреждены")
        backend = load_backend(root) if value.get("backend") else None
        controller = (
            backend.create(self.store) if backend else UiPlugin(self.store, key)
        )
        return {
            **value,
            "provider": controller,
            "backend_module": backend,
            "root": root,
            "generation": generation,
        }

    def entry(self, key):
        identity(key)
        if key not in self.entries:
            raise ValueError("Плагин не установлен")
        return self.entries[key]

    @property
    def busy(self):
        # Do not acquire the catalog lock under the worker launch lock.
        return any(
            entry["provider"].busy for entry in list(self.entries.values())
        ) or any(
            update.get("phase") in {"checking", "downloading"}
            for update in list(self.updates.values())
        )

    def metadata(self, key):
        entry = self.entry(key)
        try:
            pointer = self._pointer(key)
        except (OSError, ValueError):
            pointer = {}
        return {
            field: entry.get(field)
            for field in [
                "id",
                "name",
                "description",
                "category",
                "version",
                "permissions",
                "upstream_version",
                "generation",
            ]
        } | {
            "ui_url": (
                f"/plugins/{key}/{entry['generation']}/{entry['ui']}"
                if entry.get("ui")
                else None
            ),
            "rollback_available": bool(pointer.get("previous")),
            "update": self.updates.get(key, {"phase": "idle", "message": ""}),
        }

    def call(self, key, command, args):
        with self.lock:
            entry = self.entry(key)
            if not entry["backend_module"]:
                raise ValueError("У плагина нет команд")
            if not isinstance(args, list) or len(args) > 5:
                raise ValueError("Некорректные аргументы команды")
            return entry["backend_module"].invoke(entry["provider"], command, args)

    def asset(self, key, generation, name):
        with self.lock:
            entry = self.entry(key)
            if (
                generation != entry["generation"]
                or safe_path(name) not in entry["files"]
            ):
                raise ValueError("Ресурс плагина не найден")
            if Path(name).suffix not in {".js", ".css"}:
                raise ValueError("Недопустимый ресурс интерфейса")
            return guard(entry["root"] / name).read_text(encoding="utf-8")

    def install(self, package, expected=None, expected_version=None):
        value, files = read_package(package)
        key = value["id"]
        if expected_version and value["version"] != expected_version:
            raise ValueError("Версия пакета не совпадает с предложенным обновлением")
        if expected and key != expected:
            raise ValueError("Другой плагин в обновлении")
        with self.lock, self.store.launch_lock:
            if self.closed or getattr(self.store, "closing", False):
                raise ValueError("Приложение закрывается")
            old = self.entries.get(key)
            if old and old["provider"].busy:
                raise ValueError("Дождитесь завершения работы плагина")
            generation = self._write(value, files)
            if old and generation == old["generation"]:
                return
            previous = old["generation"] if old else None
            if previous == "unavailable":
                try:
                    previous = self._pointer(key).get("previous")
                except (OSError, ValueError):
                    previous = None
            loaded = self._load(key, generation)
            try:
                atomic_json(
                    self.root / key / "active.json",
                    {
                        "active": generation,
                        "previous": previous,
                    },
                )
            except BaseException:
                loaded["provider"].dispose()
                raise
            if old:
                old["provider"].dispose()
            self.entries[key] = loaded
            self.updates[key] = {
                "phase": "current",
                "message": "Пакет обновлён. Данные и настройки сохранены.",
            }

    def rollback(self, key):
        with self.lock, self.store.launch_lock:
            old = self.entry(key)
            if (
                old["provider"].busy
                or self.updates.get(key, {}).get("phase") == "downloading"
            ):
                raise ValueError("Дождитесь завершения работы плагина")
            previous = self._pointer(key).get("previous")
            if not previous:
                raise ValueError("Нет предыдущей версии")
            loaded = self._load(key, previous)
            try:
                atomic_json(
                    self.root / key / "active.json",
                    {
                        "active": previous,
                        "previous": (
                            old["generation"]
                            if old["generation"] != "unavailable"
                            else None
                        ),
                    },
                )
            except BaseException:
                loaded["provider"].dispose()
                raise
            old["provider"].dispose()
            self.entries[key] = loaded
            self.updates[key] = {
                "phase": "current",
                "message": "Возвращена предыдущая версия пакета",
            }

    def _background(self, key, phase, action):
        with self.lock, self.store.launch_lock:
            self.entry(key)
            if (
                self.closed
                or getattr(self.store, "closing", False)
                or self.updates.get(key, {}).get("phase")
                in {
                    "checking",
                    "downloading",
                }
            ):
                raise ValueError("Обновление уже выполняется")
            self.updates[key] = {
                "phase": phase,
                "message": (
                    "Проверяем обновления…"
                    if phase == "checking"
                    else "Скачиваем пакет плагина…"
                ),
            }

        def run():
            try:
                result = action()
                with self.lock:
                    if result:
                        self.updates[key] = result
            except Exception as error:
                with self.lock:
                    self.updates[key] = {"phase": "error", "message": str(error)}

        threading.Thread(target=run, daemon=True).start()

    def check_update(self, key):
        def action():
            from plugin_updates import latest

            return latest(key, self.entry(key)["version"])

        self._background(key, "checking", action)

    def update(self, key):
        with self.lock:
            offer = dict(self.updates.get(key, {}))
            if offer.get("phase") != "available":
                raise ValueError("Сначала проверьте обновления плагина")
            if self.entry(key)["provider"].busy:
                raise ValueError("Плагин занят")

        def action():
            from plugin_updates import download

            target = guard(self.root / key / "download.zip")
            download(offer, target)
            try:
                self.install(target, expected=key, expected_version=offer["version"])
            finally:
                target.unlink(missing_ok=True)
            return None

        self._background(key, "downloading", action)

    def dispose(self):
        with self.lock:
            self.closed = True
            for entry in self.entries.values():
                entry["provider"].dispose()
