import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core import Store
from error_recovery import adjustment, preference, plan, apply, after_job
from hermes_install import environment, prepare, install_commands, COMMIT
from hermes_plugin import HermesPlugin
from hermes_tools import call as agent_call
from mcp_server import handle

SETTINGS = {
    "model": "fixture:latest",
    "source_language": "English",
    "target_language": "Russian",
    "context": 8192,
    "max_output": 1200,
    "context_before": 4,
    "context_after": 4,
}
OOM = "Локальная модель (HTTP 500): не хватило оперативной памяти или видеопамяти"


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.store = Store(self.root / "data/test.sqlite3")
        self.store.launch_lock = threading.RLock()
        self.store.closing = False
        self.project = self.store.project(str(self.game))["id"]
        self.corpus = self.game / "text.json"
        self.corpus.write_text(
            json.dumps(
                [
                    {"source": "A neutral original sentence."},
                    {"source": "Another neutral original sentence."},
                ]
            )
        )
        self.store.import_files(self.project, [self.corpus])
        self.plugin = HermesPlugin(self.store)

    def failed(self, message=OOM, settings=None):
        jid = self.store.create_job(
            self.project, "translate", "local", settings or SETTINGS
        )
        self.store.error(jid, 1, message)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='incomplete' WHERE id=?", (jid,))
            db.execute(
                "UPDATE queue SET state=CASE WHEN record=1 THEN 'error' ELSE 'done' END WHERE job=?",
                (jid,),
            )
        return jid

    def test_plugin_is_offline_and_disabled_by_default(self):
        with patch("hermes_plugin.subprocess.Popen") as launch:
            state = self.plugin.snapshot(self.project)
        self.assertFalse(state["installed"])
        self.assertFalse(state["preferences"]["enabled"])
        self.assertFalse(state["automatic_retry"])
        launch.assert_not_called()
        self.assertFalse(self.plugin.root.exists())

    def test_configuration_persists_and_rejects_invalid_values(self):
        values = {"enabled": True, "model": "fixture:latest", "context": 65536}
        self.plugin.configure(values)
        self.assertEqual(HermesPlugin(self.store).preferences, values)
        for update in [{"context": 100}, {"enabled": "yes"}, {"model": "bad\nname"}]:
            with self.assertRaises(ValueError):
                self.plugin.configure({**values, **update})
        with self.assertRaises(ValueError):
            self.plugin.run(self.project, "research")

    def test_installer_requires_exact_hash_and_uses_private_stages(self):
        with self.assertRaises(ValueError):
            prepare(
                self.root / "install",
                opener=lambda *a, **k: io.BytesIO(b"changed installer"),
            )
        commands = list(install_commands(self.root / "install"))
        self.assertEqual(len(commands), 4)
        self.assertIn(COMMIT, commands[0][1])
        self.assertTrue(all("-NonInteractive" in command for _, command in commands))
        self.assertFalse(
            any(
                "products" in command or "gateway" in command for _, command in commands
            )
        )

    def test_child_environment_drops_inherited_provider_and_powershell_settings(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "secret",
                "HERMES_HOME": "wrong",
                "PSMODULEPATH": "wrong",
            },
        ):
            env = environment(self.root)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("PSMODULEPATH", env)
        self.assertEqual(env["HERMES_HOME"], str(self.root))
        self.assertIn("127.0.0.1", env["NO_PROXY"])

    def test_agent_mcp_cannot_use_normal_tools_or_other_project_arguments(self):
        self.store.agent_project = self.project
        names = [
            tool["name"]
            for tool in handle(self.store, {"id": 1, "method": "tools/list"})["result"][
                "tools"
            ]
        ]
        self.assertEqual(
            set(names),
            {
                "inspect_game_project",
                "read_game_text",
                "translation_errors",
                "retry_plan",
            },
        )
        denied = handle(
            self.store,
            {
                "id": 2,
                "method": "tools/call",
                "params": {"name": "read_record", "arguments": {"id": 1}},
            },
        )
        self.assertTrue(denied["result"]["isError"])
        value = agent_call(
            self.store, self.project, "read_game_text", {"path": "text.json"}
        )
        self.assertIn("neutral", value["text"])
        for path in [
            "../data/test.sqlite3",
            str(self.corpus),
            "text.json:stream",
            "text.exe",
            None,
        ]:
            with self.assertRaises((ValueError, TypeError)):
                agent_call(self.store, self.project, "read_game_text", {"path": path})

    def test_game_sample_is_bounded_and_read_only(self):
        self.corpus.write_text("x" * 20000)
        result = agent_call(
            self.store, self.project, "read_game_text", {"path": "text.json"}
        )
        self.assertEqual(len(result["text"]), 8192)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.corpus.stat().st_size, 20000)

    def test_recovery_plan_and_agent_do_not_launch_or_expose_credentials(self):
        self.failed(
            settings={
                **SETTINGS,
                "endpoint": "private",
                "custom_secret": "do-not-expose",
            }
        )
        with patch("job_runtime.launch") as launch:
            data = agent_call(self.store, self.project, "retry_plan", {})
        self.assertEqual(data["rows"][0]["parameters"]["context"], 4096)
        self.assertNotIn("settings", data["rows"][0])
        self.assertNotIn("do-not-expose", json.dumps(data))
        launch.assert_not_called()

    def test_recovery_is_opt_in_and_does_not_touch_unknown_or_service_failures(self):
        jid = self.failed()
        with patch("job_runtime.launch") as launch:
            self.assertEqual(after_job(self.store, jid), [])
            preference(self.store, self.project, True)
            with self.store.db() as db:
                db.execute("UPDATE jobs SET state='paused' WHERE id=?", (jid,))
            self.assertEqual(after_job(self.store, jid), [])
        launch.assert_not_called()
        self.assertIsNone(adjustment(SETTINGS, "HTTP 503: service offline"))
        self.assertIsNone(adjustment(SETTINGS, "Unknown HTTP 500"))

    def test_oom_retry_is_durable_bounded_and_does_not_requeue_other_records(self):
        jid = self.failed()
        preference(self.store, self.project, True)
        with patch("job_runtime.launch"):
            first = after_job(self.store, jid)
            self.assertEqual(len(first), 1)
            self.assertEqual(after_job(self.store, jid), [])
            child = first[0]
            with self.store.db() as db:
                job = db.execute("SELECT * FROM jobs WHERE id=?", (child,)).fetchone()
                self.assertEqual(json.loads(job["settings"])["context"], 4096)
                self.assertEqual(
                    [
                        row[0]
                        for row in db.execute(
                            "SELECT record FROM queue WHERE job=?", (child,)
                        )
                    ],
                    [1],
                )
                db.execute("UPDATE jobs SET state='incomplete' WHERE id=?", (child,))
            self.store.error(child, 1, OOM)
            second = after_job(self.store, child)
            self.assertEqual(len(second), 1)
            with self.store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='incomplete' WHERE id=?", (second[0],)
                )
            self.store.error(second[0], 1, OOM)
            self.assertEqual(after_job(self.store, second[0]), [])
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM queue WHERE job=? AND record=2", (jid,)
                ).fetchone()[0],
                "done",
            )

    def test_recovery_preserves_manual_edits_and_originals(self):
        jid = self.failed()
        row = self.store.record(1)
        self.store.update(
            1, row["revision"], "Ручная правка.", "translated", "human", manual=True
        )
        with patch("job_runtime.launch") as launch:
            self.assertEqual(apply(self.store, self.project, jid), [])
        launch.assert_not_called()
        self.assertEqual(self.store.record(1)["text"], "Ручная правка.")

    def test_recovery_does_not_resume_paused_job_or_launch_on_close(self):
        jid = self.failed()
        preference(self.store, self.project, True)
        another = self.store.create_job(self.project, "translate", "local", SETTINGS)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (another,))
        with patch("job_runtime.launch") as launch:
            self.assertEqual(after_job(self.store, jid), [])
            self.store.closing = True
            self.assertEqual(apply(self.store, self.project, jid), [])
        launch.assert_not_called()

    def test_context_and_output_have_different_adjustments(self):
        smaller, _ = adjustment(SETTINGS, "Часть не помещается в запрос")
        longer, _ = adjustment(SETTINGS, "Модель достигла лимита ответа")
        self.assertEqual(smaller["context"], SETTINGS["context"])
        self.assertEqual(smaller["context_before"], 0)
        self.assertEqual(longer["max_output"], 2400)
        self.assertIsNone(adjustment({**SETTINGS, "context": 2048}, OOM))

    def test_busy_plugin_prevents_translation_launch_and_app_update(self):
        from job_runtime import launch
        from update_controller import UpdateController

        self.store.hermes_busy = True
        self.store.hermes_inference = True
        with self.assertRaises(ValueError):
            launch(self.store, 999)
        updater = UpdateController(self.store, SimpleNamespace(), self.root, True)
        self.assertFalse(updater.idle())
        with self.assertRaises(ValueError):
            updater.install()

    def test_waiting_job_is_not_claimed_or_paused_while_agent_uses_model(self):
        from job_chain import schedule

        jid = self.store.create_job(
            self.project, "translate", "local", SETTINGS, defer=True
        )
        self.store.hermes_inference = True
        with patch("job_runtime.launch") as launch:
            self.assertIsNone(schedule(self.store, self.project))
            launch.assert_not_called()
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0],
                "waiting",
            )

    def test_worker_exit_only_triggers_recovery_after_normal_completion(self):
        from worker_supervision import watch_worker

        jid = self.failed()
        process = SimpleNamespace(pid=123, wait=lambda timeout=None: 0)
        with self.store.db() as db:
            db.execute(
                "UPDATE jobs SET worker_launch='fixture',worker_active=1 WHERE id=?",
                (jid,),
            )
        with patch("error_recovery.after_job") as recover:
            watch_worker(self.store, jid, process, "fixture")
            recover.assert_called_once_with(self.store, jid)
        process.wait = lambda timeout=None: 1
        with patch("error_recovery.after_job") as recover:
            watch_worker(self.store, jid, process, "fixture")
            recover.assert_not_called()

    def test_disabling_recovery_cancels_only_waiting_recovery_jobs(self):
        jid = self.failed()
        preference(self.store, self.project, True)
        user_job = self.store.create_job(
            self.project, "translate", "local", SETTINGS, defer=True
        )
        with patch("job_runtime.launch"):
            children = after_job(self.store, jid)
        self.assertEqual(len(children), 1)
        preference(self.store, self.project, False)
        with self.store.db() as db:
            self.assertEqual(
                db.execute(
                    "SELECT state FROM jobs WHERE id=?", (children[0],)
                ).fetchone()[0],
                "cancelled",
            )
            self.assertEqual(
                db.execute("SELECT state FROM jobs WHERE id=?", (user_job,)).fetchone()[
                    0
                ],
                "queued",
            )

    def test_agent_reports_do_not_leak_into_another_project(self):
        other = self.root / "other"
        other.mkdir()
        pid = self.store.project(str(other))["id"]
        self.plugin.status.update(
            project=self.project, report="Private first game report"
        )
        self.assertEqual(self.plugin.snapshot(pid)["report"], "")
        self.assertEqual(
            self.plugin.snapshot(self.project)["report"], "Private first game report"
        )

    def test_retry_batch_is_atomic_when_creating_second_group_fails(self):
        jid = self.failed()
        self.store.error(jid, 2, "Модель достигла лимита ответа")
        with patch.object(self.store, "next_id", side_effect=[2, OSError("disk full")]):
            with self.assertRaises(OSError):
                apply(self.store, self.project, jid)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT count(*) FROM recovery_jobs").fetchone()[0], 0
            )
