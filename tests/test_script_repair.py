import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from providers import Provider, ServiceUnavailable, process_record
from script_validation import mixed_script_message, mixed_script_word
from worker import run


class Model:
    kind = "local"

    def __init__(self, *answers, language="Russian"):
        self.settings = {"target_language": language}
        self.answers = iter(answers)
        self.calls = []

    def call(self, mode, entries):
        self.calls.append(copy.deepcopy(entries[0]))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return {"items": [{"id": entries[0]["id"], "text": answer}]}


class ScriptRepairTests(unittest.TestCase):
    def entry(self, source="The test is ready."):
        return {
            "id": 1,
            "english": source,
            "russian": "",
            "context_before": [{"source": "Wait here."}],
            "context_after": [{"source": "Go ahead."}],
        }

    def test_repair_gets_defect_and_scene_and_does_not_mutate_target(self):
        entry = self.entry()
        before = copy.deepcopy(entry)
        model = Model("Теst готов.", "Тест готов.")
        text, reason = process_record(model, "translate", entry)
        self.assertEqual(text, "Тест готов.")
        self.assertIn("Автоисправлено", reason)
        self.assertEqual(len(model.calls), 2)
        repair = model.calls[1]
        self.assertEqual(repair["rejected_translation"], "Теst готов.")
        self.assertIn("U+0422", repair["validation_feedback"])
        self.assertIn("U+0073", repair["validation_feedback"])
        self.assertIn("not a correction", repair["validation_feedback"])
        self.assertEqual(repair["english"], entry["english"])
        self.assertEqual(repair["context_before"], entry["context_before"])
        self.assertEqual(entry, before)

    def test_persistent_mixture_stops_after_one_repair(self):
        model = Model("Теst готов.", "Теst готов.")
        with self.assertRaisesRegex(
            ValueError, "Автоисправление алфавита не прошло"
        ) as rejected:
            process_record(model, "translate", self.entry())
        self.assertEqual(rejected.exception.candidate, "Теst готов.")
        self.assertEqual(len(model.calls), 2)

    def test_repaired_output_must_still_pass_all_invariants(self):
        for second in ["Тест готов.", "", "As an AI language model, I cannot assist."]:
            with self.subTest(second=second):
                model = Model("[name] Теst готов.", second)
                with self.assertRaises(ValueError):
                    process_record(
                        model, "translate", self.entry("[name] The test is ready.")
                    )
                self.assertEqual(len(model.calls), 2)

    def test_unrelated_error_and_nonlocal_provider_do_not_get_extra_calls(self):
        for kind, answer in [
            ("local", ""),
            ("cloud", "Теst готов."),
            ("mcp", "Теst готов."),
        ]:
            with self.subTest(kind=kind, answer=answer):
                model = Model(answer)
                model.kind = kind
                with self.assertRaises(ValueError):
                    process_record(model, "translate", self.entry())
                self.assertEqual(len(model.calls), 1)

    def test_genuine_source_words_and_tokens_are_preserved(self):
        for source, answer in [
            ("Теst is ready.", "Теst готов."),
            ("[Теst] The test is ready.", "[Теst] Тест готов."),
            ("Open USB-C.", "Открой USB-C."),
        ]:
            model = Model(answer)
            self.assertEqual(
                process_record(model, "translate", self.entry(source))[0], answer
            )
            self.assertEqual(len(model.calls), 1)

    def test_other_target_language_has_no_russian_repair(self):
        model = Model("Теst ready.", language="English")
        self.assertEqual(
            process_record(model, "translate", self.entry())[0], "Теst ready."
        )
        self.assertEqual(len(model.calls), 1)

    def test_repair_offline_is_still_a_real_pause(self):
        model = Model("Теst готов.", ServiceUnavailable("Offline"))
        with self.assertRaises(ServiceUnavailable):
            process_record(model, "translate", self.entry())

    def test_repair_budget_trims_only_references_without_mutating_entry(self):
        model = Model("Теst готов.", "Тест готов.")
        entry = self.entry()
        before = copy.deepcopy(entry)
        model.fits_request = lambda mode, items: not items[0].get(
            "validation_feedback"
        ) or not items[0].get("context_before")
        self.assertEqual(process_record(model, "translate", entry)[0], "Тест готов.")
        self.assertEqual(model.calls[1]["context_before"], [])
        self.assertEqual(entry, before)

    def test_repair_too_large_is_explicit_and_does_not_generate(self):
        model = Model("Теst готов.")
        model.fits_request = lambda mode, items: not items[0].get("validation_feedback")
        with self.assertRaisesRegex(ValueError, "Автоисправление не помещается"):
            process_record(model, "translate", self.entry())
        self.assertEqual(len(model.calls), 1)

    def test_message_explains_response_and_unicode(self):
        message = mixed_script_message("Теst")
        self.assertIn("Проверяется перевод, не оригинал", message)
        self.assertIn("U+0422", message)
        self.assertEqual(mixed_script_word("A test.", "Теst."), "Теst")

    def test_worker_repair_cache_and_error_retry_preserve_good_revisions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "test.sqlite3")
            project = store.project(str(root))["id"]
            corpus = root / "corpus.json"
            corpus.write_text(
                json.dumps(
                    [
                        {"source": s}
                        for s in [
                            "The test is ready.",
                            "The next test is ready.",
                            "Goodbye.",
                        ]
                    ]
                )
            )
            store.import_files(project, [corpus])
            settings = {
                "model": "fixture",
                "context": 8192,
                "max_output": 1200,
                "auto_foreign": False,
            }
            job = store.create_job(project, "translate", "local", settings)
            generation = []
            allow_second = False

            def respond(request, **kwargs):
                if request.full_url.endswith("/api/tags"):
                    return io.BytesIO(b'{"models":[{"name":"fixture"}]}')
                payload = json.loads(request.data)
                item = json.loads(payload["messages"][-1]["content"])["items"][0]
                generation.append(item)
                bad = item["english"] != "Goodbye."
                repaired = bool(item.get("validation_feedback"))
                if bad:
                    valid = repaired and (
                        item["english"] == "The test is ready." or allow_second
                    )
                    text = "Тест готов." if valid else "Теst готов."
                else:
                    text = "До свидания."
                return io.BytesIO(
                    json.dumps(
                        {
                            "message": {
                                "content": json.dumps(
                                    {"items": [{"id": item["id"], "text": text}]}
                                )
                            }
                        }
                    ).encode()
                )

            with patch("providers.DIRECT.open", side_effect=respond):
                run(store, job)
            with store.db() as db:
                self.assertEqual(
                    db.execute("select state from jobs where id=?", (job,)).fetchone()[
                        0
                    ],
                    "incomplete",
                )
                rows = [
                    dict(r) for r in db.execute("select * from records order by id")
                ]
                self.assertEqual(
                    [r["text"] for r in rows], ["Тест готов.", "", "До свидания."]
                )
                self.assertEqual(
                    db.execute(
                        "select count(*) from errors where resolved=0"
                    ).fetchone()[0],
                    1,
                )
                self.assertTrue(
                    all(
                        "Теst" not in json.loads(r[0])["text"]
                        for r in db.execute("select value from fragment_cache")
                    )
                )
                self.assertEqual(
                    db.execute(
                        "select candidate from errors where resolved=0"
                    ).fetchone()[0],
                    "Теst готов.",
                )
                history = db.execute(
                    "select reason from history where record=?", (rows[0]["id"],)
                ).fetchone()[0]
                self.assertIn("Автоисправлено", history)
            self.assertEqual(len(generation), 5)
            allow_second = True
            retry = store.create_job(
                project, "translate", "local", settings, retry=True
            )
            with patch("providers.DIRECT.open", side_effect=respond):
                run(store, retry)
            self.assertEqual(len(generation), 7)
            with store.db() as db:
                self.assertEqual(
                    db.execute(
                        "select count(*) from errors where resolved=0"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    [
                        r[0]
                        for r in db.execute("select revision from records order by id")
                    ],
                    [1, 1, 1],
                )

    def test_bad_cached_fragment_is_regenerated_and_good_cache_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / "cache.sqlite3")
            model = Model(*(["Теst готов.", "Тест готов."] * 3))
            model.settings["_studio_db"] = str(store.path)
            model.settings["max_output"] = 80
            entry = self.entry("The test is ready. " * 8)
            process_record(model, "translate", entry)
            self.assertEqual(len(model.calls), 4)
            # Identical request must reuse the successful checkpoint.
            process_record(model, "translate", entry)
            self.assertEqual(len(model.calls), 4)
            with store.db() as db:
                self.assertEqual(
                    db.execute("select count(*) from fragment_cache").fetchone()[0], 2
                )
                db.execute(
                    "update fragment_cache set value=? where key=(select key from fragment_cache limit 1)",
                    (json.dumps({"text": "Теst готов.", "reason": "legacy"}),),
                )
            self.assertNotIn("Теst", process_record(model, "translate", entry)[0])
            self.assertEqual(len(model.calls), 6)
            with store.db() as db:
                self.assertEqual(
                    db.execute("select count(*) from fragment_cache").fetchone()[0], 2
                )
                self.assertEqual(
                    json.loads(
                        db.execute("select value from fragment_cache").fetchone()[0]
                    )["text"],
                    "Тест готов.",
                )

    def test_russian_prompt_explains_repair_fields(self):
        provider = Provider(
            {"id": 1, "root": "missing-fixture"}, "local", {}, diagnostic=True
        )
        self.assertIn("validation_feedback", provider.prompt("translate"))
        self.assertIn("rejected_translation", provider.prompt("translate"))

    def test_semen_homoglyph_regression_in_neutral_sample(self):
        for broken in ["Сemen", "Сеmen", "cпepмa"]:
            with self.subTest(broken=broken):
                model = Model(f"Образец: {broken}.", "Образец: сперма.")
                result, reason = process_record(
                    model, "translate", self.entry("Sample: semen.")
                )
                self.assertEqual(result, "Образец: сперма.")
                self.assertIn("Автоисправлено", reason)
                self.assertEqual(len(model.calls), 2)
                self.assertIn(broken, model.calls[1]["validation_feedback"])
                self.assertEqual(model.calls[1]["english"], "Sample: semen.")

    def test_valid_russian_terms_and_name_are_not_mixed_script(self):
        for source, text in [
            ("Sample: semen.", "Образец: сперма."),
            ("A seed sprouted.", "Семя проросло."),
            ("Semyon called.", "Семён позвонил."),
        ]:
            with self.subTest(source=source):
                model = Model(text)
                self.assertEqual(
                    process_record(model, "translate", self.entry(source))[0], text
                )
                self.assertEqual(len(model.calls), 1)

    def test_semen_repair_keeps_sample_identifier(self):
        model = Model("[sample_id]: Сemen.", "[sample_id]: сперма.")
        result, _ = process_record(
            model, "translate", self.entry("[sample_id]: semen.")
        )
        self.assertEqual(result, "[sample_id]: сперма.")
        self.assertEqual(len(model.calls), 2)
