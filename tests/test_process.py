import json, tempfile, time, unittest
from pathlib import Path
from core import Store
from app import state, records
from process_view import queue_page, history_page, marks_page, save_preferences
from providers import process_record


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")
        self.pid = self.store.project(str(self.root))["id"]

    def tearDown(self):
        self.temp.cleanup()

    def corpus(self, count=60, name="corpus.json"):
        path = self.root / name
        path.write_text(
            json.dumps([{"source": f"Line {i}"} for i in range(count)]),
            encoding="utf-8",
        )
        self.store.import_files(self.pid, [path])
        with self.store.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM records ORDER BY id")]

    def finish(self, jid, ids):
        with self.store.db() as db:
            for rid in ids:
                db.execute(
                    "UPDATE records SET text='Готово',status='translated' WHERE id=?",
                    (rid,),
                )
                db.execute(
                    "UPDATE queue SET state='done' WHERE job=? AND record=?", (jid, rid)
                )

    def test_upcoming_window_remains_full_after_page_tail(self):
        rows = self.corpus(120)
        jid = self.store.create_job(self.pid, "translate", "local", {})
        self.finish(jid, [r["id"] for r in rows[:48]])
        page = queue_page(self.store, self.pid)
        self.assertEqual(len(page["rows"]), 50)
        self.assertEqual(page["rows"][0]["position"], 48)
        self.assertEqual(page["rows"][-1]["position"], 97)
        save_preferences(self.store, self.pid, {"page_size": 100})
        self.assertEqual(len(queue_page(self.store, self.pid)["rows"]), 72)

    def test_completed_results_survive_queue_and_cancellation(self):
        rows = self.corpus()
        jid = self.store.create_job(self.pid, "translate", "local", {})
        self.finish(jid, [rows[0]["id"], rows[1]["id"]])
        self.assertEqual(queue_page(self.store, self.pid)["total"], 58)
        history = history_page(self.store, self.pid)
        self.assertEqual(history["total"], 2)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (jid,))
        self.assertEqual(queue_page(self.store, self.pid)["rows"], [])
        self.assertEqual(history_page(self.store, self.pid)["total"], 2)

    def test_history_limits_hide_but_never_delete_records(self):
        rows = self.corpus()
        jid = self.store.create_job(self.pid, "translate", "local", {})
        self.finish(jid, [r["id"] for r in rows])
        stamp = time.time()
        with self.store.db() as db:
            for index, row in enumerate(rows):
                db.execute(
                    "UPDATE queue SET finished_at=? WHERE job=? AND record=?",
                    (stamp - 60 + index, jid, row["id"]),
                )
        save_preferences(self.store, self.pid, {"max_phrases": 5, "max_seconds": 0})
        self.assertEqual(history_page(self.store, self.pid)["total"], 5)
        save_preferences(self.store, self.pid, {"max_phrases": 0, "max_seconds": 10})
        self.assertEqual(history_page(self.store, self.pid)["total"], 9)
        save_preferences(self.store, self.pid, {"max_phrases": 0, "max_seconds": 0})
        page = history_page(self.store, self.pid, 50)
        self.assertEqual(page["total"], 60)
        self.assertEqual(len(page["rows"]), 10)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM records").fetchone()[0], 60
            )

    def test_project_isolation_and_mark_folders(self):
        rows = self.corpus(2)
        marked = self.store.mark(rows[0]["id"], 0, "bad")
        self.assertEqual(marked["manual"], 0)
        self.store.mark(rows[1]["id"], 0, "review")
        self.assertEqual(marks_page(self.store, self.pid, "bad")["total"], 1)
        self.assertEqual(state(self.store, self.pid)["flags"], {"bad": 1, "review": 1})
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        self.assertEqual(marks_page(self.store, pid, "bad")["total"], 0)
        self.assertEqual(history_page(self.store, pid)["total"], 0)
        restored = self.store.mark(marked["id"], marked["revision"], "")
        self.assertEqual(restored["manual"], 0)
        self.assertEqual(marks_page(self.store, self.pid, "bad")["total"], 0)

    def test_mark_blocks_inflight_revision_and_preserves_manual_edits(self):
        rows = self.corpus(1)
        r = rows[0]
        marked = self.store.mark(r["id"], 0, "review")
        with self.assertRaises(ValueError):
            self.store.update(r["id"], 0, "Готово", "translated", "local")
        edited = self.store.update(
            r["id"], marked["revision"], "Исправлено", "verified", "human", manual=True
        )
        restored = self.store.mark(r["id"], edited["revision"], "")
        self.assertEqual(restored["manual"], 1)
        self.assertEqual(restored["text"], "Исправлено")

    def test_undo_manual_edit_after_mark_and_unmark(self):
        r = self.corpus(1)[0]
        edited = self.store.update(
            r["id"], 0, "Исправлено", "translated", "human", manual=True
        )
        marked = self.store.mark(r["id"], edited["revision"], "bad")
        restored = self.store.mark(r["id"], marked["revision"], "")
        undone = self.store.undo(r["id"], restored["revision"])
        self.assertEqual(undone["text"], "")
        self.assertEqual(undone["manual"], 0)

    def test_marked_bad_text_is_not_a_confirmed_voice_example(self):
        from speaker_context import context_entry

        rows = self.corpus(2)
        with self.store.db() as db:
            db.execute("UPDATE records SET speaker='Pilot'")
        saved = self.store.update(
            rows[0]["id"], 0, "Готово", "verified", "human", manual=True
        )
        marked = self.store.mark(saved["id"], saved["revision"], "bad")
        self.assertEqual(marked["status"], "verified")
        self.assertEqual(
            context_entry(self.store, rows[1]["id"])["confirmed_character_examples"], []
        )

    def test_human_confirmation_clears_mark_and_keeps_manual_protection(self):
        row = self.corpus(1)[0]
        marked = self.store.mark(row["id"], 0, "review")
        saved = self.store.update(
            row["id"], marked["revision"], "Готово", "verified", "human", manual=True
        )
        self.assertEqual(saved["flag"], "")
        self.assertEqual(saved["manual"], 1)
        self.assertEqual(marks_page(self.store, self.pid, "review")["total"], 0)

    def test_latest_stage_deduplicates_phrase_history(self):
        row = self.corpus(1)[0]
        jid = self.store.create_job(self.pid, "translate", "local", {})
        self.finish(jid, [row["id"]])
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (jid,))
        review = self.store.create_job(self.pid, "review", "local", {})
        self.finish(review, [row["id"]])
        self.assertEqual(history_page(self.store, self.pid)["total"], 1)

    def test_punctuation_is_literal_and_never_calls_model(self):
        class Forbidden:
            def call(self, *args):
                raise AssertionError("No generation for punctuation")

        for source in ["......", "…", "?!", "—", "♥"]:
            text, _ = process_record(
                Forbidden(), "translate", {"id": 1, "english": source, "russian": ""}
            )
            self.assertEqual(text, source)

    def test_mixed_alphabet_word_is_rejected_but_source_identifier_allowed(self):
        class Model:
            settings = {"target_language": "Russian"}

            def call(self, mode, entries):
                return {"items": [{"id": 1, "text": "Теst готов."}]}

        with self.assertRaisesRegex(ValueError, "Смешаны"):
            process_record(
                Model(),
                "translate",
                {"id": 1, "english": "The test is ready.", "russian": ""},
            )
        text, _ = process_record(
            Model(), "translate", {"id": 1, "english": "Теst is ready.", "russian": ""}
        )
        self.assertEqual(text, "Теst готов.")

    def test_marked_phrase_gets_normal_review_and_flag_survives(self):
        from unittest.mock import patch
        from worker import run

        r = self.corpus(1)[0]
        saved = self.store.update(r["id"], 0, "Готово", "translated", "local")
        self.store.mark(r["id"], saved["revision"], "bad")
        jid = self.store.create_job(self.pid, "review", "local", {})

        class Fake:
            def __init__(self, *args):
                self.last_model = "fixture"

        with patch(
            "worker.process_record", return_value=("Исправлено", "reviewed")
        ) as process:
            run(self.store, jid, Fake)
        row = self.store.record(r["id"])
        self.assertEqual(row["status"], "edited")
        self.assertEqual(row["flag"], "bad")
        self.assertEqual(row["manual"], 0)
        self.assertEqual(process.call_args.args[2]["human_review_flag"], "bad")

    def test_folder_rereview_scope_and_manual_permission(self):
        rows = self.corpus(3)
        for r in rows:
            self.store.update(r["id"], 0, "Готово", "edited", "local")
        for r, kind in zip(rows, ["bad", "bad", "review"]):
            self.store.mark(r["id"], 1, kind)
        manual = self.store.record(rows[1]["id"])
        self.store.update(
            manual["id"], manual["revision"], "Ручное", "edited", "human", manual=True
        )
        jid = self.store.create_job(self.pid, "review", "local", {}, mark_kind="bad")
        with self.store.db() as db:
            self.assertEqual(
                [
                    r[0]
                    for r in db.execute("SELECT record FROM queue WHERE job=?", (jid,))
                ],
                [rows[0]["id"]],
            )
            db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (jid,))
        jid = self.store.create_job(
            self.pid, "review", "local", {}, mark_kind="bad", allow_manual=True
        )
        from unittest.mock import patch
        from worker import run

        class Fake:
            def __init__(self, *args):
                self.last_model = "fixture"

        with patch(
            "worker.process_record", return_value=("Новая редакция", "reviewed")
        ):
            run(self.store, jid, Fake)
        self.assertEqual(self.store.record(rows[1]["id"])["text"], "Новая редакция")
        self.assertEqual(self.store.record(rows[1]["id"])["manual"], 1)
        self.assertEqual(self.store.record(rows[1]["id"])["flag"], "bad")
        self.assertEqual(self.store.record(rows[2]["id"])["text"], "Готово")

    def test_bulk_unmark_is_project_and_kind_scoped_and_keeps_text(self):
        rows = self.corpus(2)
        self.store.mark(rows[0]["id"], 0, "bad")
        self.store.mark(rows[1]["id"], 0, "review")
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        path = other / "test.json"
        path.write_text('[{"source":"Other"}]')
        self.store.import_files(pid, [path])
        self.store.mark(3, 0, "bad")
        self.assertEqual(self.store.clear_marks(self.pid, "bad")["cleared"], 1)
        self.assertEqual(self.store.record(3)["flag"], "bad")
        self.assertEqual(self.store.record(rows[1]["id"])["flag"], "review")
        self.assertEqual(self.store.record(rows[0]["id"])["text"], "")

    def test_old_flag_protection_migrates_once(self):
        row = self.corpus(1)[0]
        with self.store.db() as db:
            db.execute("ALTER TABLE record_marks DROP COLUMN independent")
            db.execute("ALTER TABLE record_marks DROP COLUMN origin")
            db.execute(
                "UPDATE records SET manual=1,revision=1 WHERE id=?", (row["id"],)
            )
            db.execute(
                "INSERT INTO record_marks VALUES (?,'bad',?,0,1)",
                (row["id"], time.time()),
            )
        migrated = Store(self.store.path)
        self.assertEqual(migrated.record(row["id"])["manual"], 0)
        self.assertEqual(migrated.record(row["id"])["flag"], "bad")
        marked = migrated.record(row["id"])
        migrated.update(
            marked["id"], marked["revision"], "Ручное", "edited", "human", manual=True
        )
        self.assertEqual(Store(self.store.path).record(row["id"])["manual"], 1)

    def test_review_prompt_uses_human_instruction(self):
        from providers import Provider

        provider = object.__new__(Provider)
        provider.settings = {
            "marked_review": "bad",
            "review_instruction": "Check carefully.",
        }
        provider.profile = {"critic_prompt": "BASE"}
        provider.rules = {}
        provider.policy = "POLICY"
        prompt = provider.prompt("critic")
        self.assertIn("human_review_flag", prompt)
        self.assertIn("Check carefully.", prompt)

    def test_history_settings_validation(self):
        for values in [
            {"max_phrases": -1},
            {"max_seconds": float("inf")},
            {"page_size": 1},
            {"max_phrases": 2.5},
        ]:
            with self.assertRaises(ValueError):
                save_preferences(self.store, self.pid, values)


if __name__ == "__main__":
    unittest.main()
