"""Small native bridge; controllers remain private to avoid COM traversal."""

import webbrowser

from close_behavior import CloseApi
from version import REPOSITORY


class DesktopApi(CloseApi):
    def __init__(self, closer, updates, hermes=None):
        super().__init__(closer)
        self._updates = updates
        self._hermes = hermes
        from plugin_catalog import PluginCatalog

        self._plugins = (
            PluginCatalog(hermes.store, hermes) if hermes is not None else None
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
        return self._hermes.snapshot(int(project))

    def configure_hermes(self, values):
        return self._hermes.configure(values)

    def install_hermes(self):
        return self._hermes.install()

    def check_hermes(self):
        return self._hermes.check()

    def cancel_hermes(self):
        return self._hermes.cancel()

    def run_hermes(self, project, mode):
        return self._hermes.run(int(project), mode)

    def set_automatic_retry(self, project, enabled):
        return self._hermes.recovery(int(project), enabled=enabled)

    def start_smart_retry(self, project):
        return self._hermes.recovery(int(project), start=True)

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
