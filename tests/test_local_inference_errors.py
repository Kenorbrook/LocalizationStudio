import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from core import Store
from providers import LocalInferenceError, Provider, ServiceUnavailable, json_request
from worker import run


class LocalInferenceErrorTests(unittest.TestCase):
    def http_error(self, url, code=500, details="CUDA error: out of memory"):
        return urllib.error.HTTPError(
            url, code, "error", {}, io.BytesIO(json.dumps({"error": details}).encode())
        )

    def test_local_chat_500_becomes_record_error_without_echoing_body(self):
        url = "http://127.0.0.1:11434/api/chat"
        with patch(
            "providers.DIRECT.open",
            side_effect=self.http_error(
                url, details="std::bad_alloc secret-value private-source"
            ),
        ):
            with self.assertRaises(LocalInferenceError) as error:
                json_request(url, {"messages": []}, local=True)
        self.assertIn("памяти", str(error.exception))
        self.assertIn("HTTP 500", str(error.exception))
        self.assertNotIn("secret-value", str(error.exception))
        self.assertNotIn("private-source", str(error.exception))

    def test_generic_or_invalid_500_body_still_has_safe_record_error(self):
        url = "http://127.0.0.1:11434/api/chat"
        for body in [b"not-json", b"[]", b'{"error":"unknown private text"}']:
            error = urllib.error.HTTPError(url, 500, "error", {}, io.BytesIO(body))
            with (
                patch("providers.DIRECT.open", side_effect=error),
                self.assertRaises(LocalInferenceError) as caught,
            ):
                json_request(url, {"messages": []}, local=True)
            self.assertNotIn("private", str(caught.exception))

    def test_local_inventory_or_busy_server_still_pauses(self):
        for url, code, data in [
            ("http://127.0.0.1:11434/api/tags", 500, None),
            ("http://127.0.0.1:11434/api/chat", 503, {}),
        ]:
            with (
                patch("providers.DIRECT.open", side_effect=self.http_error(url, code)),
                self.assertRaises(ServiceUnavailable),
            ):
                json_request(url, data, local=True)

    def test_cloud_500_is_not_treated_as_bad_record_and_body_is_not_read(self):
        url = "https://example.invalid/chat/completions"
        error = self.http_error(url)
        error.read = Mock(
            side_effect=AssertionError("Do not inspect cloud response body")
        )
        opener = Mock()
        opener.open.side_effect = error
        with (
            patch("providers.urllib.request.build_opener", return_value=opener),
            self.assertRaises(ServiceUnavailable),
        ):
            json_request(url, {"messages": []})
        error.read.assert_not_called()

    def test_connection_loss_still_pauses(self):
        with (
            patch(
                "providers.DIRECT.open", side_effect=urllib.error.URLError("offline")
            ),
            self.assertRaises(ServiceUnavailable),
        ):
            json_request("http://127.0.0.1:11434/api/chat", {}, local=True)

    def test_local_200_error_payload_is_not_a_translation(self):
        provider = Provider(
            {"id": 0, "root": str(Path(__file__).parent), "settings": "{}"},
            "local",
            {"context": 32768},
            diagnostic=True,
        )
        with (
            patch("providers.json_request", return_value={"error": "std::bad_alloc"}),
            self.assertRaises(LocalInferenceError),
        ):
            provider.call("translate", [{"id": 1, "english": "Hello."}])

    def test_worker_marks_only_failed_line_and_continues_with_retry_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "test.sqlite3")
            pid = store.project(str(root))["id"]
            corpus = root / "corpus.json"
            corpus.write_text(
                json.dumps(
                    [
                        {"source": "Hello."},
                        {"source": "This request fails."},
                        {"source": "Goodbye."},
                    ]
                )
            )
            store.import_files(pid, [corpus])
            settings = {
                "model": "fixture",
                "context": 8192,
                "max_output": 1200,
                "source_language": "English",
                "target_language": "Russian",
                "auto_foreign": False,
            }
            jid = store.create_job(pid, "translate", "local", settings)
            calls = []

            def respond(request, **kwargs):
                if request.full_url.endswith("/api/tags"):
                    value = {"models": [{"name": "fixture"}]}
                else:
                    data = json.loads(request.data)
                    item = json.loads(data["messages"][-1]["content"])["items"][0]
                    calls.append(item["english"])
                    if (
                        item["english"] == "This request fails."
                        and data["options"]["num_ctx"] == 8192
                    ):
                        raise self.http_error(request.full_url)
                    text = {
                        "Hello.": "Привет.",
                        "Goodbye.": "До свидания.",
                        "This request fails.": "Этот запрос завершается ошибкой.",
                    }[item["english"]]
                    value = {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": item["id"], "text": text}]},
                                ensure_ascii=False,
                            )
                        }
                    }
                return io.BytesIO(json.dumps(value).encode())

            with patch("providers.DIRECT.open", side_effect=respond):
                run(store, jid)
            with store.db() as db:
                job = dict(
                    db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
                )
                rows = [
                    tuple(r)
                    for r in db.execute("SELECT status,text FROM records ORDER BY id")
                ]
                errors = [
                    dict(r) for r in db.execute("SELECT * FROM errors WHERE resolved=0")
                ]
                queue = [
                    r[0] for r in db.execute("SELECT state FROM queue ORDER BY record")
                ]
            self.assertEqual(
                (job["state"], job["done"], job["worker_active"]), ("incomplete", 3, 0)
            )
            self.assertEqual(
                rows,
                [
                    ("translated", "Привет."),
                    ("empty", ""),
                    ("translated", "До свидания."),
                ],
            )
            self.assertEqual(queue, ["done", "error", "done"])
            self.assertEqual(len(errors), 1)
            self.assertIn("HTTP 500", errors[0]["message"])
            self.assertEqual(json.loads(errors[0]["budget_json"])["configured"], 8192)
            self.assertEqual(calls, ["Hello.", "This request fails.", "Goodbye."])
            retry = store.create_job(
                pid, "translate", "local", {**settings, "context": 16384}, retry=True
            )
            with patch("providers.DIRECT.open", side_effect=respond):
                run(store, retry)
            with store.db() as db:
                self.assertEqual(
                    tuple(
                        db.execute(
                            "SELECT state,done,total FROM jobs WHERE id=?", (retry,)
                        ).fetchone()
                    ),
                    ("done", 1, 1),
                )
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM errors WHERE resolved=0"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    [
                        row[0]
                        for row in db.execute(
                            "SELECT revision FROM records ORDER BY id"
                        )
                    ],
                    [1, 1, 1],
                )
            self.assertEqual(
                calls,
                ["Hello.", "This request fails.", "Goodbye.", "This request fails."],
            )


if __name__ == "__main__":
    unittest.main()
