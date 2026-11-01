"""Real Ren'Py translation blocks must inherit context without sibling-branch leaks."""

import json
import tempfile
import unittest
from pathlib import Path

from core import Store
from plugin_backend import seed_module

context = seed_module("hermes", "renpy_context")
resolution = seed_module("hermes", "error_resolution")

SCRIPT = """label meeting:
    guide "Are you coming to the meeting?"
    "I need to decide."
    menu:
        "Go":
            jump club
        "Don't Go":
            jump home
label club:
    guide "Welcome to the club."
label home:
    guide "Welcome home."
"""


class HermesRenpyContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "data/studio.sqlite3")
        self.game = self.root / "game"
        self.game.mkdir()
        self.original = self.game / "script.rpy"
        self.original.write_text(SCRIPT, encoding="utf-8")
        self.tl = self.game / "tl/russian/script.rpy"
        self.tl.parent.mkdir(parents=True)
        rows = [
            (
                2,
                'guide "Are you coming to the meeting?"',
                'guide "Ты придёшь на встречу?"',
            ),
            (3, '"I need to decide."', '"Мне нужно решить."'),
            (5, '"Go"', '"Пойти"'),
            (7, '"Don\'t Go"', '"Не идти"'),
            (10, 'guide "Welcome to the club."', 'guide "Добро пожаловать в клуб."'),
            (12, 'guide "Welcome home."', 'guide "Добро пожаловать домой."'),
        ]
        self.tl.write_text(
            "\n".join(
                f"translate russian example_{i}:\n    # game/script.rpy:{line}\n    # {source}\n    {target}\n"
                for i, (line, source, target) in enumerate(rows)
            ),
            encoding="utf-8",
        )
        self.project = self.store.project(str(self.root))["id"]
        self.store.import_files(self.project, [self.tl])

    def test_tl_choice_gets_parent_question_and_original_menu_syntax(self):
        entry = context.context_entry(self.store, 3)
        self.assertEqual([r["id"] for r in entry["context_before"]], [1, 2])
        self.assertEqual(entry["context_after"], [])
        self.assertEqual(entry["speaker_metadata"]["kind"], "menu_choice")
        origin = entry["source_structure"]
        self.assertEqual(
            (origin["path"], origin["line"], origin["status"]),
            ("game/script.rpy", 5, "matched"),
        )
        self.assertIn("menu:", origin["excerpt"])
        self.assertIn("jump club", origin["excerpt"])
        self.assertEqual(entry["context_summary"]["before"], 2)
        self.assertTrue(
            all(r["relation"] == "shared_ancestor" for r in entry["context_before"])
        )

    def test_other_menu_option_and_jump_labels_are_not_sequential_neighbors(self):
        entry = context.context_entry(self.store, 4)
        ids = [r["id"] for r in entry["context_before"] + entry["context_after"]]
        self.assertEqual(ids, [1, 2])
        self.assertNotIn(3, ids)
        self.assertNotIn(5, ids)
        self.assertNotIn(6, ids)

    def test_native_script_also_identifies_menu_choice(self):
        with self.store.db() as db:
            db.execute("DELETE FROM files")
        self.store.import_files(self.project, [self.original])
        with self.store.db() as db:
            rid = db.execute(
                "SELECT id FROM records WHERE source='Go' ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        entry = context.context_entry(self.store, rid)
        self.assertEqual(entry["speaker_metadata"]["kind"], "menu_choice")
        self.assertTrue(entry["context_before"])

    def test_changed_original_is_visible_and_blocks_stale_commit(self):
        settings = {"model": "fixture", "context": 8192, "target_language": "Russian"}
        job = self.store.create_job(self.project, "review", "local", settings)
        self.store.error(job, 3, "Редактор не подтвердил результат")
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused'")
        snap = resolution.capture(
            self.store,
            self.project,
            resolution.pending(self.store, self.project)["rows"][0],
        )
        self.assertEqual(snap["data"]["line_kind"], "menu_choice")
        self.original.write_text(
            SCRIPT.replace("Are you coming", "Are you leaving"), encoding="utf-8"
        )
        proposal = {
            "record": 3,
            "revision": 0,
            "action": "keep",
            "text": "Пойти",
            "reason": "Menu action",
            "evidence": [{"record": 3, "quote": "Go"}],
        }
        verification = {
            k: v for k, v in proposal.items() if k not in {"action", "text"}
        } | {"verdict": "confirm"}
        with self.assertRaisesRegex(ValueError, "Контекст"):
            resolution.commit(self.store, snap, proposal, verification, "fixture")
        self.assertEqual(self.store.record(3)["revision"], 0)

    def test_missing_or_changed_referenced_source_is_explicit(self):
        self.original.write_text(SCRIPT.replace('"Go":', '"Leave":'), encoding="utf-8")
        entry = context.context_entry(self.store, 3)
        self.assertEqual(entry["source_structure"]["status"], "original_source_changed")
        self.assertNotEqual(entry["speaker_metadata"]["kind"], "menu_choice")
        self.original.unlink()
        entry = context.context_entry(self.store, 3)
        self.assertEqual(entry["source_structure"]["status"], "original_unavailable")

    def test_unsafe_reference_is_not_followed(self):
        file = {
            "original": 'translate russian example:\n    # game/../../outside.rpy:1\n    # "Go"\n    "Пойти"',
            "path": str(self.tl),
            "hash": "hash",
        }
        row = {"source": "Go", "locator": json.dumps({"line": 3})}
        self.assertEqual(
            context.structure({"root": str(self.root)}, file, row)["status"],
            "unsafe_original_reference",
        )

    def test_report_shows_context_even_when_model_claims_it_is_missing(self):
        controller = seed_module("hermes", "hermes_plugin").HermesPlugin(self.store)
        job = self.store.create_job(
            self.project, "review", "local", {"model": "fixture"}
        )
        self.store.error(job, 3, "Редактор не подтвердил результат")
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused'")

        def uncertain(_):
            return json.dumps(
                {
                    "record": 3,
                    "revision": 0,
                    "action": "uncertain",
                    "reason": "Not enough context",
                }
            )

        resolution.run(controller, self.project, uncertain)
        self.assertIn("2 до / 0 после", controller.status["report"])
        self.assertIn("тип: пункт меню", controller.status["report"])
        self.assertEqual(self.store.record(3)["revision"], 0)


if __name__ == "__main__":
    unittest.main()
