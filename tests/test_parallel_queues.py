import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import patch

from commands import dispatch
from core import Store
from process_view import queue_page
from storage.jobs import split_tail
from worker import run


class ParallelQueueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = Store(root / "test.sqlite3")
        self.project = self.store.project(str(root))["id"]
        corpus = root / "corpus.json"
        corpus.write_text(
            json.dumps([{"source": f"Neutral line {i}."} for i in range(1, 9)])
        )
        self.store.import_files(self.project, [corpus])
        with self.store.db() as db:
            db.execute(
                "UPDATE records SET scene=CASE WHEN id<=4 THEN 'start' ELSE 'end' END"
            )
        self.options = {
            "model": "local-fixture",
            "source_language": "English",
            "target_language": "Russian",
            "auto_foreign": False,
        }

    def rows(self, jid):
        with self.store.db() as db:
            return [
                row[0]
                for row in db.execute(
                    "SELECT record FROM queue WHERE job=? ORDER BY record", (jid,)
                )
            ]

    def parallel(self, **values):
        return dispatch(
            self.store,
            "parallel-job",
            {
                "project": self.project,
                "stage": "translate",
                "provider": "local",
                "settings": self.options,
                **values,
            },
        )["job"]

    def test_parallel_review_keeps_its_snapshot_and_both_workers_execute(self):
        self.store.update(1, 0, "Готовая строка.", "translated", "fixture")
        translation = self.store.create_job(
            self.project, "translate", "local", self.options
        )
        cloud_settings = {
            **self.options,
            "endpoint": "https://example.com/v1",
            "cloud_model": "editor",
        }
        with (
            patch("job_runtime.launch"),
            patch("cloud_connections.job_key", return_value="fixture"),
        ):
            review = self.parallel(
                stage="review", provider="cloud", settings=cloud_settings
            )
        self.assertEqual(self.rows(review), [1])
        self.assertEqual(self.rows(translation), list(range(2, 9)))
        self.assertEqual(set(self.rows(review)) & set(self.rows(translation)), set())
        entered = Barrier(2)

        class FakeProvider:
            def __init__(self, project, provider, options):
                self.calls = 0

        def generate(provider, stage, entry):
            if entry["id"] in (1, 2):
                entered.wait(timeout=10)
            return "Обработанная строка.", "test"

        with patch("worker.process_record", side_effect=generate):
            threads = [
                Thread(target=run, args=(self.store, jid, FakeProvider))
                for jid in (review, translation)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
                self.assertFalse(thread.is_alive())
        self.assertEqual(self.store.record(1)["status"], "edited")
        self.assertTrue(
            all(self.store.record(rid)["status"] == "translated" for rid in range(2, 9))
        )
        with self.store.db() as db:
            jobs = db.execute(
                "SELECT state,done,total FROM jobs ORDER BY id"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in jobs], [("done", 7, 7), ("done", 1, 1)]
            )

    def test_split_moves_a_scene_and_keeps_current_and_finished_rows(self):
        source = self.store.create_job(self.project, "translate", "local", self.options)
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET state='running',done=1,current=5,heartbeat=? WHERE id=?",
                (time.time(), source),
            )
            db.execute(
                "UPDATE queue SET state='done' WHERE job=? AND record=1", (source,)
            )
        with patch("job_runtime.launch"):
            new = self.parallel(split_from=source, take=4)
        self.assertEqual(self.rows(new), [6, 7, 8])
        self.assertEqual(self.rows(source), [1, 2, 3, 4, 5])
        with self.store.db() as db:
            self.assertEqual(
                tuple(
                    db.execute(
                        "SELECT done,total,current FROM jobs WHERE id=?", (source,)
                    ).fetchone()
                ),
                (1, 5, 5),
            )
        self.assertEqual(
            {r["id"] for r in queue_page(self.store, self.project, new)["rows"]},
            {6, 7, 8},
        )

    def test_reservations_exclude_other_queues_and_resume_is_independent(self):
        with patch("job_runtime.launch") as launch:
            first = self.parallel(take=3)
            second = self.parallel(take=3)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET state='paused' WHERE id=?", (first,))
                db.execute(
                    "UPDATE jobs SET state='running',heartbeat=? WHERE id=?",
                    (time.time(), second),
                )
            dispatch(
                self.store,
                "control",
                {"id": first, "mode": "resume", "preserve_project_settings": True},
            )
            launch.assert_called_with(self.store, first)
            self.assertEqual(self.rows(first), [1, 2, 3])
            self.assertEqual(self.rows(second), [4, 5, 6])
            self.assertEqual(self.rows(self.parallel()), [7, 8])
            with self.assertRaisesRegex(ValueError, "Нет свободных"):
                self.parallel()

    def test_prefers_boundary_and_handles_one_long_scene(self):
        rows = [(1, 1, "a"), (2, 1, "a"), (3, 1, "a"), (4, 2, "b"), (5, 2, "b")]
        self.assertEqual(split_tail(rows, 3), rows[-2:])
        self.assertEqual(split_tail(rows[:3], 2), rows[1:3])
