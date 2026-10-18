import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commands import dispatch
from core import Store
from process_view import preferences


class ProjectSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "settings.sqlite3")
        self.pid = self.store.project(str(self.root))["id"]

    def tearDown(self):
        self.temp.cleanup()

    def save(self, prefs, model="fixture"):
        with patch("commands.settings.secret", return_value=None):
            return dispatch(
                self.store,
                "settings",
                {
                    "project": self.pid,
                    "settings": {
                        "model": model,
                        "source_language": "English",
                        "target_language": "Russian",
                    },
                    "preferences": prefs,
                },
            )

    def test_project_and_display_preferences_save_together(self):
        desired = {"max_phrases": 80, "max_seconds": 0, "page_size": 25}
        result = self.save(desired)
        self.assertEqual(result["preferences"], desired)
        self.assertEqual(preferences(self.store, self.pid), desired)
        with self.store.db() as db:
            saved = json.loads(
                db.execute(
                    "SELECT settings FROM projects WHERE id=?", (self.pid,)
                ).fetchone()[0]
            )
        self.assertEqual(saved["model"], "fixture")

    def test_invalid_display_preference_does_not_partially_save_project(self):
        self.save({"max_phrases": 80, "max_seconds": 0, "page_size": 25})
        with self.store.db() as db:
            before = db.execute(
                "SELECT settings FROM projects WHERE id=?", (self.pid,)
            ).fetchone()[0]
        with self.assertRaises(ValueError):
            self.save({"page_size": 500}, model="must-not-be-saved")
        with self.store.db() as db:
            after = db.execute(
                "SELECT settings FROM projects WHERE id=?", (self.pid,)
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(preferences(self.store, self.pid)["page_size"], 25)

    def test_display_preferences_remain_project_scoped(self):
        other = self.root / "other"
        other.mkdir()
        other_pid = self.store.project(str(other))["id"]
        self.save({"max_phrases": 80, "max_seconds": 0, "page_size": 25})
        self.assertEqual(preferences(self.store, other_pid)["page_size"], 50)


if __name__ == "__main__":
    unittest.main()
