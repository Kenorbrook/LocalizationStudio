"""Small native bridge; controllers remain private to avoid COM traversal."""

from close_behavior import CloseApi


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
