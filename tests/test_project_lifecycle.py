import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from core import Store
from app import state
from project_lifecycle import delete_project, matches_worker, stop_worker


class LifecycleTests(unittest.TestCase):
    def test_exit_race_is_ignored_but_real_access_denial_is_reported(self):
        import sys
        from core import ROOT
        from types import SimpleNamespace

        command = f'"{sys.executable}" "{ROOT / "app.py"}" --worker 3 --db "{self.store.path}"'
        info = {"ExecutablePath": sys.executable, "CommandLine": command}
        for exited in (True, False):
            with self.subTest(exited=exited):
                kernel = MagicMock()
                kernel.OpenProcess.return_value = 123
                kernel.TerminateProcess.return_value = 0
                kernel.WaitForSingleObject.side_effect = [258, 0 if exited else 258]
                result = SimpleNamespace(returncode=0, stdout=json.dumps(info))
                with (
                    patch("project_lifecycle.ctypes.WinDLL", return_value=kernel),
                    patch("project_lifecycle.ctypes.get_last_error", return_value=5),
                    patch("project_lifecycle.subprocess.run", return_value=result),
                ):
                    if exited:
                        self.assertFalse(stop_worker(self.store, {"id": 3, "pid": 123}))
                    else:
                        with self.assertRaises(OSError) as error:
                            stop_worker(self.store, {"id": 3, "pid": 123})
                        self.assertEqual(error.exception.winerror, 5)
                kernel.CloseHandle.assert_called_once_with(123)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "test.db")
        self.pid, self.rid = self.fixture("first")
        self.other, self.other_rid = self.fixture("second")

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, name):
        folder = self.root / name
        folder.mkdir()
        p = self.store.project(str(folder))["id"]
        path = folder / "lines.json"
        path.write_text('[{"source":"Hello"}]')
        self.store.import_files(p, [path])
        with self.store.db() as db:
            r = db.execute(
                "SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",
                (p,),
            ).fetchone()[0]
        return p, r

    def finish(self, p, r):
        j = self.store.create_job(p, "translate", "local", {})
        self.store.update(r, 0, "Привет", "translated", "fixture")
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (j,))
        return j

    def test_auto_complete_and_explicit_restore(self):
        self.finish(self.pid, self.rid)
        self.store.archive_completed()
        self.assertEqual([p["id"] for p in self.store.completed_projects()], [self.pid])
        self.assertEqual(state(self.store, self.pid)["project"], self.other)
        self.store.hide_project(self.pid, False)
        self.store.archive_completed()
        self.assertFalse(self.store.completed_projects())
        self.assertEqual(self.store.record(self.rid)["text"], "Привет")

    def test_not_complete_with_pending_text_or_running_review(self):
        with self.assertRaises(ValueError):
            self.store.complete_project(self.pid)
        self.finish(self.pid, self.rid)
        self.store.create_job(self.pid, "review", "local", {})
        self.store.archive_completed()
        self.assertFalse(self.store.completed_projects())

    def test_auto_complete_waits_for_unsaved_editor(self):
        self.finish(self.pid, self.rid)
        self.assertEqual(state(self.store, self.pid, editing=True)["project"], self.pid)
        self.assertEqual(state(self.store, self.pid)["project"], self.other)

    def test_removed_ids_are_never_reused(self):
        jid = self.store.create_job(self.other, "translate", "local", {})
        with self.store.db() as db:
            oldfile = db.execute(
                "SELECT file FROM records WHERE id=?", (self.other_rid,)
            ).fetchone()[0]
        with patch("project_lifecycle.remove_secret"):
            delete_project(self.store, self.other, lambda *_: False)
        pid = self.store.project(str(self.root / "second"))["id"]
        self.store.import_files(pid, [self.root / "second" / "lines.json"])
        newjid = self.store.create_job(pid, "translate", "local", {})
        with self.store.db() as db:
            row = db.execute(
                "SELECT r.id,r.file FROM records r JOIN files f ON r.file=f.id WHERE f.project=?",
                (pid,),
            ).fetchone()
        self.assertGreater(pid, self.other)
        self.assertGreater(row["id"], self.other_rid)
        self.assertGreater(row["file"], oldfile)
        self.assertGreater(newjid, jid)

    def test_manual_complete_preserves_marks_history(self):
        self.store.update(self.rid, 0, "Привет", "translated", "human", manual=True)
        self.store.mark(self.rid, 1, "bad")
        before = self.store.record(self.rid)
        self.store.complete_project(self.pid)
        self.assertEqual(self.store.record(self.rid), before)
        self.assertFalse(self.store.hidden_projects())

    def test_deletion_cancels_before_stopping_and_is_scoped(self):
        jid = self.store.create_job(self.pid, "translate", "local", {})
        otherjid = self.store.create_job(self.other, "translate", "local", {})
        self.store.update(self.rid, 0, "Привет", "translated", "human", manual=True)
        self.store.mark(self.rid, 1, "bad")
        self.store.error(jid, self.rid, "fixture error")
        with self.store.db() as db:
            db.execute("INSERT INTO cache_owners VALUES (?, 'unique')", (self.pid,))
            db.execute("INSERT INTO cache_owners VALUES (?, 'shared')", (self.pid,))
            db.execute("INSERT INTO cache_owners VALUES (?, 'shared')", (self.other,))
            db.executemany(
                "INSERT INTO fragment_cache VALUES (?,?)",
                [("unique", "one"), ("shared", "two")],
            )
        seen = []

        def stopper(store, job):
            with store.db() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT state FROM jobs WHERE id=?", (job["id"],)
                    ).fetchone()[0],
                    "cancelled",
                )
            seen.append(job["id"])
            return True

        with patch("project_lifecycle.remove_secret") as secret:
            result = delete_project(self.store, self.pid, stopper)
        self.assertEqual(seen, [jid])
        self.assertEqual(result["stopped_workers"], 1)
        secret.assert_called_once_with(self.pid)
        with self.store.db() as db:
            for table in ["history", "revision_snapshots", "record_marks", "errors"]:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0
                )
            self.assertEqual(
                db.execute("SELECT count(*) FROM records").fetchone()[0], 1
            )
            self.assertEqual(db.execute("SELECT id FROM jobs").fetchone()[0], otherjid)
            self.assertEqual(
                [r[0] for r in db.execute("SELECT key FROM fragment_cache")], ["shared"]
            )
        self.assertTrue((self.root / "first" / "lines.json").exists())
        self.assertEqual(self.store.record(self.other_rid)["status"], "empty")
        new = self.store.project(str(self.root / "first"))["id"]
        self.assertGreater(new, self.other)

    def test_failed_stop_retains_retry_and_prevents_resume(self):
        self.store.create_job(self.pid, "translate", "local", {})
        with self.assertRaises(ValueError), patch("project_lifecycle.remove_secret"):
            delete_project(
                self.store,
                self.pid,
                lambda *_: (_ for _ in ()).throw(ValueError("access denied")),
            )
        self.assertTrue(self.store.hidden_projects()[0]["deleting"])
        with self.assertRaises(ValueError):
            self.store.project(str(self.root / "first"))
        with self.assertRaises(ValueError):
            self.store.create_job(self.pid, "translate", "local", {})
        with patch("project_lifecycle.remove_secret"):
            delete_project(self.store, self.pid, lambda *_: False)

    def test_worker_identity_requires_exe_job_and_database(self):
        import sys
        from core import ROOT

        info = {
            "ExecutablePath": sys.executable,
            "CommandLine": f'"{sys.executable}" "{ROOT / "app.py"}" --worker 3 --db "{self.store.path}"',
        }
        self.assertTrue(matches_worker(info, self.store, 3))
        self.assertFalse(matches_worker(info, self.store, 30))
        self.assertFalse(
            matches_worker(
                {
                    **info,
                    "CommandLine": info["CommandLine"].replace(
                        str(self.store.path), "different.db"
                    ),
                },
                self.store,
                3,
            )
        )
        self.assertFalse(
            matches_worker(
                {**info, "ExecutablePath": str(self.root / "unrelated.exe")},
                self.store,
                3,
            )
        )


if __name__ == "__main__":
    unittest.main()
