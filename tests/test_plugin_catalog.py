import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from hermes_install import COMMIT
from hermes_plugin import HermesPlugin
from plugin_catalog import PluginCatalog


class PluginCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = Store(Path(temporary.name) / "data/test.sqlite3")
        self.store.launch_lock = threading.RLock()
        self.hermes = HermesPlugin(self.store)
        self.catalog = PluginCatalog(self.store, self.hermes)

    def installed(self):
        source = self.hermes.root / "home/hermes-agent"
        source.mkdir(parents=True)
        (source / "run_agent.py").write_text("# neutral fixture")
        (self.hermes.root / "installed.json").write_text(json.dumps({"commit": COMMIT}))

    def test_catalog_does_not_download_or_create_plugin_data(self):
        with patch("hermes_plugin.subprocess.Popen") as process:
            value = self.catalog.snapshot()["plugins"][0]
        self.assertEqual(value["state"], "available")
        self.assertFalse(value["can_toggle"])
        self.assertFalse(self.hermes.root.exists())
        process.assert_not_called()

    def test_quick_toggle_persists_without_replacing_model_settings(self):
        self.installed()
        self.hermes.configure(
            {"enabled": False, "model": "neutral:latest", "context": 65536}
        )
        self.catalog.set_enabled("hermes", True)
        self.assertTrue(HermesPlugin(self.store).preferences["enabled"])
        self.catalog.set_enabled("hermes", False)
        saved = HermesPlugin(self.store).preferences
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["model"], "neutral:latest")
        self.assertEqual(saved["context"], 65536)

    def test_disable_during_task_does_not_kill_it_or_enable_new_task(self):
        self.installed()
        self.catalog.set_enabled("hermes", True)
        self.hermes.busy = True
        self.hermes.status.update(phase="running", message="Neutral task")
        with patch("hermes_plugin.stop_owned") as stop:
            value = self.catalog.set_enabled("hermes", False)["plugins"][0]
            with self.assertRaises(ValueError):
                self.hermes.run(1, "research")
        self.assertTrue(self.hermes.busy)
        self.assertFalse(value["enabled"])
        self.assertIn("завершает", value["state_label"])
        stop.assert_not_called()
        with self.assertRaises(ValueError):
            self.catalog.configure("hermes", {"model": "other", "context": 65536})

    def test_model_form_cannot_overwrite_a_recent_quick_toggle(self):
        self.installed()
        self.catalog.set_enabled("hermes", True)
        self.catalog.set_enabled("hermes", False)
        self.catalog.configure("hermes", {"model": "new-model", "context": 65536})
        self.assertFalse(self.hermes.preferences["enabled"])
        self.assertEqual(self.hermes.preferences["model"], "new-model")
        with self.assertRaises(ValueError):
            self.catalog.configure(
                "hermes", {"enabled": True, "model": "bad", "context": 65536}
            )

    def test_failed_persist_keeps_original_enabled_state(self):
        self.installed()
        self.catalog.set_enabled("hermes", True)
        with patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.catalog.set_enabled("hermes", False)
        self.assertTrue(self.hermes.preferences["enabled"])
        self.assertTrue(HermesPlugin(self.store).preferences["enabled"])

    def test_legacy_enabled_but_uninstalled_plugin_can_be_disabled(self):
        self.hermes.configure({"enabled": True, "model": "legacy", "context": 65536})
        entry = self.catalog.snapshot()["plugins"][0]
        self.assertTrue(entry["can_toggle"])
        self.assertTrue(entry["attention"])
        self.catalog.set_enabled("hermes", False)
        self.assertFalse(HermesPlugin(self.store).preferences["enabled"])

    def test_favorites_survive_restart_and_invalid_input_does_not_change_state(self):
        self.catalog.set_favorite("hermes", True)
        self.assertTrue(
            PluginCatalog(self.store, self.hermes).snapshot()["plugins"][0]["favorite"]
        )
        for identity, value in [("../other", True), ("hermes", "yes"), ([], True)]:
            with self.assertRaises(ValueError):
                self.catalog.set_favorite(identity, value)
        self.assertTrue(self.catalog.snapshot()["plugins"][0]["favorite"])

    def test_uninstalled_or_non_boolean_toggle_is_rejected(self):
        with self.assertRaises(ValueError):
            self.catalog.set_enabled("hermes", True)
        for value in ["false", 0, None]:
            with self.assertRaises(ValueError):
                self.catalog.set_enabled("hermes", value)
        self.assertFalse(self.hermes.preferences["enabled"])

    def test_corrupt_favorites_do_not_break_application_startup(self):
        self.catalog.path.parent.mkdir(parents=True)
        self.catalog.path.write_text("[]")
        self.assertFalse(
            PluginCatalog(self.store, self.hermes).snapshot()["plugins"][0]["favorite"]
        )
