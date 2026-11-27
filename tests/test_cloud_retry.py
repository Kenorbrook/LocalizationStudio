import io
import json
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from cloud_connections import listing, save
from cloud_retry import retry_delay
from commands.jobs import control
from core import Store
from providers import CloudTemporaryError, ServiceUnavailable, json_request
from worker import run


def http_error(code, after=None, payload=None):
    headers = Message()
    if after:
        headers["Retry-After"] = after
    return HTTPError(
        "https://example.com/v1/chat/completions",
        code,
        "fixture",
        headers,
        io.BytesIO(json.dumps(payload).encode()),
    )


class CloudRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "test.sqlite3")
        self.project = self.store.project(str(self.root))["id"]
        corpus = self.root / "text.json"
        corpus.write_text('[{"source":"The door is open."}]', encoding="utf-8")
        self.store.import_files(self.project, [corpus])
        credential = patch("providers.secret", return_value="fixture")
        credential.start()
        self.addCleanup(credential.stop)
        self.options = {"name": "Test", "endpoint": "https://example.com/v1"}
        self.connection = save(self.store, {**self.options, "key": "fixture"})

    def start_job(self, **overrides):
        return self.store.create_job(
            self.project,
            "translate",
            "cloud",
            dict(
                endpoint=self.options["endpoint"],
                cloud_connection=self.connection,
                cloud_model="test",
                auto_foreign=False,
                **overrides,
            ),
        )

    def state(self, job):
        with self.store.db() as db:
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone())

    def translate(self):
        text = json.dumps({"items": [{"id": 1, "text": "Дверь открыта."}]})
        return io.BytesIO(
            json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        )

    def test_retry_after_seconds_and_date(self):
        self.assertEqual(retry_delay("45", 0, 0), 45)
        self.assertEqual(retry_delay("Thu, 01 Jan 1970 00:02:00 GMT", 1, 0), 120)
        self.assertEqual(retry_delay("invalid", 2, 0), 60)
        self.assertIsNone(retry_delay(None, 6, 0))

    def test_only_transient_cloud_statuses_enter_retry(self):
        opener = Mock()
        with patch("providers.urllib.request.build_opener", return_value=opener):
            for code in (429, 503, 504, 524):
                opener.open.side_effect = http_error(code, "45")
                with self.assertRaises(CloudTemporaryError) as error:
                    json_request(self.options["endpoint"], {})
                self.assertEqual(error.exception.retry_after, "45")
            for code in (400, 401, 402, 403, 404, 413, 429):
                opener.open.side_effect = http_error(
                    code, payload={"error": {"type": "insufficient_quota"}}
                )
                with self.assertRaises(ServiceUnavailable) as error:
                    json_request(self.options["endpoint"], {})
                self.assertNotIsInstance(error.exception, CloudTemporaryError)

    def test_504_retries_same_request_and_finishes_original_queue(self):
        jid = self.start_job()
        self.assertTrue(listing(self.store)[0]["retry_unavailable"])
        opener = Mock()
        opener.open.side_effect = [http_error(504, "45"), self.translate()]
        clock = SimpleNamespace(time=time.time, monotonic=Mock(side_effect=[0, 0, 45]))

        def sleep(_):
            running = self.state(jid)
            self.assertEqual(running["state"], "running")
            self.assertIn("504", running["error"])
            self.assertGreater(running["retry_at"] - time.time(), 40)

        clock.sleep = sleep
        with (
            patch("providers.urllib.request.build_opener", return_value=opener),
            patch("cloud_retry.time", clock),
        ):
            run(self.store, jid)
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(first.data, second.data)
        self.assertEqual(self.state(jid)["state"], "done")
        self.assertEqual(self.state(jid)["retry_at"], 0)
        self.assertEqual(self.store.record(1)["text"], "Дверь открыта.")
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM errors").fetchone()[0], 0)

    def test_pause_interrupts_wait_without_another_request(self):
        jid = self.start_job()
        opener = Mock()
        opener.open.side_effect = http_error(503)
        clock = SimpleNamespace(
            time=time.time,
            monotonic=lambda: 0,
            sleep=lambda _: control(self.store, {"id": jid, "mode": "pause"}),
        )
        with (
            patch("providers.urllib.request.build_opener", return_value=opener),
            patch("cloud_retry.time", clock),
        ):
            run(self.store, jid)
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(self.state(jid)["state"], "paused")
        self.assertEqual(self.state(jid)["retry_at"], 0)
        self.assertEqual(self.state(jid)["done"], 0)

    def test_key_toggle_and_request_limit_prevent_another_request(self):
        for enabled, limit in [(False, 10), (True, 1)]:
            with self.subTest(enabled=enabled, limit=limit):
                save(
                    self.store,
                    {
                        **self.options,
                        "id": self.connection,
                        "retry_unavailable": enabled,
                    },
                )
                jid = self.start_job(max_calls=limit)
                opener = Mock()
                opener.open.side_effect = http_error(429)
                with patch(
                    "providers.urllib.request.build_opener", return_value=opener
                ):
                    run(self.store, jid)
                self.assertEqual(opener.open.call_count, 1)
                self.assertEqual(self.state(jid)["state"], "paused")
                self.assertEqual(self.state(jid)["retry_at"], 0)
                control(self.store, {"id": jid, "mode": "cancel"})
