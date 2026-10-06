import hashlib
import io
import json
import os
import threading
import time
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import Store
from update_controller import UpdateController, PREFERENCES
from update_feed import latest, download, version_tuple
from update_installer import read_plan, replace_application
from update_package import REQUIRED, validate_bundle, extract_bundle
from version import REPOSITORY


def make_bundle(path):
    path.mkdir(parents=True)
    files = {}
    for name in REQUIRED:
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(("new " + name).encode())
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (path / "release-manifest.json").write_text(
        json.dumps({"repository": REPOSITORY, "version": "0.2.0", "files": files}),
        encoding="utf-8",
    )
    return path


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def release(self, tag="v0.2.0"):
        names = [f"LocalizationStudio-{tag}-windows-x64.zip", "SHA256SUMS.txt"]
        return {
            "tag_name": tag,
            "assets": [
                {
                    "name": name,
                    "size": 3,
                    "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}",
                }
                for name in names
            ],
        }

    def test_numeric_version_order(self):
        self.assertGreater(version_tuple("v0.10.0"), version_tuple("0.2.0"))
        for value in ["latest", "0.2", "1.0.0-beta", "v../../1.0.0"]:
            with self.assertRaises(ValueError):
                version_tuple(value)

    def test_latest_verified_assets(self):
        result = latest(
            "0.1.0", lambda url: io.BytesIO(json.dumps(self.release()).encode())
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "0.2.0")

    def test_same_or_older_no_download(self):
        for current in ["0.2.0", "0.10.0"]:
            result = latest(
                current, lambda url: io.BytesIO(json.dumps(self.release()).encode())
            )
            self.assertFalse(result["available"])
            self.assertNotIn("asset", result)

    def test_draft_and_prerelease_not_available(self):
        for flag in ["draft", "prerelease"]:
            release = self.release()
            release[flag] = True
            self.assertFalse(
                latest("0.1.0", lambda url: io.BytesIO(json.dumps(release).encode()))[
                    "available"
                ]
            )

    def test_foreign_download_rejected(self):
        release = self.release()
        release["assets"][0]["browser_download_url"] = "https://example.com/setup.exe"
        with self.assertRaises(ValueError):
            latest("0.1.0", lambda url: io.BytesIO(json.dumps(release).encode()))

    def test_missing_checksum_rejected(self):
        release = self.release()
        release["assets"].pop()
        with self.assertRaises(ValueError):
            latest("0.1.0", lambda url: io.BytesIO(json.dumps(release).encode()))

    def download_fixture(self, body=b"zip", checksum=None, size=3):
        release = self.release()
        release["assets"][0]["size"] = size
        result = latest("0.1.0", lambda url: io.BytesIO(json.dumps(release).encode()))
        sums = (
            (checksum or hashlib.sha256(body).hexdigest())
            + "  "
            + result["asset"]["name"]
        ).encode()
        return result, lambda url: io.BytesIO(sums if url.endswith(".txt") else body)

    def test_download_hash_and_progress(self):
        result, opener = self.download_fixture()
        progress = []
        path = download(
            result,
            self.home / "release.zip",
            lambda done, total: progress.append((done, total)),
            opener,
        )
        self.assertEqual(path.read_bytes(), b"zip")
        self.assertEqual(progress, [(3, 3)])

    def test_corrupt_download_does_not_replace_existing(self):
        destination = self.home / "release.zip"
        destination.write_bytes(b"old")
        for checksum, size in [("0" * 64, 3), (None, 2), (None, 4)]:
            result, opener = self.download_fixture(checksum=checksum, size=size)
            with self.assertRaises(ValueError):
                download(result, destination, opener=opener)
            self.assertEqual(destination.read_bytes(), b"old")
            self.assertFalse(destination.with_suffix(".partial").exists())

    def test_complete_bundle_and_checksum(self):
        bundle = make_bundle(self.home / "bundle")
        validate_bundle(bundle, "0.2.0")
        (bundle / "LocalizationStudio.exe").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            validate_bundle(bundle, "0.2.0")

    def test_extra_unchecked_file_rejected(self):
        bundle = make_bundle(self.home / "bundle")
        (bundle / "_internal/extra.dll").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            validate_bundle(bundle, "0.2.0")

    def test_wrong_version_rejected(self):
        bundle = make_bundle(self.home / "bundle")
        with self.assertRaises(ValueError):
            validate_bundle(bundle, "0.3.0")

    def test_archive_traversal_and_userdata_rejected(self):
        for index, name in enumerate(
            [
                "../outside",
                "LocalizationStudio/data/studio.sqlite3",
                "_internal/../data/x",
                "_internal/a:stream",
                "_internal/a./x",
            ]
        ):
            archive = self.home / f"{index}.zip"
            with zipfile.ZipFile(archive, "w") as file:
                file.writestr(name, "bad")
            with self.assertRaises(ValueError):
                extract_bundle(archive, self.home / f"dest{index}", "0.2.0")
        self.assertFalse((self.home / "outside").exists())

    def test_archive_case_collision_rejected(self):
        archive = self.home / "duplicate.zip"
        with zipfile.ZipFile(archive, "w") as file:
            file.writestr("LocalizationStudio.exe", "a")
            file.writestr("localizationstudio.exe", "b")
        with self.assertRaises(ValueError):
            extract_bundle(archive, self.home / "out", "0.2.0")

    def plan(self):
        session = self.home / "data/updates/test"
        make_bundle(session / "bundle")
        backup = self.home / "data/backups/test"
        backup.mkdir(parents=True)
        plan = {
            "home": str(self.home),
            "database": str(self.home / "data/studio.sqlite3"),
            "bundle": str(session / "bundle"),
            "backup": str(backup),
            "version": "0.2.0",
            "gui_pid": os.getpid(),
            "resume_jobs": [],
        }
        path = session / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return read_plan(path)

    def old_application(self):
        (self.home / "LocalizationStudio.exe").write_bytes(b"old")
        (self.home / "_internal").mkdir()
        (self.home / "_internal/old").write_bytes(b"old runtime")
        (self.home / "data/studio.sqlite3").write_bytes(b"project data")
        (self.home / "data/update_preferences.json").write_bytes(b"preferences")

    def test_install_preserves_data_and_backs_up_runtime(self):
        plan = self.plan()
        self.old_application()
        replace_application(plan)
        self.assertEqual(
            (self.home / "data/studio.sqlite3").read_bytes(), b"project data"
        )
        self.assertEqual(
            (self.home / "data/update_preferences.json").read_bytes(), b"preferences"
        )
        self.assertEqual(
            (Path(plan["backup"]) / "application/LocalizationStudio.exe").read_bytes(),
            b"old",
        )
        self.assertTrue((self.home / "LocalizationUpdater.exe").exists())
        self.assertEqual(
            (self.home / "worker_version.txt").read_text(), "LocalizationWorker.exe"
        )

    def test_partial_copy_rolls_back(self):
        plan = self.plan()
        self.old_application()

        def failure(source, target):
            Path(target).mkdir()
            (Path(target) / "partial").write_bytes(b"partial")
            raise OSError("disk full")

        with patch("update_installer.shutil.copytree", side_effect=failure):
            with self.assertRaises(OSError):
                replace_application(plan)
        self.assertEqual((self.home / "LocalizationStudio.exe").read_bytes(), b"old")
        self.assertTrue((self.home / "_internal/old").exists())
        self.assertFalse((self.home / "_internal/partial").exists())
        self.assertEqual(
            (self.home / "data/studio.sqlite3").read_bytes(), b"project data"
        )

    def test_plan_outside_application_rejected(self):
        plan = self.plan()
        path = Path(plan["_path"])
        raw = json.loads(path.read_text())
        raw["bundle"] = str(self.home / "data")
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            read_plan(path)

    def test_offline_defaults_and_persisted_preferences(self):
        store = Store(self.home / "data/studio.sqlite3")
        closer = SimpleNamespace(
            window=SimpleNamespace(evaluate_js=lambda script: False)
        )
        with patch("update_controller.latest") as network:
            controller = UpdateController(store, closer, home=self.home, frozen=True)
            controller.initialize()
            controller.dispose()
            network.assert_not_called()
        self.assertEqual(controller.snapshot()["preferences"], PREFERENCES)
        values = {**PREFERENCES, "auto_check": True}
        controller.configure(values)
        self.assertEqual(
            UpdateController(store, closer, home=self.home, frozen=True).snapshot()[
                "preferences"
            ],
            values,
        )
        with self.assertRaises(ValueError):
            controller.configure({"auto_check": "yes"})

    def test_unsaved_edit_blocks_install(self):
        store = Store(self.home / "data/studio.sqlite3")
        closer = SimpleNamespace(
            window=SimpleNamespace(evaluate_js=lambda script: True)
        )
        controller = UpdateController(store, closer, home=self.home, frozen=True)
        controller.state["phase"] = "ready"
        controller.session = self.home
        controller.install()
        for _ in range(100):
            if controller.snapshot()["phase"] == "error":
                break
            time.sleep(0.01)
        self.assertEqual(controller.snapshot()["phase"], "error")
        self.assertIn("правки", controller.snapshot()["message"])

    def test_install_saves_queues_and_launches_helper(self):
        store = Store(self.home / "data/studio.sqlite3")
        store.launch_lock = threading.RLock()
        store.closing = False
        project = self.home / "game"
        project.mkdir()
        pid = store.project(str(project))["id"]
        corpus = project / "text.json"
        corpus.write_text(json.dumps([{"source": "Hello."}]))
        store.import_files(pid, [corpus])
        jobs = [store.create_job(pid, "translate", "local", {})]
        with store.db() as db:
            for state in ["waiting", "held"]:
                cursor = db.execute(
                    "INSERT INTO jobs(project,stage,provider,settings,state) VALUES (?,'translate','local','{}',?)",
                    (pid, state),
                )
                jobs.append(cursor.lastrowid)
            for jid, state in zip(jobs, ["queued", "waiting", "held"]):
                db.execute("UPDATE jobs SET state=? WHERE id=?", (state, jid))
        destroyed = threading.Event()
        window = SimpleNamespace(
            evaluate_js=lambda script: False, destroy=destroyed.set
        )
        closer = SimpleNamespace(window=window, pending=False, allow_exit=False)
        controller = UpdateController(store, closer, home=self.home, frozen=True)
        session = self.home / "data/updates/test"
        make_bundle(session / "bundle")
        controller.session = session
        controller.release = {"latest": "0.2.0"}
        controller.state["phase"] = "ready"
        with patch("update_controller.subprocess.Popen") as launch:
            controller.install()
            self.assertTrue(destroyed.wait(5), controller.snapshot())
            launch.assert_called_once()
        plan = json.loads((session / "plan.json").read_text())
        self.assertEqual(plan["resume_jobs"], [jobs[0]])
        self.assertEqual(plan["waiting_jobs"], [jobs[1]])
        with store.db() as db:
            states = [r[0] for r in db.execute("SELECT state FROM jobs ORDER BY id")]
        self.assertEqual(states, ["paused", "held", "held"])
        self.assertTrue((Path(plan["backup"]) / "studio.sqlite3").exists())
