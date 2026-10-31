import io
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from error_catalog import page, repair_candidates
from job_chain import enqueue_retry
from queries import state
from script_validation import MIXED_SCRIPT_PREFIX
from worker import run


class ErrorCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.sqlite3")
        self.project = self.store.project(str(self.root))["id"]
        corpus = self.root / "text.json"
        corpus.write_text(
            json.dumps(
                [
                    {"source": "I copy the note."},
                    {"source": "I copy the note."},
                    {"source": "Goodbye."},
                ]
            )
        )
        self.store.import_files(self.project, [corpus])
        self.settings = {
            "model": "fixture",
            "source_language": "English",
            "target_language": "Russian",
            "context": 8192,
            "max_output": 1200,
            "auto_foreign": False,
        }
        with self.store.db() as db:
            db.execute(
                "update projects set settings=? where id=?",
                (json.dumps(self.settings), self.project),
            )
        self.job = self.store.create_job(
            self.project, "translate", "local", self.settings
        )
        self.message = MIXED_SCRIPT_PREFIX + "перепиcываю"

    def tearDown(self):
        self.temp.cleanup()

    def errors(self, record=1, count=3):
        for _ in range(count):
            self.store.error(self.job, record, self.message)

    def enqueue(self, record=None):
        with patch("job_runtime.launch"):
            return enqueue_retry(
                self.store,
                {
                    "project": self.project,
                    "record": record,
                    "stage": "translate",
                    "mode": "repair",
                },
            )

    def test_one_card_per_record_and_attempts_preserved(self):
        self.errors()
        self.errors(2, 1)
        rows = page(self.store, self.project)
        self.assertEqual(len(rows), 2)
        row = next(r for r in rows if r["record"] == 1)
        self.assertEqual(row["attempt_count"], 3)
        self.assertEqual(row["id"], row["attempts"][0]["id"])
        self.assertEqual(len(row["attempts"]), 3)
        self.assertTrue(row["repairable"])
        self.assertEqual(state(self.store, self.project)["errors"], 2)
        self.assertEqual(state(self.store, self.project)["repairable_errors"], 2)
        with self.store.db() as db:
            self.assertEqual(db.execute("select count(*) from errors").fetchone()[0], 4)

    def test_import_errors_are_separate_and_other_project_is_isolated(self):
        self.errors()
        (self.root / "other").mkdir()
        other = self.store.project(str(self.root / "other"))["id"]
        with self.store.db() as db:
            for _ in range(2):
                db.execute(
                    "insert into errors(project,message,at) values (?,?,?)",
                    (self.project, "Import error", "fixture"),
                )
            db.execute(
                "insert into errors(project,record,message,at) values (?,?,?,?)",
                (other, 1, self.message, "fixture"),
            )
        self.assertEqual(len(page(self.store, self.project)), 3)
        self.assertEqual(state(self.store, self.project)["errors"], 3)
        self.assertEqual(state(self.store, other)["errors"], 1)

    def test_manual_save_resolves_all_attempts_without_erasing_history(self):
        self.errors()
        self.store.update(
            1, 0, "Я переписываю записку.", "translated", "human", manual=True
        )
        self.assertEqual(page(self.store, self.project), [])
        self.assertEqual(state(self.store, self.project)["errors"], 0)
        with self.store.db() as db:
            self.assertEqual(
                [r[0] for r in db.execute("select resolved from errors")], [1, 1, 1]
            )

    def test_manual_error_cannot_confirm_the_unchanged_invalid_draft(self):
        from commands.records import manual_error

        self.errors()
        with self.assertRaisesRegex(ValueError, "U\\+0063"):
            manual_error(
                self.store,
                {
                    "project": self.project,
                    "id": 1,
                    "revision": 0,
                    "text": "Я перепиcываю записку.",
                },
            )
        self.assertEqual(self.store.record(1)["revision"], 0)
        self.assertEqual(page(self.store, self.project)[0]["attempt_count"], 3)

    def test_only_latest_error_determines_repair_and_protected_rows_are_excluded(self):
        self.errors()
        self.store.error(self.job, 1, "HTTP 500: memory")
        self.errors(2, 1)
        with self.store.db() as db:
            db.execute("update records set manual=1 where id=2")
            self.assertEqual(repair_candidates(db, self.project), [])
        self.assertFalse(any(r["repairable"] for r in page(self.store, self.project)))
        with self.assertRaisesRegex(ValueError, "Нет подходящих"):
            self.enqueue()

    def test_repair_is_fifo_unique_frozen_local_and_has_fresh_cache(self):
        self.errors()
        self.errors(2, 1)
        result = self.enqueue()
        again = self.enqueue()
        self.assertEqual(result["records"], 2)
        self.assertTrue(again["already_queued"])
        self.assertEqual(result["job"], again["job"])
        with self.store.db() as db:
            job = dict(
                db.execute("select * from jobs where id=?", (result["job"],)).fetchone()
            )
            settings = json.loads(job["settings"])
            self.assertEqual(job["state"], "waiting")
            self.assertEqual(job["provider"], "local")
            self.assertEqual(set(settings["_error_repair"]), {"1", "2"})
            self.assertIn("script-repair", settings["cache_namespace"])
            self.assertEqual(
                [
                    r[0]
                    for r in db.execute(
                        "select record from queue where job=? order by record",
                        (job["id"],),
                    )
                ],
                [1, 2],
            )
            self.assertEqual(
                json.loads(db.execute("select settings from projects").fetchone()[0]),
                self.settings,
            )
            db.execute(
                "update projects set settings=?",
                (json.dumps({**self.settings, "model": "changed"}),),
            )
            self.assertEqual(
                json.loads(
                    db.execute(
                        "select settings from jobs where id=?", (job["id"],)
                    ).fetchone()[0]
                )["model"],
                "fixture",
            )

    def test_feedback_reaches_provider_and_resolved_later_rows_are_skipped(self):
        self.errors()
        self.errors(2, 1)
        result = self.enqueue()
        # Another earlier queue has already fixed record 2 before this job starts.
        self.store.update(2, 0, "Ранее исправлено.", "translated", "local:fixture")
        with self.store.db() as db:
            db.execute("update jobs set state='incomplete' where id=?", (self.job,))
            db.execute("update jobs set state='queued' where id=?", (result["job"],))
        generated = []

        def respond(request, **kwargs):
            if request.full_url.endswith("/api/tags"):
                return io.BytesIO(b'{"models":[{"name":"fixture"}]}')
            payload = json.loads(request.data)
            item = json.loads(payload["messages"][-1]["content"])["items"][0]
            generated.append(item)
            self.assertIn("U+0063", item["validation_feedback"])
            self.assertIn("U+043F", item["validation_feedback"])
            text = "Я переписываю записку."
            return io.BytesIO(
                json.dumps(
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": item["id"], "text": text}]}
                            )
                        }
                    }
                ).encode()
            )

        with patch("providers.DIRECT.open", side_effect=respond):
            run(self.store, result["job"])
        self.assertEqual(len(generated), 1)
        self.assertEqual(self.store.record(1)["text"], "Я переписываю записку.")
        self.assertEqual(self.store.record(2)["text"], "Ранее исправлено.")
        self.assertEqual(self.store.record(2)["revision"], 1)
        self.assertEqual(page(self.store, self.project), [])

    def test_pagination_counts_records_not_attempts(self):
        corpus = self.root / "many.json"
        corpus.write_text(
            json.dumps([{"source": f"Neutral line {n}."} for n in range(101)])
        )
        self.store.import_files(self.project, [corpus])
        with self.store.db() as db:
            ids = [
                r[0]
                for r in db.execute(
                    "select id from records where file=(select id from files where path=?)",
                    (str(corpus),),
                )
            ]
            for rid in ids:
                for _ in range(3):
                    db.execute(
                        "insert into errors(project,job,record,message,at,budget_json) values (?,?,?,?,?,?)",
                        (
                            self.project,
                            self.job,
                            rid,
                            self.message,
                            "fixture",
                            '{"configured":8192}',
                        ),
                    )
        self.assertEqual(len(page(self.store, self.project)), 100)
        self.assertEqual(len(page(self.store, self.project, 100)), 1)
        self.assertEqual(state(self.store, self.project)["errors"], 101)

    def test_legacy_error_schema_keeps_history_and_adds_empty_candidate(self):
        path = self.root / "legacy.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE errors(id INTEGER PRIMARY KEY,project INTEGER,job INTEGER,record INTEGER,message TEXT,at TEXT,resolved INTEGER DEFAULT 0)"
            )
            db.execute(
                "INSERT INTO errors VALUES (1,1,NULL,NULL,'Old import error','fixture',0)"
            )
            db.commit()
        migrated = Store(path)
        with migrated.db() as db:
            row = dict(db.execute("select * from errors").fetchone())
            self.assertEqual(row["message"], "Old import error")
            self.assertEqual(row["resolved"], 0)
            self.assertEqual(row["candidate"], "")
