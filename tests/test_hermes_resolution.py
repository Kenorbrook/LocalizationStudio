"""Contextual repairs resolve real attempts without trusting prose or stale data."""

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from core import Store
from plugin_backend import seed_module

resolution = seed_module("hermes", "error_resolution")
HermesPlugin = seed_module("hermes", "hermes_plugin").HermesPlugin


class HermesResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "data/studio.sqlite3")
        self.store.launch_lock = threading.RLock()
        self.project = self.store.project(str(self.root))["id"]
        self.settings = {
            "model": "neutral",
            "target_language": "Russian",
            "context": 8192,
        }
        corpus = self.root / "text.json"
        corpus.write_text(
            json.dumps(
                [
                    {
                        "source": "Her father moved for work.",
                        "translation": "Её отец переехал из-за работы.",
                        "scene": "room",
                    },
                    {
                        "source": "The transfer happened suddenly.",
                        "translation": "Перевод произошёл неожиданно.",
                        "scene": "room",
                    },
                    {
                        "source": "The door is closed.",
                        "translation": "Дверь открыта.",
                        "scene": "room",
                    },
                    {
                        "source": "Go",
                        "translation": "Пойти",
                        "utterance_kind": "menu_choice",
                        "scene": "room",
                    },
                    {
                        "source": "An unrelated scene.",
                        "translation": "Другая сцена.",
                        "scene": "other",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.store.import_files(self.project, [corpus])
        with self.store.db() as db:
            db.execute(
                "UPDATE projects SET settings=? WHERE id=?",
                (json.dumps(self.settings), self.project),
            )
        self.job = self.store.create_job(self.project, "review", "local", self.settings)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (self.job,))
        self.controller = HermesPlugin(self.store)
        self.controller.preferences.update(enabled=True, model="neutral-agent")

    def snapshot(self, rid=2):
        self.store.error(
            self.job, rid, "Редактор не подтвердил результат: neutral uncertainty"
        )
        item = next(
            r
            for r in resolution.pending(self.store, self.project)["rows"]
            if r["record"] == rid
        )
        return resolution.capture(self.store, self.project, item)

    def proposal(self, snapshot, action="keep", text=None):
        rid = snapshot["row"]["id"]
        return {
            "record": rid,
            "revision": snapshot["row"]["revision"],
            "action": action,
            "text": snapshot["row"]["text"] if text is None else text,
            "reason": "Проверено по оригиналу и соседней реплике.",
            "evidence": [{"record": rid, "quote": snapshot["row"]["source"]}],
        }

    def verifier(self, snapshot, verdict="confirm"):
        value = self.proposal(snapshot)
        return {k: v for k, v in value.items() if k not in {"action", "text"}} | {
            "verdict": verdict
        }

    def test_context_includes_scene_speaker_rules_and_menu_type_without_secrets(self):
        config = self.root / "translation_tools/localization_config.json"
        config.parent.mkdir()
        config.write_text(
            json.dumps(
                {"glossary": {"transfer": "перевод по работе"}, "api_key": "private"}
            )
        )
        with self.store.db() as db:
            db.execute(
                "UPDATE projects SET settings=?",
                (json.dumps({**self.settings, "api_key": "private"}),),
            )
        snap = self.snapshot()
        self.assertIn(
            "Her father", snap["data"]["entry"]["context_before"][0]["source"]
        )
        self.assertNotIn("unrelated", json.dumps(snap["data"]).lower())
        self.assertIn("glossary", snap["data"]["project_rules"])
        self.assertNotIn("private", resolution.prompt(snap))
        menu = self.snapshot(4)
        self.assertEqual(menu["data"]["line_kind"], "menu_choice")
        self.assertIn("инфинитив", resolution.prompt(menu))

    def test_keep_closes_duplicate_errors_with_history_and_no_text_change(self):
        self.store.error(self.job, 2, "Previous attempt")
        snap = self.snapshot()
        decision = resolution.parse(json.dumps(self.proposal(snap)), snap)
        verifier = resolution.parse(json.dumps(self.verifier(snap)), snap, True)
        resolution.commit(self.store, snap, decision, verifier, "neutral-agent")
        row = self.store.record(2)
        self.assertEqual(row["text"], snap["row"]["text"])
        self.assertEqual(
            (row["status"], row["revision"], row["manual"]), ("edited", 1, 0)
        )
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM errors WHERE resolved=0").fetchone()[
                    0
                ],
                0,
            )
            self.assertEqual(
                db.execute(
                    "SELECT count(*) FROM history WHERE record=2 AND reviewer LIKE 'Hermes:%'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute(
                    "SELECT text FROM revision_snapshots WHERE record=2"
                ).fetchone()[0],
                snap["row"]["text"],
            )
        self.assertEqual(resolution.pending(self.store, self.project)["eligible"], 0)

    def test_fix_is_checked_and_atomic_and_does_not_change_job_or_other_record(self):
        snap = self.snapshot(3)
        decision = self.proposal(snap, "fix", "Дверь закрыта.")
        resolution.parse(json.dumps(decision), snap)
        resolution.commit(
            self.store, snap, decision, self.verifier(snap), "neutral-agent"
        )
        self.assertEqual(self.store.record(3)["text"], "Дверь закрыта.")
        self.assertEqual(self.store.record(2)["revision"], 0)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id=?", (self.job,)).fetchone()[
                    0
                ],
                "paused",
            )

    def test_ambiguous_verdict_cannot_commit(self):
        snap = self.snapshot()
        with self.assertRaisesRegex(ValueError, "не подтвердила"):
            resolution.commit(
                self.store,
                snap,
                self.proposal(snap),
                self.verifier(snap, "uncertain"),
                "agent",
            )
        self.assertEqual(self.store.record(2)["revision"], 0)

    def test_bad_json_ids_evidence_and_keep_draft_are_rejected(self):
        snap = self.snapshot()
        for update in [
            {"record": 999},
            {"revision": True},
            {"action": "delete"},
            {"text": "Иной текст"},
            {"evidence": [{"record": 999, "quote": "invented"}]},
            {"evidence": []},
            {"evidence": [{"record": 2, "quote": "invented"}]},
        ]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                resolution.parse(json.dumps({**self.proposal(snap), **update}), snap)
        for raw in ["plain prose", "[]", "{}", "```python\nprint('bad')\n```"]:
            with self.assertRaises(ValueError):
                resolution.parse(raw, snap)
        valid = json.dumps(self.proposal(snap))
        self.assertEqual(
            resolution.parse("```json\n" + valid + "\n```", snap)["action"], "keep"
        )

    def test_placeholders_script_refusal_and_missing_translation_stay_open(self):
        snap = self.snapshot(3)
        for text in [
            "The door is closed.",
            "Дверь заkрыта.",
            "Я не могу перевести этот текст.",
            "Дверь закрыта. {new}",
            "",
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                resolution.parse(json.dumps(self.proposal(snap, "fix", text)), snap)
        self.assertEqual(self.store.record(3)["revision"], 0)

    def test_revision_context_rules_new_errors_and_protection_block_stale_save(self):
        for mutation in [
            "target",
            "neighbor",
            "settings",
            "error",
            "manual",
            "mark",
            "worker",
        ]:
            with self.subTest(mutation=mutation):
                snap = self.snapshot(3)
                with self.store.db() as db:
                    if mutation == "target":
                        db.execute("UPDATE records SET revision=revision+1 WHERE id=3")
                    elif mutation == "neighbor":
                        db.execute("UPDATE records SET revision=revision+1 WHERE id=1")
                    elif mutation == "settings":
                        db.execute("UPDATE projects SET settings='{}'")
                    elif mutation == "error":
                        db.execute(
                            "INSERT INTO errors(project,job,record,message) VALUES (?,?,3,'new')",
                            (self.project, self.job),
                        )
                    elif mutation == "manual":
                        db.execute("UPDATE records SET manual=1 WHERE id=3")
                    elif mutation == "mark":
                        db.execute(
                            "INSERT OR REPLACE INTO record_marks(record,kind) VALUES(3,'review')"
                        )
                    else:
                        db.execute(
                            "UPDATE jobs SET worker_active=1 WHERE id=?", (self.job,)
                        )
                with self.assertRaises(ValueError):
                    resolution.commit(
                        self.store,
                        snap,
                        self.proposal(snap, "fix", "Дверь закрыта."),
                        self.verifier(snap),
                        "agent",
                    )
                self.assertEqual(self.store.record(3)["text"], "Дверь открыта.")
                with self.store.db() as db:
                    db.execute("UPDATE records SET manual=0 WHERE id=3")
                    db.execute("DELETE FROM record_marks")
                    db.execute(
                        "UPDATE projects SET settings=?", (json.dumps(self.settings),)
                    )
                    db.execute("UPDATE jobs SET worker_active=0")

    def test_scope_dedup_and_protected_records_are_not_queued(self):
        self.snapshot(2)
        self.snapshot(2)
        self.snapshot(3)
        with self.store.db() as db:
            db.execute("UPDATE records SET manual=1 WHERE id=3")
        selected = resolution.pending(self.store, self.project)
        self.assertEqual((selected["eligible"], selected["protected"]), (1, 1))
        self.assertEqual(resolution.pending(self.store, 999)["rows"], [])
        snap = resolution.capture(self.store, self.project, selected["rows"][0])
        snap["project"] = 999
        with self.assertRaises(ValueError):
            resolution.commit(
                self.store, snap, self.proposal(snap), self.verifier(snap), "agent"
            )

    def test_batch_runs_two_fresh_calls_per_decision_and_persists_backup_summary(self):
        self.snapshot(2)
        self.snapshot(3)
        self.snapshot(4)
        calls = []

        def request(prompt):
            payload = json.loads(prompt.split("\nDATA:\n")[1])
            calls.append(payload)
            snap = {"row": self.store.record(payload["record"])}
            if "proposal" in payload:
                result = self.verifier(snap)
            elif payload["record"] == 3:
                result = self.proposal(snap, "fix", "Дверь закрыта.")
            elif payload["record"] == 4:
                result = {
                    "record": 4,
                    "revision": 0,
                    "action": "uncertain",
                    "reason": "Нужна проверка меню.",
                }
            else:
                result = self.proposal(snap)
            return json.dumps(result)

        resolution.run(self.controller, self.project, request)
        report = json.loads(
            (self.controller.root / f"project-{self.project}-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["counts"], {"fix": 1, "keep": 1, "attention": 1})
        self.assertEqual(len(calls), 5)
        self.assertEqual(sum("proposal" in c for c in calls), 2)
        self.assertEqual(resolution.pending(self.store, self.project)["eligible"], 1)
        with closing(sqlite3.connect(report["backup"])) as db:
            self.assertEqual(
                db.execute("SELECT text FROM records WHERE id=3").fetchone()[0],
                "Дверь открыта.",
            )
        self.assertIn("Стало: Дверь закрыта.", report["text"])

    def test_cancel_after_model_response_prevents_commit(self):
        snap = self.snapshot()

        def request(_):
            self.controller.cancelled.set()
            return json.dumps(self.proposal(snap))

        resolution.run(self.controller, self.project, request)
        self.assertEqual(self.store.record(2)["revision"], 0)
        self.assertEqual(resolution.pending(self.store, self.project)["eligible"], 1)

    def test_invalid_model_reply_leaves_error_and_continues_next_record(self):
        snap = self.snapshot()
        self.snapshot(3)
        replies = iter(
            [
                "bad JSON",
                json.dumps({**self.proposal(snap), "record": 3, "action": "uncertain"}),
            ]
        )
        resolution.run(self.controller, self.project, lambda _: next(replies))
        self.assertEqual(self.controller.status["repair_progress"]["attention"], 2)
        self.assertEqual(resolution.pending(self.store, self.project)["eligible"], 2)

    def test_snapshot_does_not_leak_repair_progress_across_projects(self):
        self.controller.status.update(
            project=self.project,
            report="private",
            report_mode="repair_errors",
            repair_progress={"done": 1},
        )
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        snapshot = self.controller.snapshot(pid)
        self.assertEqual(snapshot["report"], "")
        self.assertNotIn("repair_progress", snapshot)

    def test_changed_import_or_knowledge_blocks_save_without_closing_error(self):
        from project_knowledge import source_hash

        snap = self.snapshot(3)
        with self.store.db() as db:
            db.execute("UPDATE files SET hash='changed'")
        with self.assertRaises(ValueError):
            resolution.commit(
                self.store,
                snap,
                self.proposal(snap, "fix", "Дверь закрыта."),
                self.verifier(snap),
                "agent",
            )
        snap = self.snapshot(3)
        with self.store.db() as db:
            data = json.dumps({"mentions": [], "evidence": [3], "reason": "context"})
            item = db.execute(
                "INSERT INTO knowledge_items(project,kind,subject,attribute,value,data,state) VALUES (?,'term','door','translation','дверь',?,'confirmed')",
                (self.project, data),
            ).lastrowid
            db.execute(
                "INSERT INTO knowledge_evidence VALUES (?,?,?)",
                (item, 3, source_hash(snap["row"]["source"])),
            )
        with self.assertRaisesRegex(ValueError, "справочник"):
            resolution.commit(
                self.store,
                snap,
                self.proposal(snap, "fix", "Дверь закрыта."),
                self.verifier(snap),
                "agent",
            )
        self.assertEqual(self.store.record(3)["revision"], 0)

    def test_uncertain_cases_do_not_starve_unseen_errors_in_next_batch(self):
        self.snapshot(2)
        self.snapshot(3)

        def uncertain(prompt):
            data = json.loads(prompt.split("\nDATA:\n")[1])
            return json.dumps(
                {
                    "record": data["record"],
                    "revision": data["revision"],
                    "action": "uncertain",
                    "reason": "Needs a human",
                }
            )

        with patch.object(resolution, "LIMIT", 1):
            resolution.run(self.controller, self.project, uncertain)
            self.assertEqual(
                resolution.pending(self.store, self.project)["rows"][0]["record"], 3
            )
            resolution.run(self.controller, self.project, uncertain)
            self.assertEqual(
                resolution.pending(self.store, self.project)["rows"][0]["record"], 2
            )
        self.store.error(self.job, 3, "New failure")
        self.assertEqual(
            resolution.pending(self.store, self.project)["rows"][0]["record"], 3
        )

    def test_rejected_rewrite_can_confirm_original_after_fresh_third_call(self):
        snap = self.snapshot(2)
        with self.store.db() as db:
            db.execute(
                "UPDATE queue SET state='error' WHERE job=? AND record=2", (self.job,)
            )
        replies = iter(
            [
                json.dumps(self.proposal(snap, "fix", "Его перевели неожиданно.")),
                json.dumps(self.verifier(snap, "keep")),
                json.dumps(self.verifier(snap, "confirm")),
            ]
        )
        calls = []

        def request(prompt):
            calls.append(json.loads(prompt.split("\nDATA:\n")[1]))
            return next(replies)

        resolution.run(self.controller, self.project, request)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1]["proposal"]["action"], "keep")
        self.assertEqual(self.store.record(2)["text"], snap["row"]["text"])
        self.assertEqual(self.controller.status["repair_progress"]["keep"], 1)
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM queue WHERE job=? AND record=2", (self.job,)
                ).fetchone()[0],
                "done",
            )
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id=?", (self.job,)).fetchone()[
                    0
                ],
                "paused",
            )


if __name__ == "__main__":
    unittest.main()
