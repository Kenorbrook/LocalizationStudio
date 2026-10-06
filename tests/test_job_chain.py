import json, tempfile, threading, unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from app import api
from job_chain import schedule, enqueue_retry


class JobChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "test.db")
        self.store.launch_lock = threading.RLock()
        self.pid = self.store.project(str(self.root))["id"]
        f = self.root / "a.json"
        f.write_text('[{"source":"Hello."},{"source":"Goodbye."}]')
        self.store.import_files(self.pid, [f])
        self.original = {
            "context": 8192,
            "max_output": 1200,
            "context_before": 12,
            "context_after": 8,
            "model": "fixture",
            "source_language": "English",
            "target_language": "Russian",
        }
        self.jid = self.store.create_job(self.pid, "translate", "local", self.original)
        self.store.error(self.jid, 1, "context")

    def tearDown(self):
        self.tmp.cleanup()

    def retry(self, custom=False):
        data = {
            "project": self.pid,
            "record": 1,
            "stage": "translate",
            "mode": "original",
            "settings": {"context": 32768},
        }
        if custom:
            data.update(
                mode="custom",
                provider="local",
                settings={**self.original, "model": "new", "max_output": 2400},
                overrides={"context": 16384, "context_before": 3, "context_after": 1},
            )
        return enqueue_retry(self.store, data)["job"]

    def job(self, jid):
        with self.store.db() as db:
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())

    def test_original_waits_and_preserves_settings(self):
        with self.store.db() as db:
            before = db.execute("SELECT settings FROM projects").fetchone()[0]
        with patch("job_runtime.launch") as launch:
            jid = self.retry()
        launch.assert_not_called()
        self.assertEqual(self.job(jid)["state"], "waiting")
        self.assertEqual(json.loads(self.job(jid)["settings"])["context"], 8192)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT settings FROM projects").fetchone()[0], before
            )
        self.assertEqual(json.loads(self.job(self.jid)["settings"])["context"], 8192)

    def test_custom_job_owns_parameters_and_record(self):
        jid = self.retry(True)
        s = json.loads(self.job(jid)["settings"])
        self.assertEqual(
            (
                s["context"],
                s["context_before"],
                s["context_after"],
                s["model"],
                s["max_output"],
            ),
            (16384, 3, 1, "new", 2400),
        )
        with self.store.db() as db:
            self.assertEqual(
                [
                    r[0]
                    for r in db.execute("SELECT record FROM queue WHERE job=?", (jid,))
                ],
                [1],
            )

    def test_bulk_includes_all_errors_once_beyond_visible_page(self):
        f = self.root / "many.json"
        f.write_text(json.dumps([{"source": f"Neutral line {i}"} for i in range(105)]))
        self.store.import_files(self.pid, [f])
        with self.store.db() as db:
            for rid in range(3, 108):
                db.execute(
                    "INSERT INTO errors(project,job,record,message,at) VALUES (?,?,?,?,?)",
                    (self.pid, self.jid, rid, "fixture", "now"),
                )
            db.execute(
                "INSERT INTO errors(project,job,record,message,at) VALUES (?,?,?,?,?)",
                (self.pid, self.jid, 1, "duplicate", "now"),
            )
            db.execute("UPDATE records SET manual=1 WHERE id=106")
            db.execute("UPDATE records SET status='preserved' WHERE id=107")
            old = db.execute(
                "SELECT settings FROM projects WHERE id=?", (self.pid,)
            ).fetchone()[0]
        with patch("job_runtime.launch") as launch:
            result = enqueue_retry(
                self.store,
                {
                    "project": self.pid,
                    "stage": "translate",
                    "mode": "custom",
                    "provider": "local",
                    "settings": self.original,
                    "overrides": {
                        "context": 16384,
                        "context_before": 3,
                        "context_after": 1,
                    },
                },
            )
        launch.assert_not_called()
        self.assertEqual(result["records"], 104)
        self.assertEqual(self.job(result["job"])["state"], "waiting")
        with self.store.db() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "SELECT record FROM queue WHERE job=?", (result["job"],)
                )
            ]
            self.assertEqual(len(set(ids)), 104)
            self.assertNotIn(106, ids)
            self.assertNotIn(107, ids)
            self.assertEqual(
                db.execute(
                    "SELECT settings FROM projects WHERE id=?", (self.pid,)
                ).fetchone()[0],
                old,
            )

    def test_bulk_plan_scopes_stage_project_and_deduplicates(self):
        from job_chain import plan

        self.store.error(self.jid, 1, "duplicate")
        self.store.update(2, 0, "Привет.", "translated", "fixture")
        review = self.store.create_job(
            self.pid, "review", "local", self.original, defer=True
        )
        self.store.error(review, 2, "review")
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        self.assertEqual(
            plan(self.store, pid), {"translate": 0, "review": 0, "cloud": 0}
        )
        self.assertEqual(
            plan(self.store, self.pid), {"translate": 1, "review": 1, "cloud": 0}
        )

    def test_bulk_budget_is_offline_and_covers_all_candidates(self):
        from job_chain import bulk_budget

        self.store.error(self.jid, 2, "context")
        with patch("providers.json_request", side_effect=AssertionError("inference")):
            result = bulk_budget(
                self.store, self.pid, "translate", {**self.original, "context": 16384}
            )
        self.assertEqual(result["records"], 2)
        self.assertEqual(result["configured"], 16384)
        self.assertTrue(result["approximate"])

    def test_fifo_and_paused_chain(self):
        one = self.retry()
        two = self.retry(True)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (self.jid,))
        with patch("job_runtime.launch") as launch:
            self.assertIsNone(schedule(self.store, self.pid))
            launch.assert_not_called()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (self.jid,))
        with patch("job_runtime.launch") as launch:
            self.assertEqual(schedule(self.store, self.pid), one)
            launch.assert_called_once_with(self.store, one)
            self.assertIsNone(schedule(self.store, self.pid))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (one,))
        with patch("job_runtime.launch"):
            self.assertEqual(schedule(self.store, self.pid), two)

    def test_cancelled_inflight_worker_blocks_next(self):
        one = self.retry()
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET state='cancelled',worker_active=1 WHERE id=?",
                (self.jid,),
            )
        with patch("job_runtime.launch") as launch:
            self.assertIsNone(schedule(self.store, self.pid))
            launch.assert_not_called()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET worker_active=0 WHERE id=?", (self.jid,))
        with patch("job_runtime.launch"):
            self.assertEqual(schedule(self.store, self.pid), one)

    def test_close_holds_chain_and_resume_restores_it(self):
        from close_behavior import stop_jobs

        one = self.retry()
        stop_jobs(self.store, lambda *a: None)
        self.assertEqual(self.job(one)["state"], "held")
        self.store.closing = False
        with patch("job_runtime.launch"):
            api(
                self.store,
                "control",
                {"id": self.jid, "mode": "resume", "preserve_project_settings": True},
            )
        self.assertEqual(self.job(one)["state"], "waiting")
        self.assertEqual(self.job(self.jid)["state"], "queued")

    def test_budget_saved_without_inference(self):
        with self.store.db() as db:
            b = json.loads(db.execute("SELECT budget_json FROM errors").fetchone()[0])
        self.assertEqual(b["configured"], 8192)
        self.assertTrue(b["approximate"])
        self.assertGreater(b["estimated_full"], 0)

    def test_manual_error_translation_resolves_and_protects(self):
        r = self.store.record(1)
        api(
            self.store,
            "manual-error",
            {
                "project": self.pid,
                "id": 1,
                "revision": r["revision"],
                "text": "Привет.",
            },
        )
        self.assertTrue(self.store.record(1)["manual"])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT resolved FROM errors").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.retry()

    def test_failed_launch_keeps_queue(self):
        one = self.retry()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (self.jid,))
        with patch("job_runtime.launch", side_effect=RuntimeError("fixture")):
            schedule(self.store, self.pid)
        self.assertEqual(self.job(one)["state"], "paused")
        self.assertEqual(self.job(one)["worker_active"], 0)

    def test_worker_completion_schedules_followup(self):
        from worker import run

        one = self.retry()

        class Fake:
            def __init__(self, p, k, s):
                self.settings = s

        with (
            patch("worker.process_record", return_value=("Привет.", "")),
            patch(
                "foreign_language.check_record",
                side_effect=lambda store, rid, *a, **k: store.record(rid),
            ),
            patch("job_runtime.launch") as launch,
        ):
            run(self.store, self.jid, Fake)
        launch.assert_called_once_with(self.store, one)
        self.assertEqual(self.job(one)["state"], "queued")
        self.assertEqual(self.job(self.jid)["worker_active"], 0)


if __name__ == "__main__":
    unittest.main()
