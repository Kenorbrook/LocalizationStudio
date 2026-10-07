import json
import tempfile
import unittest
from pathlib import Path

from core import Store
from commands import dispatch
from process_view import marks_page


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "workspace.sqlite3")
        self.pid = self.store.project(self.root)["id"]
        source = self.root / "corpus.json"
        source.write_text(
            json.dumps(
                [{"source": f"Line {i}", "translation": "Текст"} for i in range(52)]
            ),
            encoding="utf-8",
        )
        self.store.import_files(self.pid, [source])

    def mark(self, rid=1, kind="review", origin="human"):
        return self.store.mark(rid, self.store.record(rid)["revision"], kind, origin)

    def verify(self, row, text=None):
        return dispatch(
            self.store,
            "save",
            {
                "id": row["id"],
                "revision": row["revision"],
                "text": text or row["text"],
                "verified": True,
            },
        )

    def test_human_verification_clears_both_kinds_without_losing_manual_protection(
        self,
    ):
        for rid, kind in [(1, "bad"), (2, "review")]:
            with self.subTest(kind=kind):
                checked = self.verify(self.mark(rid, kind))
                self.assertEqual(
                    (checked["status"], checked["flag"], checked["manual"]),
                    ("verified", "", 1),
                )
                self.assertEqual(marks_page(self.store, self.pid, kind)["total"], 0)

    def test_model_review_keeps_mark_and_human_can_cancel_check_without_reverting_text(
        self,
    ):
        row = self.mark(1, "bad")
        edited = self.store.update(
            1, row["revision"], "Исправлено", "edited", "local:fixture"
        )
        self.assertEqual(edited["flag"], "bad")
        cloud = self.store.update(
            1, edited["revision"], "Исправлено", "verified", "cloud:fixture"
        )
        self.assertEqual(cloud["flag"], "bad")
        checked = self.verify(cloud, "Ручной вариант")
        restored = dispatch(
            self.store, "unverify", {"id": 1, "revision": checked["revision"]}
        )
        self.assertEqual(
            (
                restored["status"],
                restored["flag"],
                restored["text"],
                restored["manual"],
            ),
            ("translated", "bad", "Ручной вариант", 1),
        )

    def test_unverify_restores_edited_stage_and_language_mark(self):
        row = self.store.update(1, 0, "Редакция", "edited", "local:fixture")
        row = self.mark(1, origin="language")
        checked = self.verify(row)
        restored = self.store.unverify(1, checked["revision"])
        self.assertEqual(
            (restored["status"], restored["flag"], restored["text"]),
            ("edited", "review", "Редакция"),
        )
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT origin FROM record_marks WHERE record=1").fetchone()[
                    0
                ],
                "language",
            )

    def test_stale_verification_does_not_clear_mark_and_stale_cancel_does_not_restore_it(
        self,
    ):
        row = self.mark()
        with self.assertRaises(ValueError):
            self.verify({**row, "revision": 0})
        self.assertEqual(self.store.record(1)["flag"], "review")
        checked = self.verify(row)
        with self.assertRaises(ValueError):
            self.store.unverify(1, row["revision"])
        self.assertEqual(self.store.record(1)["flag"], "")
        self.assertEqual(self.store.record(1)["revision"], checked["revision"])

    def test_verified_row_retained_only_in_its_folder_session_and_project(self):
        for rid in range(1, 53):
            self.mark(rid)
        first = marks_page(self.store, self.pid, "review")["rows"][0]
        checked = self.verify(first)
        retained = marks_page(self.store, self.pid, "review", retained=[checked["id"]])
        self.assertEqual(retained["total"], 52)
        self.assertEqual(retained["rows"][0]["id"], checked["id"])
        self.assertEqual(retained["rows"][0]["flag"], "")
        self.assertEqual(
            marks_page(
                self.store, self.pid, "review", offset=50, retained=[checked["id"]]
            )["rows"][0]["id"],
            2,
        )
        self.assertEqual(marks_page(self.store, self.pid, "review")["total"], 51)
        self.assertEqual(
            marks_page(self.store, self.pid, "bad", retained=[checked["id"]])["total"],
            0,
        )
        other = self.root / "other"
        other.mkdir()
        other_pid = self.store.project(other)["id"]
        self.assertEqual(
            marks_page(self.store, other_pid, "review", retained=[checked["id"]])[
                "total"
            ],
            0,
        )

    def test_editing_checked_text_demotes_it_and_restores_review_mark(self):
        checked = self.verify(self.mark())
        changed = dispatch(
            self.store,
            "save",
            {"id": 1, "revision": checked["revision"], "text": "Новая правка"},
        )
        self.assertEqual(
            (changed["status"], changed["flag"], changed["text"]),
            ("translated", "review", "Новая правка"),
        )

    def test_legacy_marks_before_check_are_repaired_but_new_marks_are_kept(self):
        before = self.mark(1)
        with self.store.db() as db:
            old = dict(
                db.execute("SELECT * FROM record_marks WHERE record=1").fetchone()
            )
        checked = self.verify(before)
        later = self.verify(self.mark(2))
        later = self.mark(2, "bad")
        with self.store.db() as db:
            db.execute(
                "DELETE FROM schema_versions WHERE name='human-verification-marks-v1'"
            )
            db.execute("DELETE FROM verification_snapshots WHERE record=1")
            db.execute(
                "INSERT INTO record_marks(record,kind,at,was_manual,protected_revision,origin) VALUES (?,?,?,?,?,?)",
                tuple(
                    old[key]
                    for key in [
                        "record",
                        "kind",
                        "at",
                        "was_manual",
                        "protected_revision",
                        "origin",
                    ]
                ),
            )
        reopened = Store(self.store.path)
        self.assertEqual(reopened.record(1)["flag"], "")
        self.assertEqual(reopened.record(2)["flag"], "bad")
        undone = reopened.unverify(1, checked["revision"])
        self.assertEqual(undone["flag"], "review")

    def test_undo_edit_restores_verification_and_can_then_cancel_it(self):
        checked = self.verify(self.mark())
        changed = dispatch(
            self.store,
            "save",
            {"id": 1, "revision": checked["revision"], "text": "Изменено"},
        )
        restored = self.store.undo(1, changed["revision"])
        self.assertEqual(
            (restored["status"], restored["flag"], restored["text"]),
            ("verified", "", checked["text"]),
        )
        unchecked = self.store.unverify(1, restored["revision"])
        self.assertEqual(unchecked["flag"], "review")
        self.assertEqual(unchecked["text"], checked["text"])
