import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import Store
from worker_supervision import process_alive, recover_workers, watch_worker
from queries import state


class WorkerSupervisionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.store = Store(self.root / "test.db")
        self.project = self.store.project(str(self.root))["id"]
        corpus = self.root / "corpus.json"
        corpus.write_text(json.dumps([{"source": "Hello"}, {"source": "Goodbye"}]))
        self.store.import_files(self.project, [corpus])
        self.job = self.store.create_job(self.project, "translate", "local", {})

    def running(self, status="running", pid=1234):
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET state=?,worker_active=1,pid=?,heartbeat=? WHERE id=?",
                (status, pid, time.time() - 90, self.job),
            )

    def read(self):
        with self.store.db() as db:
            return dict(
                db.execute("SELECT * FROM jobs WHERE id=?", (self.job,)).fetchone()
            )

    def test_actual_process_liveness(self):
        self.assertTrue(process_alive(os.getpid()))
        self.assertFalse(process_alive(None))
        self.assertFalse(process_alive(-1))

    def test_crash_pauses_and_keeps_unfinished_queue_and_saved_translation(self):
        self.running()
        with self.store.db() as db:
            rid = db.execute(
                "SELECT min(record) FROM queue WHERE job=?", (self.job,)
            ).fetchone()[0]
            db.execute(
                "UPDATE queue SET state='done' WHERE job=? AND record=?",
                (self.job, rid),
            )
            db.execute("UPDATE records SET text='Привет',revision=1 WHERE id=?", (rid,))
            db.execute("UPDATE jobs SET done=1 WHERE id=?", (self.job,))
        watch_worker(self.store, self.job, Mock(pid=1234, **{"wait.return_value": 1}))
        row = self.read()
        self.assertEqual(
            (row["state"], row["worker_active"], row["done"]), ("paused", 0, 1)
        )
        self.assertIn("код 1", row["error"])
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM queue WHERE state='pending'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT text,revision FROM records WHERE id=?", (rid,)
                ).fetchone()[:],
                ("Привет", 1),
            )

    def test_unexpected_zero_exit_is_not_success(self):
        self.running()
        watch_worker(self.store, self.job, Mock(pid=1234, **{"wait.return_value": 0}))
        self.assertEqual(self.read()["state"], "paused")

    def test_terminal_and_user_pause_states_are_preserved(self):
        for status in ["paused", "cancelled", "done", "failed", "waiting", "held"]:
            with self.subTest(status=status):
                self.running(status)
                watch_worker(
                    self.store, self.job, Mock(pid=1234, **{"wait.return_value": 1})
                )
                self.assertEqual(self.read()["state"], status)
                self.assertEqual(self.read()["worker_active"], 0)

    def test_old_watcher_cannot_pause_a_resumed_worker(self):
        self.running(pid=5678)
        watch_worker(self.store, self.job, Mock(pid=1234, **{"wait.return_value": 1}))
        self.assertEqual(
            (self.read()["state"], self.read()["worker_active"]), ("running", 1)
        )

    def test_launcher_pid_can_differ_from_claimed_worker_pid(self):
        self.running(pid=5678)
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET worker_launch='attempt-1' WHERE id=?", (self.job,)
            )
        watch_worker(
            self.store,
            self.job,
            Mock(pid=1234, **{"wait.return_value": 1}),
            "attempt-1",
        )
        self.assertEqual(
            (self.read()["state"], self.read()["worker_active"]), ("paused", 0)
        )

    def test_old_generation_cannot_change_reused_pid(self):
        self.running(pid=1234)
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET worker_launch='attempt-2' WHERE id=?", (self.job,)
            )
        watch_worker(
            self.store,
            self.job,
            Mock(pid=1234, **{"wait.return_value": 1}),
            "attempt-1",
        )
        self.assertEqual(
            (self.read()["state"], self.read()["worker_active"]), ("running", 1)
        )

    def test_recovery_on_ui_poll_clears_dead_worker_and_holds_chain(self):
        self.running()
        following = self.store.create_job(
            self.project, "translate", "local", {}, defer=True
        )
        with (
            patch("worker_supervision.process_alive", return_value=False),
            patch("job_runtime.launch") as launch,
        ):
            result = state(self.store, self.project)
        self.assertEqual(result["jobs"][0]["state"], "paused")
        self.assertEqual(self.read()["worker_active"], 0)
        launch.assert_not_called()
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM jobs WHERE id=?", (following,)
                ).fetchone()[0],
                "waiting",
            )

    def test_recovery_clears_orphaned_paused_flag(self):
        self.running("paused")
        with patch("worker_supervision.process_alive", return_value=False):
            self.assertEqual(recover_workers(self.store), [self.job])
        self.assertEqual(
            (self.read()["state"], self.read()["worker_active"]), ("paused", 0)
        )

    def test_dead_worker_fails_only_pending_sampling_requests(self):
        self.running()
        with self.store.db() as db:
            for status in ["pending", "sent", "done"]:
                db.execute(
                    "INSERT INTO mcp_requests(id,job,state,response) VALUES (?,?,?,?)",
                    (status, self.job, status, "original"),
                )
        watch_worker(self.store, self.job, Mock(pid=1234, **{"wait.return_value": 1}))
        with self.store.db() as db:
            rows = {
                row["id"]: dict(row) for row in db.execute("SELECT * FROM mcp_requests")
            }
        self.assertEqual(rows["pending"]["state"], "failed")
        self.assertEqual(rows["sent"]["state"], "failed")
        self.assertEqual(
            (rows["done"]["state"], rows["done"]["response"]), ("done", "original")
        )

    def test_live_slow_or_uninspectable_process_is_not_paused(self):
        self.running()
        for alive in [True, None]:
            with patch("worker_supervision.process_alive", return_value=alive):
                self.assertEqual(recover_workers(self.store), [])
        self.assertEqual(self.read()["state"], "running")

    def test_recovery_rechecks_heartbeat_before_writing(self):
        self.running()

        def updated_while_checking(_):
            with self.store.db() as db:
                db.execute(
                    "UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), self.job)
                )
            return False

        with patch(
            "worker_supervision.process_alive", side_effect=updated_while_checking
        ):
            self.assertEqual(recover_workers(self.store), [])
        self.assertEqual(self.read()["state"], "running")

    def test_launching_worker_gets_grace_period(self):
        self.running("queued", pid=None)
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET heartbeat=? WHERE id=?", (time.time(), self.job)
            )
        with patch("worker_supervision.process_alive") as alive:
            self.assertEqual(recover_workers(self.store), [])
            alive.assert_not_called()

    def test_resume_rejects_live_worker_even_with_old_heartbeat(self):
        from commands.jobs import control

        self.running("paused")
        with (
            patch("worker_supervision.process_alive", return_value=True),
            patch("job_runtime.launch") as launch,
        ):
            with self.assertRaises(ValueError):
                control(self.store, {"id": self.job, "mode": "resume"})
        launch.assert_not_called()
