import hashlib
import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from extraction import extract
from plugin_manager import PluginManager
from project_workflow import overview
from game_detection import inspect_project
from plugins.renpy.controller import RenPyPlugin
from plugins.renpy.filesystem import copy_game, output_path, owned
from plugins.renpy.build import apply_plan, restore, digest
from plugins.renpy.runtime import discover, command, environment
from plugins.renpy.verification import expectation


def fake_font():
    # A format-12 cmap with all basic Cyrillic letters and nonzero glyph IDs.
    points = [0x401, *range(0x410, 0x450), 0x451]
    table = struct.pack(">HHIII", 12, 0, 16 + len(points) * 12, 0, len(points))
    table += b"".join(
        struct.pack(">III", point, point, i + 1) for i, point in enumerate(points)
    )
    cmap = struct.pack(">HHHHI", 0, 1, 3, 10, 12) + table
    return (
        struct.pack(">IHHHH", 0x10000, 1, 0, 0, 0)
        + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap))
        + cmap
    )


def fake_game(root):
    for folder in ["game", "renpy/common", "lib/py3-windows-x86_64"]:
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "renpy/__init__.py").write_text(
        "version_tuple = (8, 5, 3, 0)\n", encoding="utf-8"
    )
    (root / "renpy.py").write_text("import renpy.bootstrap\n", encoding="utf-8")
    (root / "lib/py3-windows-x86_64/python.exe").write_bytes(b"neutral fixture")
    (root / "renpy/common/DejaVuSans.ttf").write_bytes(fake_font())
    (root / "game/script.rpy").write_text(
        'label start:\n    "A neutral sentence."\n    return\n', encoding="utf-8"
    )
    (root / "game/screens.rpy").write_text(
        'screen preferences():\n    tag menu\n    text "Settings"\n', encoding="utf-8"
    )


def templates(root):
    folder = root / "game/tl/studio_russian"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "script.rpy").write_text(
        """# game/script.rpy:2
translate studio_russian start_ab12:

    # "A neutral sentence."
    ""

translate studio_russian strings:
    old "Settings"
    new ""
""",
        encoding="utf-8",
    )


class RenPyPluginTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        fake_game(self.source)
        self.store = Store(self.root / "data/test.sqlite3")
        self.store.launch_lock = threading.RLock()
        self.project = self.store.project(str(self.source))["id"]
        self.plugin = RenPyPlugin(self.store)
        self.addCleanup(self.plugin.dispose)
        self.calls = []

        def engine(root, home, task, *args):
            self.calls.append(task)
            if task == "translate":
                templates(Path(root))
            elif task == "studio_verify":
                Path(args[-1]).write_text('{"checked":2,"font":true}', encoding="utf-8")

        self.plugin.run_engine = engine

    def wait(self):
        self.plugin.thread.join(10)
        self.assertFalse(self.plugin.busy, self.plugin.status)
        return self.plugin.read(self.project)

    def prepare(self):
        self.plugin.prepare(self.project, str(self.root / "copy"))
        value = self.wait()
        self.assertTrue(value["prepared"], value)
        return value

    def records(self, project):
        with self.store.db() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE f.project=? ORDER BY r.id",
                    (project,),
                )
            ]

    def fill(self, project, status="edited"):
        for row in self.records(project):
            self.store.update(
                row["id"],
                row["revision"],
                "Нейтральная фраза." if row["speaker"] != "interface" else "Настройки",
                status,
                "local:fixture",
            )

    def test_discovery_uses_bundled_runtime_and_dynamic_version_literal(self):
        self.assertEqual(discover(self.source)["version"], "8.5.3")
        (self.source / "renpy/__init__.py").write_text(
            "from renpy.vc_version import version\n", encoding="utf-8"
        )
        (self.source / "renpy/vc_version.py").write_text(
            "version = '8.5.3.1234'\n", encoding="utf-8"
        )
        argv = command(self.source, "compile", self.root / "saves")
        self.assertEqual(argv[1:3], ["-X", "utf8"])
        env = environment(self.root, root=self.source, language="original")
        self.assertEqual(env["PYTHONHOME"], str(self.source))
        self.assertEqual(env["STUDIO_RENPY_LANGUAGE"], "original")
        self.assertNotIn("RENPY_LANGUAGE", env)

    def test_constructor_never_recovers_another_process_or_creates_jobs(self):
        self.assertFalse(self.plugin.root.exists())
        state = self.prepare()
        self.plugin.save({**state, "active": True})
        other = RenPyPlugin(self.store)
        self.assertTrue(other.read(self.project)["active"])
        other.recover_startup()
        self.assertFalse(other.read(self.project)["active"])
        self.assertEqual(other.read(self.project)["phase"], "paused")
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_overlap_existing_destination_and_junction_are_blocked(self):
        for target in [
            self.source,
            self.source / "copy",
            self.root,
            self.root / "data",
        ]:
            with self.assertRaises(ValueError):
                output_path(self.source, target)
        link = self.root / "link"
        with patch.object(Path, "is_junction", lambda path: path == link):
            with self.assertRaises(ValueError):
                output_path(self.source, link)

    def test_copy_ownership_cancellation_and_no_source_writes(self):
        original = {
            p.relative_to(self.source): digest(p)
            for p in self.source.rglob("*")
            if p.is_file()
        }
        state = self.prepare()
        self.assertEqual(
            original,
            {
                p.relative_to(self.source): digest(p)
                for p in self.source.rglob("*")
                if p.is_file()
            },
        )
        self.assertEqual(owned(state["copy"], state["owner"]), self.root / "copy")
        with self.assertRaises(ValueError):
            owned(state["copy"], "wrong-owner")
        event = threading.Event()
        event.set()
        with self.assertRaises(ValueError):
            copy_game(
                self.source, self.root / "cancelled", "owner", lambda *args: None, event
            )

    def test_preparation_imports_only_native_templates_into_separate_project(self):
        self.store.import_files(self.project, [self.source / "game/script.rpy"])
        old = self.records(self.project)[0]
        self.store.update(
            old["id"],
            old["revision"],
            "Нейтральная фраза.",
            "verified",
            "human",
            manual=True,
        )
        state = self.prepare()
        child = state["workspace_project"]
        self.assertNotEqual(child, self.project)
        rows = self.records(child)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["text"], "Нейтральная фраза.")
        self.assertEqual(state["transfer"]["transferred"], 1)
        with self.store.db() as db:
            files = db.execute(
                "SELECT path FROM files WHERE project=?", (child,)
            ).fetchall()
        self.assertTrue(
            all("/tl/studio_russian/" in Path(row[0]).as_posix() for row in files)
        )

    def test_ambiguous_existing_translation_requires_human_review(self):
        self.store.import_files(self.project, [self.source / "game/script.rpy"])
        duplicate = self.source / "game/duplicate.rpy"
        duplicate.write_bytes((self.source / "game/script.rpy").read_bytes())
        self.store.import_files(self.project, [duplicate])
        for i, row in enumerate(self.records(self.project)):
            self.store.update(
                row["id"],
                row["revision"],
                "Вариант " + str(i),
                "edited",
                "local:fixture",
            )
        state = self.prepare()
        self.assertEqual(state["transfer"]["ambiguous"], 1)
        row = self.store.record(self.records(state["workspace_project"])[0]["id"])
        self.assertEqual(row["flag"], "review")
        self.assertEqual(row["status"], "empty")

    @patch("plugins.renpy.runtime.require_not_running")
    def test_build_requires_complete_reviewed_clean_records(self, running):
        state = self.prepare()
        child = state["workspace_project"]
        self.plugin.build(child)
        self.assertIn("завершите перевод", self.wait()["message"])
        self.fill(child, "translated")
        self.plugin.build(child)
        self.assertIn("редактуру", self.wait()["message"])
        self.fill(child)
        row = self.records(child)[0]
        self.store.mark(row["id"], row["revision"], "review")
        self.plugin.build(child)
        self.assertIn("ручную проверку", self.wait()["message"])

    @patch("plugins.renpy.runtime.require_not_running")
    def test_menu_launcher_switch_and_rollback_preserve_original(self, running):
        state = self.prepare()
        child = state["workspace_project"]
        self.fill(child)
        original = (self.source / "game/screens.rpy").read_bytes()
        self.plugin.build(child, "menu")
        state = self.wait()
        self.assertTrue(state["built"], state)
        prefs = self.root / "copy/game/screens.rpy"
        self.assertIn(
            "use _studio_language_selector", prefs.read_text(encoding="utf-8")
        )
        helper = self.root / "copy/game/zz_studio_localization.rpy"
        self.assertNotIn(
            "config.always_shown_screens.append", helper.read_text(encoding="utf-8")
        )
        self.assertEqual((self.source / "game/screens.rpy").read_bytes(), original)
        expected = json.loads(
            expectation(self.store, child, "menu", self.plugin.root).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [r["type"] for r in expected["records"]], ["dialogue", "string"]
        )
        self.plugin.build(child, "launcher")
        state = self.wait()
        self.assertTrue(state["built"], state)
        self.assertNotIn(
            "use _studio_language_selector", prefs.read_text(encoding="utf-8")
        )
        self.plugin.rollback(child)
        self.assertIn(
            "use _studio_language_selector", prefs.read_text(encoding="utf-8")
        )
        self.plugin.build(child, "menu")
        self.assertTrue(self.wait()["built"])
        self.assertEqual(self.calls[-3:], ["compile", "lint", "studio_verify"])

    @patch("plugins.renpy.runtime.require_not_running")
    def test_first_build_rollback_allows_rebuild_and_keeps_source_templates(
        self, running
    ):
        state = self.prepare()
        child = state["workspace_project"]
        self.fill(child)
        self.plugin.build(child)
        self.assertTrue(self.wait()["built"])
        self.plugin.rollback(child)
        self.assertFalse(self.plugin.read(child)["built"])
        self.plugin.build(child)
        self.assertTrue(self.wait()["built"])

    @patch("plugins.renpy.runtime.require_not_running")
    def test_engine_failure_restores_files_and_keeps_translations(self, running):
        state = self.prepare()
        child = state["workspace_project"]
        self.fill(child)
        before = (self.root / "copy/game/tl/studio_russian/script.rpy").read_bytes()
        self.plugin.run_engine = lambda *args: (_ for _ in ()).throw(
            ValueError("fixture compile failure")
        )
        self.plugin.build(child)
        value = self.wait()
        self.assertEqual(value["phase"], "error")
        self.assertFalse(value["built"])
        self.assertEqual(
            (self.root / "copy/game/tl/studio_russian/script.rpy").read_bytes(), before
        )
        self.assertFalse((self.root / "copy/game/zz_studio_localization.rpy").exists())
        self.assertTrue(all(r["status"] == "edited" for r in self.records(child)))

    @patch("plugins.renpy.runtime.require_not_running")
    def test_tampered_copy_blocks_rebuild_and_rollback(self, running):
        state = self.prepare()
        child = state["workspace_project"]
        self.fill(child)
        self.plugin.build(child)
        self.assertTrue(self.wait()["built"])
        path = self.root / "copy/game/screens.rpy"
        path.write_text("# user change\n", encoding="utf-8")
        self.plugin.build(child)
        self.assertIn("изменён", self.wait()["message"])
        with self.assertRaises(ValueError):
            self.plugin.rollback(child)
        self.assertEqual(path.read_text(encoding="utf-8"), "# user change\n")

    def test_partial_patch_failure_is_transactional_and_paths_are_bounded(self):
        target = self.root / "patch"
        target.mkdir()
        (target / "a.rpy").write_bytes(b"original")
        replace = Path.replace

        def fail_second(path, destination):
            if Path(destination).name == "b.rpy":
                raise OSError("fixture write failure")
            return replace(path, destination)

        with patch.object(Path, "replace", fail_second):
            with self.assertRaises(OSError):
                apply_plan(
                    target,
                    {"a.rpy": b"changed", "b.rpy": b"new"},
                    self.root / "backups",
                )
        self.assertEqual((target / "a.rpy").read_bytes(), b"original")
        self.assertFalse((target / "b.rpy").exists())
        with self.assertRaises(ValueError):
            apply_plan(target, {"../outside": b"unsafe"}, self.root / "backups")

    @patch("plugins.renpy.runtime.require_not_running")
    def test_pipeline_uses_local_translation_then_review_without_saving_settings(
        self, running
    ):
        state = self.prepare()
        child = state["workspace_project"]
        seen = []

        def job(store, data):
            seen.append(data)
            jid = store.create_job(
                child, data["stage"], data["provider"], {"model": "fixture"}
            )
            self.fill(child, "translated" if data["stage"] == "translate" else "edited")
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='done',worker_active=0 WHERE id=?", (jid,)
                )
            return {"job": jid}

        with patch("commands.jobs.job", job):
            self.plugin.start_pipeline(child, {"provider": "cloud", "model": "fixture"})
            value = self.wait()
        self.assertTrue(value["built"], value)
        self.assertEqual([d["stage"] for d in seen], ["translate", "review"])
        self.assertTrue(
            all(
                d["provider"] == "local"
                and not d["persist_settings"]
                and d["settings"]["execution_kind"] == "local"
                for d in seen
            )
        )

    def test_incomplete_queue_blocks_prepare_and_never_resumes(self):
        self.store.import_files(self.project, [self.source / "game/script.rpy"])
        jid = self.store.create_job(
            self.project, "translate", "local", {"model": "fixture"}
        )
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (jid,))
        with self.assertRaises(ValueError):
            self.plugin.prepare(self.project, self.root / "copy")
        self.assertFalse((self.root / "copy").exists())

    def test_engine_mutation_blocks_native_translation_launch(self):
        from commands.jobs import job
        from job_runtime import launch

        self.store.engine_mutation = self.plugin
        with self.assertRaisesRegex(ValueError, "сборки"):
            job(self.store, {})
        with self.assertRaisesRegex(ValueError, "сборки"):
            launch(self.store, 1)

    @patch("plugins.renpy.runtime.require_not_running")
    def test_explicit_game_launch_uses_copy_saves_and_selected_language(self, running):
        state = self.prepare()
        child = state["workspace_project"]
        self.fill(child)
        self.plugin.build(child)
        self.assertTrue(self.wait()["built"])
        with patch("plugins.renpy.controller.subprocess.Popen") as process:
            process.return_value.pid = 4321
            result = self.plugin.launch_game(child, "original")
            args, kwargs = process.call_args
        self.assertEqual(result["pid"], 4321)
        self.assertIn(str(self.root / "copy/game/saves"), args[0])
        self.assertEqual(kwargs["env"]["STUDIO_RENPY_LANGUAGE"], "original")
        self.plugin.set_enabled(False)
        with self.assertRaises(ValueError):
            self.plugin.launch_game(child, "russian")

    def test_paused_pipeline_never_advances_to_review_or_build(self):
        state = self.prepare()
        child = state["workspace_project"]
        seen = []

        def job(store, data):
            seen.append(data["stage"])
            jid = store.create_job(child, "translate", "local", {"model": "fixture"})
            with store.db() as db:
                db.execute("UPDATE jobs SET state='paused' WHERE id=?", (jid,))
            return {"job": jid}

        with patch("commands.jobs.job", job):
            self.plugin.start_pipeline(child, {"model": "fixture"})
            value = self.wait()
        self.assertEqual(seen, ["translate"])
        self.assertFalse(value["built"])
        self.assertNotIn("compile", self.calls)

    def test_disabled_engine_removes_installation_capability(self):
        manager = PluginManager(self.store)
        self.addCleanup(manager.dispose)
        self.store.engine_status = manager.engine_status
        inspect_project(self.store, self.project)
        self.assertTrue(overview(self.store, self.project)["installation"]["available"])
        manager.entry("renpy")["provider"].set_enabled(False)
        self.assertFalse(
            overview(self.store, self.project)["installation"]["available"]
        )

    def test_voice_instruction_does_not_consume_dialogue_translation(self):
        path = self.source / "game/tl/test/script.rpy"
        path.parent.mkdir(parents=True)
        path.write_text(
            """translate test start_ab:
    # voice "voice.ogg"
    # e "A neutral sentence."
    voice "voice.ogg"
    e "Нейтральная фраза."
""",
            encoding="utf-8",
        )
        records, _, _ = extract(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "Нейтральная фраза.")
        self.assertEqual(records[0]["speaker"], "e")
