import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from commands import dispatch
from project_knowledge import validate_facts, save_batch, page, decide, select_context
from speaker_context import context_entry
from worker import run
from providers import Provider, ServiceUnavailable
from job_chain import schedule


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "test.db")
        self.pid = self.store.project(str(self.root))["id"]
        self.path = self.root / "corpus.json"
        self.path.write_text(
            json.dumps(
                [
                    {
                        "source": "I am Alice, the captain of Aurora.",
                        "speaker": "alice",
                        "scene": "bridge",
                    },
                    {
                        "source": "Alice, Bob is your brother.",
                        "speaker": "bob",
                        "scene": "bridge",
                    },
                    {
                        "source": "Aurora is our ship.",
                        "speaker": "alice",
                        "scene": "bridge",
                    },
                    {
                        "source": "We meet again, Alice.",
                        "speaker": "bob",
                        "scene": "ending",
                    },
                ]
            ),
            encoding="utf-8",
        )
        self.store.import_files(self.pid, [self.path])
        with self.store.db() as db:
            self.rows = [
                dict(r) for r in db.execute("SELECT * FROM records ORDER BY id")
            ]
        self.settings = {
            "model": "test",
            "context": 16384,
            "max_output": 2400,
            "source_language": "English",
            "target_language": "Russian",
        }

    def tearDown(self):
        self.temp.cleanup()

    def fact(self, **changes):
        value = {
            "kind": "character",
            "subject": "alice",
            "attribute": "role",
            "value": "Капитан Авроры",
            "mentions": ["Alice", "Aurora"],
            "confidence": 0.95,
            "uncertain": False,
            "reason": "Прямо представляется капитаном",
            "evidence": [{"id": self.rows[0]["id"], "quote": "the captain of Aurora"}],
        }
        value.update(changes)
        return value

    def save(self, key="one", **changes):
        facts = validate_facts({"facts": [self.fact(**changes)]}, self.rows)
        save_batch(self.store, self.pid, key, facts, self.rows)
        return page(self.store, self.pid)["rows"][0]

    def test_rejects_invented_evidence_and_cross_batch_ids(self):
        for evidence in [
            [{"id": self.rows[0]["id"], "quote": "invented"}],
            [{"id": 999, "quote": "Alice"}],
        ]:
            with self.assertRaises(ValueError):
                validate_facts({"facts": [self.fact(evidence=evidence)]}, self.rows)

    def test_retrieval_and_human_edit_preserve_translation(self):
        item = self.save()
        rid = self.rows[0]["id"]
        before = self.store.record(rid)
        self.assertEqual(
            context_entry(self.store, rid)["project_memory"][0]["status"], "auto"
        )
        decide(
            self.store, self.pid, item["id"], 0, "confirmed", "Капитан корабля «Аврора»"
        )
        memory = context_entry(self.store, rid)["project_memory"][0]
        self.assertEqual(memory["value"], "Капитан корабля «Аврора»")
        self.assertEqual(memory["status"], "confirmed")
        after = self.store.record(rid)
        self.assertEqual(
            (before["revision"], before["text"], before["manual"]),
            (after["revision"], after["text"], after["manual"]),
        )
        with self.assertRaises(ValueError):
            decide(self.store, self.pid, item["id"], 0, "rejected")

    def test_uncertainty_excluded_and_reported(self):
        self.save(uncertain=True, reason="Это сестра или брат?")
        entry = context_entry(self.store, self.rows[0]["id"])
        self.assertEqual(entry["project_memory"], [])
        self.assertIn("брат", entry["context_questions"][0]["question"])
        self.assertEqual(page(self.store, self.pid, state="review")["total"], 1)

    def test_conflicts_remove_automatic_facts(self):
        self.save()
        self.save("two", value="Не капитан, а пассажир")
        self.assertEqual(page(self.store, self.pid)["counts"], {"review": 2})
        self.assertEqual(
            context_entry(self.store, self.rows[0]["id"])["project_memory"], []
        )

    def test_source_change_invalidates_memory_and_confirmation(self):
        item = self.save()
        with self.store.db() as db:
            db.execute(
                "UPDATE records SET source='A changed line.' WHERE id=?",
                (self.rows[0]["id"],),
            )
        self.assertFalse(page(self.store, self.pid)["rows"][0]["live"])
        self.assertNotIn(
            "project_memory", context_entry(self.store, self.rows[1]["id"])
        )
        with self.assertRaises(ValueError):
            decide(self.store, self.pid, item["id"], 0, "confirmed")

    def test_source_changed_in_flight_does_not_checkpoint(self):
        facts = validate_facts({"facts": [self.fact()]}, self.rows)
        with self.store.db() as db:
            db.execute(
                "UPDATE records SET source='Changed' WHERE id=?", (self.rows[0]["id"],)
            )
        with self.assertRaises(ValueError):
            save_batch(self.store, self.pid, "changed", facts, self.rows)
        self.assertEqual(page(self.store, self.pid)["total"], 0)

    def test_scene_scope_and_project_isolation_and_switch(self):
        self.save(kind="scene", subject="bridge", attribute="summary")
        self.assertNotIn(
            "project_memory", context_entry(self.store, self.rows[-1]["id"])
        )
        self.assertNotIn(
            "project_memory",
            context_entry(
                self.store,
                self.rows[0]["id"],
                settings={"use_project_knowledge": False},
            ),
        )
        other = {**self.rows[0], "project": 999}
        self.assertEqual(select_context(self.store, other, self.rows, {}), {})

    def test_analysis_includes_manual_records_preserves_text_and_reuses_cache(self):
        rid = self.rows[0]["id"]
        self.store.update(rid, 0, "Я капитан.", "verified", "human", manual=True)
        calls = []
        outer = self

        class Fake:
            def __init__(self, *args):
                self.rules = {}
                self.calls = 0

            def fits_request(self, mode, rows):
                return True

            def call(self, mode, rows):
                calls.append(rows)
                return (
                    {"facts": [outer.fact()]}
                    if rid in {r["id"] for r in rows}
                    else {"facts": []}
                )

        for repeat in range(2):
            jid = self.store.create_job(self.pid, "analyze", "local", self.settings)
            run(self.store, jid, Fake)
            with self.store.db() as db:
                job = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
                self.assertEqual(
                    (job["state"], job["done"], job["worker_active"]), ("done", 4, 0)
                )
                self.assertGreater(job["run_done"], 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.record(rid)["text"], "Я капитан.")
        self.assertEqual(self.store.record(rid)["revision"], 1)

    def test_network_pause_then_resume(self):
        class Offline:
            def __init__(self, *args):
                self.rules = {}
                self.calls = 0

            def fits_request(self, *args):
                return True

            def call(self, *args):
                raise ServiceUnavailable("offline")

        jid = self.store.create_job(self.pid, "analyze", "local", self.settings)
        run(self.store, jid, Offline)
        with self.store.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            self.assertEqual((row["state"], row["done"]), ("paused", 0))

        class Online(Offline):
            def call(self, *args):
                return {"facts": []}

        run(self.store, jid, Online)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0],
                "done",
            )

    def test_late_result_does_not_apply_after_cancel(self):
        store = self.store

        class Late:
            def __init__(self, *args):
                self.rules = {}
                self.calls = 0

            def fits_request(self, *args):
                return True

            def call(self, *args):
                with store.db() as db:
                    db.execute("UPDATE jobs SET state='cancelled'")
                return {"facts": []}

        jid = store.create_job(self.pid, "analyze", "local", self.settings)
        run(store, jid, Late)
        with store.db() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM knowledge_batches").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT done FROM jobs WHERE id=?", (jid,)).fetchone()[0], 0
            )

    def test_chain_launches_translation_only_after_analysis(self):
        with patch("job_runtime.launch") as launch:
            result = dispatch(
                self.store,
                "job",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "provider": "local",
                    "settings": {**self.settings, "analyze_before_translate": True},
                },
            )
        analysis, translation = result["job"], result["translation_job"]
        launch.assert_called_once_with(self.store, analysis)
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM jobs WHERE id=?", (translation,)
                ).fetchone()[0],
                "waiting",
            )
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (analysis,))
        with patch("job_runtime.launch") as launch:
            self.assertEqual(schedule(self.store, self.pid), translation)
        launch.assert_called_once_with(self.store, translation)

    def test_cancelled_analysis_cannot_be_bypassed(self):
        with patch("job_runtime.launch"):
            result = dispatch(
                self.store,
                "job",
                {
                    "project": self.pid,
                    "stage": "translate",
                    "provider": "local",
                    "settings": {**self.settings, "analyze_before_translate": True},
                },
            )
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='cancelled' WHERE id=?", (result["job"],))
        with patch("job_runtime.launch") as launch:
            self.assertIsNone(schedule(self.store, self.pid))
            launch.assert_not_called()
        with self.assertRaises(ValueError), patch("job_runtime.launch") as launch:
            dispatch(
                self.store,
                "control",
                {"id": result["translation_job"], "mode": "resume"},
            )
            launch.assert_not_called()

    def test_rejected_fact_does_not_return_with_other_model(self):
        item = self.save()
        decide(self.store, self.pid, item["id"], 0, "rejected")
        self.save("other-model")
        self.assertEqual(page(self.store, self.pid)["counts"], {"rejected": 1})

    def test_short_speaker_id_does_not_match_every_word(self):
        self.save(subject="a", mentions=[])
        with self.store.db() as db:
            db.execute(
                "UPDATE records SET speaker='bob' WHERE id=?", (self.rows[-1]["id"],)
            )
        entry = context_entry(
            self.store,
            self.rows[-1]["id"],
            settings={"context_before": 0, "context_after": 0},
        )
        self.assertNotIn("project_memory", entry)

    def test_venice_payload_and_generic_provider(self):
        project = {"id": self.pid, "root": str(self.root)}
        for endpoint in ("https://api.venice.ai/api/v1", "https://example.com/v1"):
            with (
                patch("providers.secret", return_value="TEST"),
                patch(
                    "providers.json_request",
                    return_value={
                        "choices": [{"message": {"content": '{"facts":[]}'}}]
                    },
                ) as request,
            ):
                provider = Provider(
                    project,
                    "cloud",
                    {
                        **self.settings,
                        "endpoint": endpoint,
                        "cloud_model": "venice-uncensored-1-2",
                    },
                )
                provider.call("analyze", [])
                payload = request.call_args.args[1]
                if "venice.ai" in endpoint:
                    self.assertFalse(
                        payload["venice_parameters"]["include_venice_system_prompt"]
                    )
                    self.assertEqual(payload["max_completion_tokens"], 2400)
                    self.assertNotIn("max_tokens", payload)
                else:
                    self.assertNotIn("venice_parameters", payload)
                    self.assertEqual(payload["max_tokens"], 2400)

    def test_analysis_launcher_uses_visible_powershell_runner(self):
        from job_runtime import launch

        jid = self.store.create_job(self.pid, "analyze", "local", self.settings)
        with patch("job_runtime.subprocess.Popen") as popen:
            popen.return_value.pid = 1234
            launch(self.store, jid)
        args = popen.call_args.args[0]
        self.assertIn("powershell.exe", args)
        self.assertIn("-StudioJob", args)
        self.assertIn("-StudioPython", args)


if __name__ == "__main__":
    unittest.main()
