import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core import Store
from desktop_api import DesktopApi
from plugin_catalog import PluginCatalog
from plugin_manager import PluginManager
from plugin_package import read_package
from plugin_updates import latest


class PluginPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "data/test.sqlite3")
        self.store.launch_lock = threading.RLock()
        self.manager = PluginManager(self.store)
        self.addCleanup(self.manager.dispose)
        self.catalog = PluginCatalog(self.store, manager=self.manager)

    def package(self, revision="1.0.0", broken=False, extras=None, backend=None):
        value = dict(
            id="sample-theme",
            name="Sample",
            description="Neutral fixture",
            category="Themes",
            version=revision,
            api_version=1,
            min_app_version="0.6.0",
            ui="ui/entry.js",
            permissions=["ui", "theme"],
        )
        files = {
            "ui/entry.js": b"export function create(api) { api.style('body { color:red }'); return {}; }"
        }
        if backend:
            files["__init__.py"] = backend.encode()
            value["backend"] = "__init__.py"
            value["permissions"].append("backend")
        value["files"] = {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }
        if broken:
            files["ui/entry.js"] = b"corrupted"
        files["plugin.json"] = json.dumps(value).encode()
        files.update(extras or {})
        path = self.root / (revision + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return path

    def test_seed_migrates_without_touching_legacy_runtime_preferences(self):
        entry = self.manager.entry("hermes")
        controller = entry["provider"]
        controller.configure(
            {"enabled": False, "model": "saved-model", "context": 98304}
        )
        runtime = controller.root / "home/keep.txt"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("installed runtime")
        generation = entry["generation"]
        restarted = PluginManager(self.store)
        self.addCleanup(restarted.dispose)
        self.assertEqual(restarted.entry("hermes")["generation"], generation)
        self.assertEqual(
            restarted.entry("hermes")["provider"].preferences["model"], "saved-model"
        )
        self.assertEqual(runtime.read_text(), "installed runtime")
        self.assertEqual(self.catalog.snapshot()["plugins"][0]["version"], "1.0.1")

    def test_update_and_rollback_preserve_enabled_and_data_and_other_plugins(self):
        original = self.manager.entry("hermes")["provider"]
        self.manager.install(self.package())
        self.catalog.set_enabled("sample-theme", True)
        data = self.store.path.parent / "plugins/sample-theme/keep.txt"
        data.write_text("user data")
        self.manager.install(self.package("1.0.1"))
        self.assertTrue(
            self.manager.entry("sample-theme")["provider"].summary()["enabled"]
        )
        self.assertEqual(self.manager.metadata("sample-theme")["version"], "1.0.1")
        self.manager.rollback("sample-theme")
        self.assertEqual(self.manager.metadata("sample-theme")["version"], "1.0.0")
        self.assertEqual(data.read_text(), "user data")
        self.assertIs(self.manager.entry("hermes")["provider"], original)

    def test_failed_validation_and_backend_import_keep_previous_generation(self):
        self.manager.install(self.package())
        old = self.manager.entry("sample-theme")
        for package in [
            self.package("1.0.1", broken=True),
            self.package("1.0.2", backend="raise RuntimeError('broken backend')"),
        ]:
            with self.assertRaises((ValueError, RuntimeError)):
                self.manager.install(package)
            self.assertIs(self.manager.entry("sample-theme"), old)
            self.assertEqual(
                self.manager._pointer("sample-theme")["active"], old["generation"]
            )

    def test_damaged_plugin_stays_visible_and_can_roll_back_after_restart(self):
        self.manager.install(self.package())
        self.manager.install(self.package("1.0.1"))
        entry = self.manager.entry("sample-theme")
        (entry["root"] / "ui/entry.js").write_text("corrupted")
        restarted = PluginManager(self.store)
        self.addCleanup(restarted.dispose)
        value = PluginCatalog(self.store, manager=restarted).snapshot()
        broken = next(p for p in value["plugins"] if p["id"] == "sample-theme")
        self.assertTrue(broken["attention"])
        self.assertTrue(broken["rollback_available"])
        restarted.rollback("sample-theme")
        self.assertEqual(restarted.metadata("sample-theme")["version"], "1.0.0")

    def test_busy_plugin_cannot_be_replaced_or_rolled_back(self):
        self.manager.install(self.package())
        self.manager.install(self.package("1.0.1"))
        self.manager.entry("sample-theme")["provider"].busy = True
        with self.assertRaises(ValueError):
            self.manager.install(self.package("1.0.2"))
        with self.assertRaises(ValueError):
            self.manager.rollback("sample-theme")

    def test_traversal_unlisted_files_and_case_collisions_are_rejected(self):
        for name in [
            "../escape.txt",
            "/absolute",
            "C:/escape",
            "ui/ENTRY.js",
            "ui/missing.js",
            "nul.txt",
            "ui/file.",
        ]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                read_package(self.package(extras={name: b"bad"}))
        self.assertFalse((self.root / "escape.txt").exists())

    def test_ui_assets_are_active_generation_and_manifest_only(self):
        self.manager.install(self.package())
        generation = self.manager.entry("sample-theme")["generation"]
        self.assertIn(
            "export", self.manager.asset("sample-theme", generation, "ui/entry.js")
        )
        for name in ["../active.json", "plugin.json", "missing.js"]:
            with self.assertRaises(ValueError):
                self.manager.asset("sample-theme", generation, name)
        self.manager.install(self.package("1.0.1"))
        with self.assertRaises(ValueError):
            self.manager.asset("sample-theme", generation, "ui/entry.js")

    def test_core_recovery_and_catalog_do_not_depend_on_hermes_presence(self):
        self.manager.entries.pop("hermes")
        api = DesktopApi(None, None, manager=self.manager)
        self.assertEqual(api.get_plugins()["plugins"], [])
        self.assertFalse(api.get_recovery_state()["automatic_retry"])

    def test_scoped_mcp_call_resolves_tools_without_prior_list_request(self):
        from mcp_server import handle

        game = self.root / "game"
        game.mkdir()
        (game / "script.rpy").write_text('label start:\n    "Neutral text."\n')
        self.store.agent_project = self.store.project(str(game))["id"]
        self.store.agent_tools = self.manager.entry("hermes")[
            "backend_module"
        ].hermes_tools
        value = handle(
            self.store,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "inspect_game_project", "arguments": {}},
            },
        )
        self.assertFalse(value["result"].get("isError"), value)
        self.assertIn("script.rpy", json.dumps(value))

    def test_background_update_downloads_only_plugin_and_checks_checksum(self):
        self.manager.install(self.package())
        package = self.package("1.0.1")
        payload = package.read_bytes()
        offer = dict(
            phase="available",
            version="1.0.1",
            filename="plugin-sample-theme-v1.0.1.zip",
            url="fixture-zip",
            checksum_url="fixture-sums",
        )
        self.manager.updates["sample-theme"] = offer

        def fetch(url, *args):
            return (
                payload
                if url == "fixture-zip"
                else (
                    hashlib.sha256(payload).hexdigest() + "  " + offer["filename"]
                ).encode()
            )

        with patch("plugin_updates.fetch", side_effect=fetch):
            self.manager.update("sample-theme")
            deadline = time.monotonic() + 5
            while self.manager.busy and time.monotonic() < deadline:
                time.sleep(0.01)
        self.assertEqual(self.manager.metadata("sample-theme")["version"], "1.0.1")
        self.assertFalse((self.manager.root / "sample-theme/download.zip").exists())

    def test_update_feed_ignores_app_tags_and_selects_own_package(self):
        prefix = "https://github.com/Kenorbrook/LocalizationStudio/releases/download/plugin-sample-theme-v1.0.1/"

        def fetch(url, *args):
            if "matching-refs" in url:
                return json.dumps(
                    [
                        {"ref": "refs/tags/v99.0.0"},
                        {"ref": "refs/tags/plugin-sample-theme-v1.0.1"},
                    ]
                ).encode()
            return json.dumps(
                {
                    "assets": [
                        {
                            "name": "plugin-sample-theme-v1.0.1.zip",
                            "size": 100,
                            "browser_download_url": prefix
                            + "plugin-sample-theme-v1.0.1.zip",
                        },
                        {
                            "name": "SHA256SUMS.txt",
                            "browser_download_url": prefix + "SHA256SUMS.txt",
                        },
                    ]
                }
            ).encode()

        with patch("plugin_updates.fetch", side_effect=fetch):
            offer = latest("sample-theme", "1.0.0")
        self.assertEqual(offer["version"], "1.0.1")
        self.assertEqual(offer["phase"], "available")
