"""Application plugin metadata and quick controls, independent of project actions."""

import json
import threading


class PluginCatalog:
    def __init__(self, store, hermes):
        self.path = store.path.resolve().parent / "plugins/catalog.json"
        self.lock = threading.RLock()
        self.entries = {
            "hermes": {
                "provider": hermes,
                "name": "Hermes Agent",
                "description": "Исследование игровых файлов и разбор ошибок через локальную модель",
                "category": "ИИ-помощники",
                "version": "0.22.5",
            }
        }
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("Некорректные настройки каталога")
            self.favorites = set(
                item for item in saved.get("favorites", []) if item in self.entries
            )
        except (OSError, TypeError, ValueError):
            self.favorites = set()

    def _entry(self, identity):
        if not isinstance(identity, str) or identity not in self.entries:
            raise ValueError("Плагин не найден")
        return self.entries[identity]

    def snapshot(self):
        with self.lock:
            return {
                "plugins": [
                    {
                        "id": identity,
                        **{
                            key: value
                            for key, value in entry.items()
                            if key != "provider"
                        },
                        **entry["provider"].summary(),
                        "favorite": identity in self.favorites,
                    }
                    for identity, entry in self.entries.items()
                ]
            }

    def set_enabled(self, identity, enabled):
        self._entry(identity)["provider"].set_enabled(enabled)
        return self.snapshot()

    def configure(self, identity, values):
        self._entry(identity)["provider"].configure_settings(values)
        return self.snapshot()

    def set_favorite(self, identity, favorite):
        self._entry(identity)
        if type(favorite) is not bool:
            raise ValueError("Некорректное значение избранного")
        with self.lock:
            if any(
                path.is_symlink() or path.is_junction()
                for path in [self.path, *self.path.parents]
            ):
                raise ValueError("Настройки каталога не должны быть ссылкой")
            selected = (
                self.favorites | {identity} if favorite else self.favorites - {identity}
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"favorites": sorted(selected)}), encoding="utf-8"
            )
            temporary.replace(self.path)
            self.favorites = selected
        return self.snapshot()
