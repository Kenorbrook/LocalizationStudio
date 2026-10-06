import json, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from app import api
from error_workflow import budget, retry_errors


class ErrorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "test.db")
        self.pid = self.store.project(str(self.root))["id"]
        p = self.root / "a.json"
        p.write_text('[{"source":"Hello."},{"source":"Goodbye."}]')
        self.store.import_files(self.pid, [p])
        self.jid = self.store.create_job(
            self.pid,
            "translate",
            "local",
            {
                "context": 8192,
                "model": "fixture",
                "source_language": "English",
                "target_language": "Russian",
            },
        )
        self.store.error(self.jid, 1, "context")
        self.store.error(self.jid, 2, "context")

    def tearDown(self):
        self.tmp.cleanup()

    def test_budget_is_offline_and_does_not_change_queue(self):
        with (
            patch("providers.models", side_effect=AssertionError("network")),
            patch("providers.json_request", side_effect=AssertionError("inference")),
        ):
            r = budget(
                self.store,
                self.pid,
                1,
                "translate",
                {
                    "context": 16384,
                    "max_output": 1200,
                    "context_before": 0,
                    "context_after": 0,
                },
            )
        self.assertTrue(r["approximate"])
        self.assertEqual(r["configured"], 16384)
        self.assertGreater(r["estimated_full"], 1200)
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM queue WHERE state='pending'"
                ).fetchone()[0],
                2,
            )

    def test_resume_changes_token_budget_and_requeues_selected_error(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused',heartbeat=0,done=2")
            db.execute("UPDATE queue SET state='error'")
        with patch("job_runtime.launch") as launch:
            api(
                self.store,
                "retry-errors",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "provider": "local",
                    "record": 1,
                    "settings": {
                        "context": 16384,
                        "model": "fixture-new",
                        "max_output": 2400,
                    },
                },
            )
        launch.assert_called_once()
        with self.store.db() as db:
            j = dict(db.execute("SELECT * FROM jobs").fetchone())
            self.assertEqual(json.loads(j["settings"])["context"], 16384)
            self.assertEqual(json.loads(j["settings"])["model"], "fixture-new")
            self.assertEqual(j["done"], 1)
            self.assertEqual(
                [r[0] for r in db.execute("SELECT state FROM queue ORDER BY record")],
                ["pending", "error"],
            )
            self.assertEqual(
                json.loads(db.execute("SELECT settings FROM projects").fetchone()[0])[
                    "context"
                ],
                16384,
            )

    def test_running_retry_is_rejected_without_mutation(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='running'")
        with self.assertRaises(ValueError):
            retry_errors(
                self.store,
                {
                    "project": self.pid,
                    "stage": "translate",
                    "provider": "local",
                    "settings": {},
                },
                api,
            )

    def test_finished_job_retry_targets_only_selected_record(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='incomplete'")
        with patch("job_runtime.launch"):
            r = api(
                self.store,
                "retry-errors",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "provider": "local",
                    "record": 2,
                    "settings": {
                        "model": "fixture",
                        "source_language": "English",
                        "target_language": "Russian",
                        "context": 16384,
                    },
                },
            )
        with self.store.db() as db:
            self.assertEqual(
                [
                    x[0]
                    for x in db.execute(
                        "SELECT record FROM queue WHERE job=?", (r["job"],)
                    )
                ],
                [2],
            )

    def test_budget_cannot_read_another_projects_record(self):
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        with self.assertRaises(ValueError):
            budget(self.store, pid, 1, "translate", {})

    def test_custom_retry_preserves_job_and_project_defaults(self):
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused',heartbeat=0,done=2")
            db.execute("UPDATE queue SET state='error'")
            before = db.execute("SELECT settings FROM projects").fetchone()[0]
            old = json.loads(db.execute("SELECT settings FROM jobs").fetchone()[0])
        with patch("job_runtime.launch"):
            api(
                self.store,
                "retry-errors",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "record": 1,
                    "provider": "local",
                    "settings": {"context": 32768},
                    "overrides": {
                        "context": 16384,
                        "context_before": 2,
                        "context_after": 1,
                    },
                },
            )
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT settings FROM projects").fetchone()[0], before
            )
            self.assertEqual(
                json.loads(db.execute("SELECT settings FROM jobs").fetchone()[0])[
                    "context"
                ],
                old["context"],
            )
            rows = db.execute("SELECT record,settings FROM queue_overrides").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["record"], 1)
            self.assertEqual(json.loads(rows[0]["settings"])["context"], 16384)

    def test_worker_restores_defaults_after_custom_record(self):
        from worker import run

        with self.store.db() as db:
            db.execute(
                "INSERT INTO queue_overrides VALUES (?,?,?)",
                (
                    self.jid,
                    1,
                    json.dumps(
                        {"context": 16384, "context_before": 2, "context_after": 1}
                    ),
                ),
            )

        class Fake:
            def __init__(self, p, k, s):
                self.settings = s
                self.calls = 0

        seen = []

        def translate(provider, stage, entry):
            seen.append(
                (
                    entry["id"],
                    provider.settings["context"],
                    provider.settings["context_before"],
                )
            )
            return ("Привет.", "")

        with (
            patch("worker.process_record", side_effect=translate),
            patch(
                "foreign_language.check_record",
                side_effect=lambda store, rid, *a, **k: store.record(rid),
            ),
        ):
            run(self.store, self.jid, Fake)
        self.assertEqual(seen, [(1, 16384, 2), (2, 8192, 12)])

    def test_invalid_custom_values_do_not_create_new_job(self):
        with self.assertRaises(ValueError):
            api(
                self.store,
                "retry-errors",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "record": 1,
                    "provider": "local",
                    "settings": {},
                    "overrides": {
                        "context": 16384,
                        "context_before": 101,
                        "context_after": 2,
                    },
                },
            )
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
