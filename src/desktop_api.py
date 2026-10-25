"""Small native bridge; controllers remain private to avoid COM traversal."""

import webbrowser

from close_behavior import CloseApi
from version import REPOSITORY


class DesktopApi(CloseApi):
    def __init__(self, closer, updates, hermes=None, manager=None):
        super().__init__(closer)
        self._updates = updates
        self._manager = manager
        self._store = manager.store if manager else hermes.store if hermes else None
        self._hermes = hermes
        from plugin_catalog import PluginCatalog

        self._plugins = (
            PluginCatalog(self._store, hermes, manager)
            if self._store is not None
            else None
        )

    def get_plugins(self):
        return self._plugins.snapshot()

    def set_plugin_enabled(self, identity, enabled):
        return self._plugins.set_enabled(identity, enabled)

    def set_plugin_favorite(self, identity, favorite):
        return self._plugins.set_favorite(identity, favorite)

    def configure_plugin(self, identity, values):
        return self._plugins.configure(identity, values)

    def get_hermes_state(self, project=0):
        return self._agent_controller().snapshot(int(project))

    def _agent_controller(self):
        return (
            self._manager.entry("hermes")["provider"] if self._manager else self._hermes
        )

    def configure_hermes(self, values):
        return self._agent_controller().configure(values)

    def get_hermes_models(self):
        return self._agent_controller().models.listing()

    def prepare_hermes_model(self, model, context):
        return self._agent_controller().prepare_model(model, context)

    def install_hermes(self):
        return self._agent_controller().install()

    def check_hermes(self):
        return self._agent_controller().check()

    def cancel_hermes(self):
        return self._agent_controller().cancel()

    def run_hermes(self, project, mode):
        return self._agent_controller().run(int(project), mode)

    def choose_plugin_package(self):
        from webview import FileDialog

        paths = self._controller.window.create_file_dialog(
            FileDialog.OPEN, allow_multiple=False, file_types=("Plugin ZIP (*.zip)",)
        )
        return self.preview_plugin_package(paths[0]) if paths else None

    def preview_plugin_package(self, path):
        import hashlib
        from pathlib import Path
        from plugin_package import read_package

        path = Path(path)
        value, _ = read_package(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self._pending_plugin = (path, digest)
        return {key: value[key] for key in ["id", "name", "version", "permissions"]}

    def install_plugin_package(self):
        import hashlib

        pending = getattr(self, "_pending_plugin", None)
        if pending is None:
            raise ValueError("Сначала выберите пакет")
        path, digest = pending
        self._pending_plugin = None
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Пакет изменился после проверки")
        self._manager.install(path)
        return self.get_plugins()

    def set_automatic_retry(self, project, enabled):
        from recovery_api import recovery

        return recovery(self._store, int(project), enabled=enabled)

    def start_smart_retry(self, project):
        from recovery_api import recovery

        return recovery(self._store, int(project), start=True)

    def get_recovery_state(self, project=0):
        from recovery_api import recovery

        return recovery(self._store, int(project))

    def plugin_call(self, identity, command, args):
        return self._manager.call(identity, command, args)

    def check_plugin_updates(self, identity):
        self._manager.check_update(identity)
        return self.get_plugins()

    def update_plugin(self, identity):
        self._manager.update(identity)
        return self.get_plugins()

    def rollback_plugin(self, identity):
        self._manager.rollback(identity)
        return self.get_plugins()

    def get_update_state(self):
        return self._updates.snapshot()

    def check_updates(self):
        return self._updates.check()

    def download_update(self):
        return self._updates.fetch()

    def install_update(self):
        return self._updates.install()

    def set_update_preferences(self, values):
        return self._updates.configure(values)

    def open_project_link(self, destination):
        paths = {
            "issues": "/issues/new/choose",
            "source": "",
            "guide": "/blob/main/README.md",
        }
        if destination not in paths:
            raise ValueError("Неизвестная ссылка приложения")
        return webbrowser.open("https://github.com/" + REPOSITORY + paths[destination])
