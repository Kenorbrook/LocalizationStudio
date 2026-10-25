import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import Store
from hermes_plugin import HermesPlugin
from hermes_tools import call
from plugin_resources.hermes_progress import (
    Progress,
    progress_message,
    REQUEST_TIMEOUT,
    RUN_BUDGET,
)


class HermesExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_progress_does_not_write_reasoning_content_or_tool_arguments(self):
        path = self.root / "progress.json"
        progress = Progress(path)
        with patch("builtins.print"):
            progress.model_step(1, {"secret": "do not expose"})
            progress.delta("private game contents")
            progress.tool_start("private-id", "private-name", {"api_key": "secret"})
            progress.tool_complete(None, None, None, "private response")
        raw = path.read_text()
        self.assertNotIn("private", raw)
        self.assertNotIn("secret", raw)
        self.assertEqual(json.loads(raw)["step"], 1)
        self.assertIn("02:05", progress_message(path, 125))
        self.assertIn("21", progress_message(path, 125))

    def test_invalid_progress_is_not_displayed(self):
        path = self.root / "progress.json"
        for value in [
            {"stage": "untrusted"},
            {"stage": "answer", "step": "bad"},
            {"stage": "answer", "characters": -1},
        ]:
            path.write_text(json.dumps(value))
            self.assertIsNone(progress_message(path, 1))

    def test_progress_write_failure_cannot_abort_the_agent(self):
        progress = Progress(self.root / "progress.json")
        with (
            patch.object(Path, "replace", side_effect=PermissionError("locked")),
            patch("builtins.print"),
        ):
            progress.emit("startup")
            progress.delta("neutral text")

    def test_supervisor_updates_progress_while_slow_process_is_alive(self):
        plugin = HermesPlugin(Store(self.root / "test.sqlite3"))
        plugin.root.mkdir(parents=True)
        progress = plugin.root / "progress.json"
        progress.write_text(
            json.dumps({"stage": "answer", "step": 2, "characters": 123})
        )
        process = SimpleNamespace(
            wait=unittest.mock.Mock(
                side_effect=[subprocess.TimeoutExpired("neutral", 1), 0]
            )
        )
        with (
            patch("hermes_plugin.subprocess.Popen", return_value=process),
            patch("hermes_plugin.stop_owned") as stop,
        ):
            plugin._run(["neutral"], timeout=600, progress=progress)
        self.assertIn("Получаем ответ", plugin.status["message"])
        self.assertIn("123", plugin.status["message"])
        stop.assert_not_called()
        self.assertIsNone(plugin.process)

    def test_real_timeout_stops_only_owned_process_and_reports_last_stage(self):
        plugin = HermesPlugin(Store(self.root / "test.sqlite3"))
        plugin.root.mkdir(parents=True)
        progress = plugin.root / "progress.json"
        progress.write_text(json.dumps({"stage": "waiting", "step": 1}))
        process = SimpleNamespace(wait=lambda timeout: 0)
        with (
            patch("hermes_plugin.subprocess.Popen", return_value=process),
            patch("hermes_plugin.time.monotonic", side_effect=[0, 61]),
            patch("hermes_plugin.stop_owned") as stop,
        ):
            with self.assertRaisesRegex(ValueError, "Последний этап: Модель"):
                plugin._run(["neutral"], timeout=60, progress=progress)
        stop.assert_called_once_with(process)
        self.assertIsNone(plugin.process)

    def test_local_execution_budget_is_bounded_and_longer_than_original_deadline(self):
        self.assertGreater(REQUEST_TIMEOUT, 420)
        self.assertLess(RUN_BUDGET, REQUEST_TIMEOUT)

    def test_compact_structure_keeps_evidence_and_marks_omitted_items(self):
        store = Store(self.root / "test.sqlite3")
        game = self.root / "game"
        game.mkdir()
        project = store.project(str(game))["id"]
        data = {
            "engine": {
                "name": "RenPy",
                "confidence": "high",
                "evidence": ["script.rpy"],
            },
            "root": str(game),
            "candidates": [
                {
                    "path": f"{i}.rpy",
                    "format": "rpy",
                    "supported": True,
                    "samples": ["x" * 5000],
                }
                for i in range(100)
            ],
            "archives": [{"path": f"{i}.zip"} for i in range(100)],
        }
        with patch("hermes_tools.analyze", return_value=data):
            result = call(store, project, "inspect_game_project", {})
        self.assertEqual(len(result["candidates"]), 12)
        self.assertEqual(result["candidates_total"], 100)
        self.assertEqual(len(result["archives"]), 8)
        self.assertTrue(result["listing_truncated"])
        self.assertEqual(result["engine"]["evidence"], ["script.rpy"])
        self.assertNotIn("samples", result["candidates"][0])
        self.assertLess(len(json.dumps(result)), 4000)
