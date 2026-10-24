import copy
import gzip
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import Store
from tests.test_updates import make_bundle
from tools.package_release import package_files
from update_controller import UpdateController
from update_feed import latest
from update_files import file_hash, stage_files, validate_index
from update_installer import replace_application
from update_package import validate_bundle
from version import REPOSITORY


class FileUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = make_bundle(self.root / "new")
        self.library = "_internal/lingua/lingua.cp312-win_amd64.pyd"
        path = self.bundle / self.library
        path.parent.mkdir(parents=True)
        path.write_bytes(b"language detector unchanged" * 500)
        manifest_path = self.bundle / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][self.library] = file_hash(path)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assets = self.root / "assets"
        self.index, self.parts = package_files(self.bundle, self.assets, "0.2.0")
        self.raw = json.loads(self.index.read_text())
        self.home = self.root / "installed"
        shutil.copytree(self.bundle, self.home)
        (self.home / "LocalizationStudio.exe").write_bytes(b"old version")
        self.sums = self.assets / "SHA256SUMS.txt"
        self.sums.write_text(f"{file_hash(self.index)}  update-files.json\n")
        self.release_raw = {
            "tag_name": "v0.2.0",
            "assets": [
                self.asset(path.name, path.stat().st_size)
                for path in [self.index, self.sums, *self.parts]
            ]
            + [self.asset("LocalizationStudio-v0.2.0-windows-x64.zip", 100000)],
        }
        self.calls = []
        self.release = latest(
            "0.1.0", lambda _: io.BytesIO(json.dumps(self.release_raw).encode())
        )

    def asset(self, name, size):
        return {
            "name": name,
            "size": size,
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v0.2.0/{name}",
        }

    def opener(self, url):
        name = url.rsplit("/", 1)[-1]
        self.calls.append(name)
        return io.BytesIO((self.assets / name).read_bytes())

    def stage(self, progress=lambda **kw: None):
        return stage_files(
            self.release, self.home, self.root / "stage", progress, self.opener
        )

    def publish_index(self, index):
        self.index.write_text(json.dumps(index))
        self.sums.write_text(f"{file_hash(self.index)}  update-files.json\n")
        self.release["incremental"]["manifest"]["size"] = self.index.stat().st_size

    def test_only_changed_file_downloaded_and_old_install_not_modified(self):
        progress = []
        staged = self.stage(lambda **kw: progress.append(kw))
        validate_bundle(staged, "0.2.0")
        needed = self.raw["files"]["LocalizationStudio.exe"]
        self.assertEqual(
            self.calls, ["SHA256SUMS.txt", "update-files.json", needed["asset"]]
        )
        self.assertEqual(
            (self.home / "LocalizationStudio.exe").read_bytes(), b"old version"
        )
        self.assertEqual(
            (staged / self.library).read_bytes(),
            (self.home / self.library).read_bytes(),
        )
        self.assertEqual(sum(e.get("changed_files", 0) for e in progress), 1)
        self.assertEqual(progress[-1]["downloaded"], needed["download_size"])
        self.assertTrue(
            any(
                e.get("reused_bytes", 0) >= (self.home / self.library).stat().st_size
                for e in progress
            )
        )

    def test_no_file_download_when_everything_matches(self):
        shutil.copyfile(
            self.bundle / "LocalizationStudio.exe", self.home / "LocalizationStudio.exe"
        )
        self.stage()
        self.assertEqual(self.calls, ["SHA256SUMS.txt", "update-files.json"])

    def test_missing_and_corrupt_library_downloaded(self):
        for mode in ["missing", "corrupt"]:
            with self.subTest(mode=mode):
                library = self.home / self.library
                if mode == "missing":
                    library.unlink()
                else:
                    library.write_bytes(b"x" * self.raw["files"][self.library]["size"])
                self.stage()
                self.assertIn(self.raw["files"][self.library]["asset"], self.calls)
                self.assertEqual(
                    file_hash(self.root / "stage" / self.library),
                    self.raw["files"][self.library]["sha256"],
                )
                shutil.rmtree(self.root / "stage")
                self.calls.clear()

    def test_local_copy_changed_during_read_downloads_verified_replacement(self):
        original = shutil.copyfile

        def changing(source, target):
            result = original(source, target)
            if Path(source).as_posix().endswith(self.library):
                Path(target).write_bytes(b"changed during copy")
            return result

        with patch("update_files.shutil.copyfile", side_effect=changing):
            self.stage()
        self.assertIn(self.raw["files"][self.library]["asset"], self.calls)

    def test_tampered_index_rejected_before_file_download(self):
        self.index.write_bytes(self.index.read_bytes().replace(b"0.2.0", b"0.9.0"))
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(self.calls, ["SHA256SUMS.txt", "update-files.json"])

    def test_corrupt_compressed_file_and_truncation_rejected(self):
        entry = self.raw["files"]["LocalizationStudio.exe"]
        path = self.assets / entry["asset"]
        original = path.read_bytes()
        for body in [b"x" * len(original), original[:-1], original + b"extra"]:
            path.write_bytes(body)
            with self.assertRaises(ValueError):
                self.stage()
            self.assertFalse((self.root / "file.partial").exists())
            self.assertEqual(
                (self.home / "LocalizationStudio.exe").read_bytes(), b"old version"
            )
            shutil.rmtree(self.root / "stage")

    def test_unpacked_hash_and_expansion_limit_checked(self):
        for content in [b"wrong", b"x" * 1000000]:
            index = copy.deepcopy(self.raw)
            entry = index["files"]["LocalizationStudio.exe"]
            path = self.assets / entry["asset"]
            path.write_bytes(gzip.compress(content, mtime=0))
            entry["download_size"] = path.stat().st_size
            entry["download_sha256"] = file_hash(path)
            for asset in self.release["incremental"]["release"]["assets"]:
                if asset["name"] == path.name:
                    asset["size"] = path.stat().st_size
            self.publish_index(index)
            with self.assertRaises(ValueError):
                self.stage()
            shutil.rmtree(self.root / "stage")

    def test_invalid_index_paths_versions_hashes_and_sizes(self):
        changes = [
            lambda i: i.update(schema=9),
            lambda i: i.update(version="0.3.0"),
            lambda i: i.update(repository="evil/repo"),
            lambda i: i["files"].pop("LocalizationWorker.exe"),
        ]
        for name in [
            "data/studio.sqlite3",
            "../outside",
            "_internal/../x",
            "_internal/CON.txt",
            "_internal/a:b",
            "_internal/../data/x",
            "_internal/x ",
            "_internal/A?",
            "_internal",
        ]:
            changes.append(
                lambda i, name=name: i["files"].update(
                    {name: self.raw["files"]["LocalizationStudio.exe"]}
                )
            )
        for field, value in [
            ("size", -1),
            ("size", True),
            ("size", 2**31),
            ("download_size", 0),
            ("download_sha256", "bad"),
            ("sha256", "0" * 63),
            ("asset", "https://evil/a.gz"),
        ]:
            changes.append(
                lambda i, field=field, value=value: i["files"][
                    "LocalizationStudio.exe"
                ].update({field: value})
            )
        changes.append(
            lambda i: i["files"].update(
                {
                    "_internal/UI/INDEX.HTML": self.raw["files"][
                        "_internal/ui/index.html"
                    ]
                }
            )
        )
        for change in changes:
            index = copy.deepcopy(self.raw)
            change(index)
            with self.assertRaises(ValueError):
                validate_index(index, "0.2.0")

    def test_foreign_blob_or_missing_asset_rejected_without_full_download(self):
        asset_name = self.raw["files"]["LocalizationStudio.exe"]["asset"]
        assets = self.release["incremental"]["release"]["assets"]
        asset = next(a for a in assets if a["name"] == asset_name)
        asset["browser_download_url"] = "https://evil/file.gz"
        with self.assertRaises(ValueError):
            self.stage()
        self.assertEqual(self.calls, ["SHA256SUMS.txt", "update-files.json"])

    def test_staged_bundle_installs_and_failed_install_restores_old_library(self):
        staged = self.stage()
        data = self.home / "data"
        data.mkdir()
        (data / "studio.sqlite3").write_bytes(b"user data")
        plan = {
            "home": str(self.home),
            "bundle": str(staged),
            "backup": str(data / "backups/failed"),
            "version": "0.2.0",
        }
        with patch(
            "update_installer.shutil.copytree", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                replace_application(plan)
        self.assertEqual(
            file_hash(self.home / self.library),
            self.raw["files"][self.library]["sha256"],
        )
        self.assertEqual(
            (self.home / "LocalizationStudio.exe").read_bytes(), b"old version"
        )
        plan["backup"] = str(data / "backups/success")
        replace_application(plan)
        for name, entry in self.raw["files"].items():
            self.assertEqual(file_hash(self.home / name), entry["sha256"])
        self.assertEqual(
            file_hash(self.home / "LocalizationStudio.exe"),
            self.raw["files"]["LocalizationStudio.exe"]["sha256"],
        )
        self.assertEqual((data / "studio.sqlite3").read_bytes(), b"user data")

    def test_controller_uses_incremental_and_cleans_failed_stage(self):
        store = Store(self.home / "data/studio.sqlite3")
        controller = UpdateController(store, SimpleNamespace(), self.home, True)
        controller.release = self.release
        with (
            patch.object(
                controller, "_launch", side_effect=lambda phase, action: action()
            ),
            patch("update_controller.download") as full,
            patch(
                "update_controller.stage_files",
                side_effect=lambda release, home, destination, progress: stage_files(
                    release, home, destination, progress, self.opener
                ),
            ),
        ):
            controller.fetch()
            self.assertEqual(controller.state["phase"], "ready")
            self.assertEqual(controller.state["changed_files"], 1)
            full.assert_not_called()
        self.index.write_bytes(b"tampered")
        with (
            patch.object(
                controller, "_launch", side_effect=lambda phase, action: action()
            ),
            patch(
                "update_controller.stage_files",
                side_effect=lambda release, home, destination, progress: stage_files(
                    release, home, destination, progress, self.opener
                ),
            ),
        ):
            with self.assertRaises(ValueError):
                controller.fetch()
            self.assertIsNone(controller.session)
        self.assertEqual(len(list((self.home / "data/updates").iterdir())), 1)
