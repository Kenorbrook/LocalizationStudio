import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from game_detection import inspect_project
from project_workflow import overview
from read_routes import read_request


class ProjectWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.store = Store(self.root / "test.sqlite3")
        self.project = self.store.project(str(self.game))["id"]

    def corpus(self):
        path = self.game / "dialogue.json"
        path.write_text(json.dumps([{"source": "A neutral line."}]))
        self.store.import_files(self.project, [path])
        return path

    def test_empty_project_is_not_a_completed_game_and_does_not_scan_or_infer(self):
        with (
            patch("game_detection.analyze") as scan,
            patch("providers.models") as models,
        ):
            value = overview(self.store, self.project)
        self.assertEqual(value["total"], 0)
        self.assertFalse(value["scan_available"])
        self.assertFalse(value["installation"]["available"])
        self.assertFalse(value["coverage_confirmed"])
        scan.assert_not_called()
        models.assert_not_called()

    def test_imported_text_never_claims_complete_extraction_or_installation(self):
        path = self.corpus()
        inspect_project(self.store, self.project)
        value = read_request(
            self.store, "/api/project-workflow", {"project": str(self.project)}, 0
        )
        self.assertEqual(value["total"], 1)
        self.assertEqual(value["empty"], 1)
        self.assertNotIn(path.name, value["pending_files"])
        self.assertFalse(value["coverage_confirmed"])
        self.assertFalse(value["installation"]["available"])
        self.assertEqual(value["files"][0]["export_kind"], "json")

    def test_remaining_supported_files_are_visible_without_importing_them(self):
        self.corpus()
        path = self.game / "extra.json"
        path.write_text(json.dumps([{"source": "Another neutral line."}]))
        inspect_project(self.store, self.project)
        self.assertIn("extra.json", overview(self.store, self.project)["pending_files"])
        self.assertEqual(overview(self.store, self.project)["total"], 1)

    def test_renpy_export_type_depends_on_existing_translation_layer(self):
        source = self.game / "script.rpy"
        source.write_text('label start:\n    "A neutral line."\n')
        translated = self.game / "tl/russian/script.rpy"
        translated.parent.mkdir(parents=True)
        translated.write_text(
            'translate russian start_123:\n    # "A neutral line."\n    ""\n'
        )
        self.store.import_files(self.project, [source, translated])
        files = {
            Path(f["path"]): f for f in overview(self.store, self.project)["files"]
        }
        self.assertEqual(files[source]["export_kind"], "json")
        self.assertEqual(files[translated]["export_kind"], "rpy")
        self.assertFalse(
            overview(self.store, self.project)["installation"]["available"]
        )

    def test_paused_cloud_queue_is_reported_without_resuming_or_switching_it(self):
        self.corpus()
        job = self.store.create_job(
            self.project, "translate", "cloud", {"cloud_model": "fixture-cloud"}
        )
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (job,))
        for _ in range(2):
            value = overview(self.store, self.project)
            self.assertEqual(value["jobs"][0]["provider"], "cloud")
            self.assertEqual(value["jobs"][0]["state"], "paused")
        with self.store.db() as db:
            row = db.execute(
                "SELECT state,provider FROM jobs WHERE id=?", (job,)
            ).fetchone()
        self.assertEqual(tuple(row), ("paused", "cloud"))

    def test_unknown_project_rejected(self):
        with self.assertRaises(ValueError):
            overview(self.store, 999999)
