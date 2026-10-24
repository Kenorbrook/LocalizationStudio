import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from preservation import symbolic, folder
from app import records, api
from process_view import marks_page


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")
        self.pid = self.store.project(str(self.root))["id"]

    def tearDown(self):
        self.temp.cleanup()

    def corpus(self, items):
        path = self.root / "corpus.json"
        path.write_text(json.dumps(items), encoding="utf-8")
        self.store.import_files(self.pid, [path])
        with self.store.db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM records ORDER BY id")]

    def test_symbols_imported_literal_and_excluded_from_jobs(self):
        rows = self.corpus(
            [
                {"source": "……?! ♥ [name]"},
                {"source": "Hello."},
                {"source": "Hello."},
                {"source": "1234"},
            ]
        )
        literal = self.store.record(rows[0]["id"])
        self.assertEqual(literal["status"], "preserved")
        self.assertEqual(literal["source"], literal["text"])
        self.assertEqual(literal["preserve_kind"], "symbols")
        jid = self.store.create_job(self.pid, "translate", "local", {})
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT total FROM jobs WHERE id=?", (jid,)).fetchone()[0], 3
            )
        self.assertFalse(symbolic("Hello"))
        self.assertFalse(symbolic("123"))
        self.assertTrue(symbolic("{w}…"))

    def test_foreign_language_only_explicit_author_or_user(self):
        rows = self.corpus(
            [
                {"source": "Veni, vidi, vici."},
                {
                    "source": "Carpe diem.",
                    "preserve_original": True,
                    "original_language": "Latin",
                    "preserve_reason": "Авторская латинская реплика",
                },
            ]
        )
        self.assertEqual(rows[0]["status"], "empty")
        self.assertEqual(rows[1]["status"], "preserved")
        self.assertIn("латинская", self.store.record(rows[1]["id"])["preserve_reason"])
        self.assertEqual(folder(self.store, self.pid)["total"], 1)
        self.assertEqual(
            records(self.store, rows[1]["file"])["rows"][1]["preserve_kind"], "foreign"
        )

    def test_automatic_preservation_restores_translation_without_clearing_human_flag(
        self,
    ):
        row = self.corpus([{"source": "Carpe diem.", "translation": "Лови момент."}])[0]
        mark = self.store.mark(row["id"], 0, "review")
        preserved = self.store.preserve(
            row["id"], mark["revision"], "foreign", "Намеренно другой язык"
        )
        self.assertEqual(preserved["text"], "Carpe diem.")
        self.assertEqual(preserved["flag"], "review")
        with self.assertRaises(ValueError):
            self.store.update(
                row["id"], preserved["revision"], "Изменено", "edited", "local"
            )
        restored = self.store.restore_preserved(row["id"], preserved["revision"])
        self.assertEqual(restored["text"], "Лови момент.")
        self.assertEqual(restored["flag"], "review")

    def test_literal_can_be_manually_edited_and_restored(self):
        row = self.corpus([{"source": "…"}])[0]
        r = self.store.record(row["id"])
        edited = self.store.update(
            r["id"], r["revision"], "...", "preserved", "human", manual=True
        )
        self.assertEqual(edited["status"], "preserved")
        restored = self.store.restore_preserved(edited["id"], edited["revision"])
        self.assertEqual(restored["text"], "...")
        self.assertEqual(restored["manual"], 1)

    def test_pending_literal_completion_keeps_progress_consistent(self):
        rows = self.corpus([{"source": "Hello"}, {"source": "Carpe diem."}])
        jid = self.store.create_job(self.pid, "translate", "local", {})
        r = self.store.record(rows[1]["id"])
        self.store.preserve(r["id"], r["revision"], "foreign", "Латинская реплика")
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT done FROM jobs WHERE id=?", (jid,)).fetchone()[0], 1
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM queue WHERE record=?", (r["id"],)
                ).fetchone()[0],
                "done",
            )

    def test_restore_returns_pending_original_to_current_translation(self):
        rows = self.corpus([{"source": "Hello"}, {"source": "Carpe diem."}])
        jid = self.store.create_job(self.pid, "translate", "local", {})
        r = self.store.preserve(rows[1]["id"], 0, "foreign", "Латинская реплика")
        self.store.restore_preserved(r["id"], r["revision"])
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT done FROM jobs WHERE id=?", (jid,)).fetchone()[0], 0
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM queue WHERE record=?", (r["id"],)
                ).fetchone()[0],
                "pending",
            )

    def test_inflight_preservation_rejected_to_avoid_double_completion(self):
        r = self.corpus([{"source": "Hello"}])[0]
        jid = self.store.create_job(self.pid, "translate", "local", {})
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET state='running',current=? WHERE id=?", (r["id"], jid)
            )
        with self.assertRaisesRegex(ValueError, "Дождитесь"):
            self.store.preserve(r["id"], 0, "foreign", "Авторский приём")

    def test_existing_manual_symbol_edit_not_replaced(self):
        row = self.corpus([{"source": "Hello"}])[0]
        with self.store.db() as db:
            db.execute(
                "UPDATE records SET source='…',text='...',manual=1,status='translated' WHERE id=?",
                (row["id"],),
            )
        self.store.classify_preserved(self.pid)
        self.assertEqual(self.store.record(row["id"])["text"], "...")
        self.assertEqual(self.store.record(row["id"])["status"], "translated")

    def test_restore_exempts_symbol_from_automatic_reclassification(self):
        r = self.store.record(self.corpus([{"source": "..."}])[0]["id"])
        restored = self.store.restore_preserved(r["id"], r["revision"])
        self.store.classify_preserved(self.pid)
        self.assertEqual(self.store.record(r["id"])["status"], "empty")
        self.assertEqual(restored["preserve_override"], 1)

    def test_literal_folder_project_isolation(self):
        self.corpus([{"source": "..."}])
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        self.assertEqual(folder(self.store, pid)["total"], 0)

    def test_literal_check_does_not_use_model_or_modify_record(self):
        from app import api

        row = self.corpus([{"source": "..."}])[0]
        before = self.store.record(row["id"])
        with patch(
            "foreign_language.detect",
            side_effect=AssertionError("symbols need no language detector"),
        ):
            result = api(
                self.store,
                "preserved-check",
                {"project": self.pid, "record": row["id"]},
            )
        self.assertFalse(result["inference"])
        self.assertFalse(result["changed"])
        self.assertEqual(self.store.record(row["id"]), before)

    def test_literal_check_cannot_read_other_project(self):
        from app import api

        row = self.corpus([{"source": "..."}])[0]
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        with self.assertRaises(ValueError):
            api(self.store, "preserved-check", {"project": pid, "record": row["id"]})

    def test_user_preservation_resolves_review_and_keeps_original_reason(self):
        rows = self.corpus([{"source": "Carpe diem."}, {"source": "Hello."}])
        jid = self.store.create_job(self.pid, "translate", "local", {})
        for origin in ["human", "language"]:
            row = self.store.record(rows[0]["id"])
            row = self.store.mark(row["id"], row["revision"], "review", origin=origin)
            saved = api(
                self.store,
                "preserve",
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    "kind": "foreign",
                    "reason": "Авторский приём",
                },
            )
            self.assertEqual(
                (saved["status"], saved["text"], saved["flag"]),
                ("preserved", "Carpe diem.", ""),
            )
            self.assertEqual(saved["preserve_reason"], "Авторский приём")
            self.assertEqual(saved["reviewer"], "human:preserve")
            self.assertEqual(marks_page(self.store, self.pid, "review")["total"], 0)
            self.assertEqual(folder(self.store, self.pid)["total"], 1)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT done FROM jobs WHERE id=?", (jid,)).fetchone()[0], 1
            )
            self.assertEqual(
                db.execute(
                    "SELECT state FROM queue WHERE record=?", (rows[1]["id"],)
                ).fetchone()[0],
                "pending",
            )

    def test_user_preservation_does_not_clear_bad_or_unrelated_review(self):
        rows = self.corpus([{"source": "Carpe diem."}, {"source": "Hello."}])
        first = self.store.mark(rows[0]["id"], 0, "bad")
        second = self.store.mark(rows[1]["id"], 0, "review")
        api(
            self.store,
            "preserve",
            {
                "id": first["id"],
                "revision": first["revision"],
                "kind": "foreign",
                "reason": "Другой язык",
            },
        )
        self.assertEqual(self.store.record(first["id"])["flag"], "bad")
        self.assertEqual(self.store.record(second["id"])["flag"], "review")

    def test_stale_or_failed_user_preservation_does_not_clear_review(self):
        first = self.corpus([{"source": "Carpe diem."}])[0]
        marked = self.store.mark(first["id"], 0, "review")
        for revision, reason in [(0, "Другой язык"), (marked["revision"], "")]:
            with self.assertRaises(ValueError):
                api(
                    self.store,
                    "preserve",
                    {
                        "id": first["id"],
                        "revision": revision,
                        "kind": "foreign",
                        "reason": reason,
                    },
                )
            self.assertEqual(self.store.record(first["id"])["flag"], "review")
            self.assertEqual(self.store.record(first["id"])["status"], "empty")

    def test_migration_repairs_only_old_language_marks_and_preserves_new_decisions(
        self,
    ):
        rows = self.corpus([{"source": "Carpe diem."} for _ in range(5)])
        for index, row in enumerate(rows[:4]):
            row = self.store.mark(
                row["id"],
                0,
                "bad" if index == 3 else "review",
                origin="human" if index == 2 else "language",
            )
            preserved = self.store.preserve(
                row["id"], row["revision"], "foreign", "Другой язык"
            )
            if index == 1:
                self.store.mark(
                    row["id"], preserved["revision"], "review", origin="language"
                )
        self.store.mark(rows[4]["id"], 0, "review", origin="language")
        before = self.store.record(rows[0]["id"])
        with self.store.db() as db:
            db.execute(
                "DELETE FROM schema_versions WHERE name='preserved-language-review-marks-v1'"
            )
        reopened = Store(self.store.path)
        after = reopened.record(rows[0]["id"])
        self.assertEqual(after["flag"], "")
        for key in ["source", "text", "status", "preserve_reason"]:
            self.assertEqual(after[key], before[key])
        self.assertEqual(
            [reopened.record(row["id"])["flag"] for row in rows[1:]],
            ["review", "review", "bad", "review"],
        )
        # A new explicit review after repair survives another opening.
        later = reopened.mark(after["id"], after["revision"], "review")
        self.assertEqual(Store(self.store.path).record(later["id"])["flag"], "review")


if __name__ == "__main__":
    unittest.main()
