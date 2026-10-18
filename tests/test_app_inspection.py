import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app_inspection import AppInspection, discovery_path
from inspection_client import inspect_app
from mcp_server import handle


class AppInspectionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "test.sqlite3"
        self.inspection = AppInspection(self.database)
        self.inspection.port = 12345
        self.inspection.window = Mock()
        self.inspection.window.evaluate_js.return_value = json.dumps(
            {"view": "text", "screenshot_available": True}
        )

    def test_unready_window_is_an_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, "не готово"):
            self.inspection.inspect()

    def test_discovery_owned_by_session_and_database(self):
        self.inspection.publish()
        path = discovery_path(self.database)
        self.assertEqual(json.loads(path.read_text())["port"], 12345)
        other = AppInspection(self.database)
        other.dispose()
        self.assertTrue(path.exists())
        self.inspection.dispose()
        self.assertFalse(path.exists())

    def test_capture_is_fresh_and_does_not_call_window_controls(self):
        self.inspection.publish()
        png = b"\x89PNG\r\n\x1a\nfixture"
        with patch.object(self.inspection, "capture_png", return_value=png) as capture:
            for _ in range(2):
                result = self.inspection.inspect(True)
                self.assertEqual(base64.b64decode(result["image"]["data"]), png)
            self.assertEqual(capture.call_count, 2)
        self.assertEqual(
            {call[0] for call in self.inspection.window.method_calls}, {"evaluate_js"}
        )

    def test_revealed_secret_prevents_image_capture(self):
        self.inspection.publish()
        self.inspection.window.evaluate_js.return_value = (
            '{"screenshot_available":false}'
        )
        with patch.object(self.inspection, "capture_png") as capture:
            with self.assertRaisesRegex(RuntimeError, "ключ"):
                self.inspection.inspect(True)
            capture.assert_not_called()

    def test_missing_window_does_not_launch_gui(self):
        with self.assertRaisesRegex(RuntimeError, "Не удалось прочитать"):
            inspect_app(self.database)

    def test_mcp_returns_native_image_content_and_errors(self):
        store = Mock(path=self.database)
        request = {
            "id": 3,
            "method": "tools/call",
            "params": {"name": "app_screenshot", "arguments": {}},
        }
        with patch(
            "inspection_client.inspect_app",
            return_value={
                "ui": {"view": "text"},
                "image": {"mimeType": "image/png", "data": "cG5n"},
            },
        ):
            result = handle(store, request)["result"]
        self.assertEqual(
            result["content"][1],
            {"type": "image", "mimeType": "image/png", "data": "cG5n"},
        )
        self.assertNotIn("image", json.loads(result["content"][0]["text"]))
        with patch(
            "inspection_client.inspect_app", side_effect=RuntimeError("not running")
        ):
            result = handle(store, request)["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "not running")


if __name__ == "__main__":
    unittest.main()
