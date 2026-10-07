"""Small native bridge; controllers remain private to avoid COM traversal."""

import webbrowser

from close_behavior import CloseApi
from version import REPOSITORY


class DesktopApi(CloseApi):
    def __init__(self, closer, updates):
        super().__init__(closer)
        self._updates = updates

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
