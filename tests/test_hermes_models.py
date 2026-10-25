import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from core import Store
from desktop_api import DesktopApi
from hermes_models import HermesModels, configured_context
from hermes_plugin import HermesPlugin


class HermesModelsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.models = {
            "fixture:latest": {
                "name": "fixture:latest",
                "digest": "original-digest",
                "size": 1000,
                "capabilities": ["tools"],
                "model_info": {"llama.context_length": 131072},
                "parameters": "num_ctx 8192\ntemperature 0.4",
            }
        }
        self.requests = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.fail_create = False
        self.incomplete = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond({"models": list(owner.models.values())})

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, data))
                if self.path == "/api/show":
                    return self.respond(owner.models[data["model"]])
                if self.path != "/api/create":
                    raise AssertionError(
                        "Inference and downloads must never be requested"
                    )
                owner.entered.set()
                owner.release.wait(10)
                if owner.fail_create:
                    return self.respond({"error": "neutral failure"})
                if owner.incomplete:
                    return self.respond({"status": "using existing layer"})
                new = copy.deepcopy(owner.models[data["from"]])
                new.update(
                    name=data["model"],
                    digest=hashlib.sha256(json.dumps(data).encode()).hexdigest(),
                    parameters="num_ctx " + str(data["parameters"]["num_ctx"]),
                )
                owner.models[new["name"]] = new
                self.respond({"status": "success"})

            def respond(self, data):
                body = (json.dumps(data) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.client = HermesModels(f"http://127.0.0.1:{server.server_port}")
        self.cancelled = threading.Event()

    def prepare(self, name="fixture:latest", context=65536):
        return self.client.prepare(
            self.root / "profiles", name, context, self.cancelled, lambda message: None
        )

    def creates(self):
        return [data for path, data in self.requests if path == "/api/create"]

    def test_prepares_selected_context_using_existing_weights_only(self):
        original = copy.deepcopy(self.models["fixture:latest"])
        result = self.prepare(context=98304)
        self.assertEqual(
            self.creates(),
            [
                {
                    "model": result["model"],
                    "from": "fixture:latest",
                    "parameters": {"num_ctx": 98304},
                    "stream": True,
                }
            ],
        )
        self.assertEqual(self.models["fixture:latest"], original)
        self.assertEqual(configured_context(self.models[result["model"]]), 98304)
        self.assertTrue(
            all(path in {"/api/show", "/api/create"} for path, _ in self.requests)
        )

    def test_repeated_preparation_reuses_verified_profile(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first["model"], second["model"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(self.creates()), 1)

    def test_matching_original_context_requires_no_creation(self):
        self.models["fixture:latest"]["parameters"] = "num_ctx 65536"
        result = self.prepare()
        self.assertEqual(result["model"], "fixture:latest")
        self.assertEqual(self.creates(), [])

    def test_existing_unowned_name_is_never_overwritten(self):
        first = self.prepare()
        original = copy.deepcopy(self.models[first["model"]])
        (self.root / "profiles/model_profiles.json").unlink()
        second = self.prepare()
        self.assertNotEqual(first["model"], second["model"])
        self.assertEqual(self.models[first["model"]], original)

    def test_replaced_profile_is_never_overwritten(self):
        first = self.prepare()
        self.models[first["model"]]["digest"] = "external-change"
        second = self.prepare()
        self.assertNotEqual(first["model"], second["model"])
        self.assertEqual(self.models[first["model"]]["digest"], "external-change")

    def test_unsupported_models_and_invalid_context_do_not_create_anything(self):
        for name, context in [
            ("missing", 65536),
            ("fixture:latest", 8192),
            ("fixture:latest", "65536"),
        ]:
            with self.assertRaises(ValueError):
                self.prepare(name, context)
        self.models["fixture:latest"]["capabilities"] = ["completion"]
        with self.assertRaisesRegex(ValueError, "инструментов"):
            self.prepare()
        self.models["fixture:latest"]["capabilities"] = ["tools"]
        self.models["fixture:latest"]["model_info"] = {"llama.context_length": 32768}
        with self.assertRaisesRegex(ValueError, "контекста"):
            self.prepare()
        self.assertEqual(self.creates(), [])

    def test_cloud_is_rejected_without_creating_or_pulling(self):
        self.models["fixture:latest"]["remote_model"] = "cloud-target"
        with self.assertRaisesRegex(ValueError, "облачные"):
            self.prepare()
        self.assertFalse(self.creates())

    def test_incomplete_or_error_response_is_not_reported_as_prepared(self):
        self.fail_create = True
        with self.assertRaisesRegex(ValueError, "не смогла"):
            self.prepare()
        self.fail_create = False
        self.incomplete = True
        with self.assertRaisesRegex(ValueError, "не подтвердила"):
            self.prepare()
        self.assertFalse((self.root / "profiles/model_profiles.json").exists())

    def test_cancel_before_create_does_not_write_profile(self):
        self.cancelled.set()
        with self.assertRaisesRegex(ValueError, "остановлена"):
            self.prepare()
        self.assertEqual(self.creates(), [])

    def test_unprepared_context_is_rejected_even_when_model_maximum_is_large(self):
        with self.assertRaisesRegex(ValueError, "Подготовить"):
            self.client.validate("fixture:latest", 65536, configured=True)

    def test_native_bridge_persists_profile_without_reenabling_plugin(self):
        store = Store(self.root / "test.sqlite3")
        store.launch_lock = threading.RLock()
        plugin = HermesPlugin(store)
        plugin.models = self.client
        api = DesktopApi(None, None, plugin)
        self.release.clear()
        api.prepare_hermes_model("fixture:latest", 65536)
        self.assertTrue(self.entered.wait(3))
        plugin.set_enabled(False)
        self.release.set()
        self.until(lambda: not plugin.busy)
        self.assertEqual(plugin.status["phase"], "ready")
        self.assertTrue(plugin.preferences["model"].startswith("studio-hermes-"))
        self.assertFalse(HermesPlugin(store).preferences["enabled"])
        self.assertEqual(plugin.preferences["context"], 65536)

    def test_cancelled_preparation_keeps_previous_model_preferences(self):
        store = Store(self.root / "test.sqlite3")
        plugin = HermesPlugin(store)
        plugin.models = self.client
        previous = dict(plugin.preferences)
        self.release.clear()
        plugin.prepare_model("fixture:latest", 65536)
        self.assertTrue(self.entered.wait(3))
        plugin.cancel()
        self.release.set()
        self.until(lambda: not plugin.busy)
        self.assertEqual(plugin.preferences, previous)
        self.assertEqual(plugin.status["phase"], "idle")

    def test_connection_check_rejects_wrong_actual_context_before_runtime(self):
        plugin = HermesPlugin(Store(self.root / "test.sqlite3"))
        plugin.models = self.client
        plugin.configure(
            {"enabled": False, "model": "fixture:latest", "context": 65536}
        )
        with (
            patch.object(plugin, "installed", return_value=True),
            patch.object(plugin, "_check_runtime") as check,
        ):
            plugin.check()
            self.until(lambda: not plugin.busy)
            check.assert_not_called()
        self.assertEqual(plugin.status["phase"], "error")
        self.assertIn("Подготовить", plugin.status["message"])

    def test_offline_inventory_reports_reason(self):
        client = HermesModels("http://127.0.0.1:1")
        result = client.listing()
        self.assertFalse(result["online"])
        self.assertIn("Запустите Ollama", result["error"])

    def until(self, action):
        deadline = time.monotonic() + 5
        while not action() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(action())
