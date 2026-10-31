import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from providers import process_record
from script_validation import normalize_russian_lookalikes
from tests.test_script_repair import Model
from worker import run


class LookalikeNormalizationTests(unittest.TestCase):
    def test_repeated_copy_typo_is_fixed_without_another_model_request(self):
        model = Model("Я перепиcываю записку.")
        text, reason = process_record(
            model, "translate", {"id": 1, "english": "I copy the note.", "russian": ""}
        )
        self.assertEqual(text, "Я переписываю записку.")
        self.assertEqual(len(model.calls), 1)
        self.assertIn("перепиcываю → переписываю", reason)

    def test_visual_changes_are_bounded_and_reported(self):
        text, changes = normalize_russian_lookalikes(
            "A note.", "Пoжалуйста, перепиcываю записку. МОСКВA."
        )
        self.assertEqual(text, "Пожалуйста, переписываю записку. МОСКВА.")
        self.assertEqual(len(changes), 3)

    def test_mostly_latin_words_short_words_nonlookalikes_and_identifiers_stay(self):
        for word in [
            "Сemen",
            "Сеmen",
            "Теst",
            "тeст",
            "свиdания",
            "методC",
            "перепиcываю_id",
            "id_перепиcываю",
            "перепиcываю123",
            "123перепиcываю",
        ]:
            with self.subTest(word=word):
                self.assertEqual(
                    normalize_russian_lookalikes("A note.", word), (word, [])
                )

    def test_source_names_tokens_and_placeholders_are_never_normalized(self):
        source = "Brand Пoжалуйста and [перепиcываю] {Пoжалуйста} %(Пoжалуйста)s."
        text = "Brand Пoжалуйста и [перепиcываю] {Пoжалуйста} %(Пoжалуйста)s."
        self.assertEqual(normalize_russian_lookalikes(source, text), (text, []))
        protected = "[перепиcываю] {Пoжалуйста} %(Пoжалуйста)s"
        self.assertEqual(
            normalize_russian_lookalikes("Other.", protected), (protected, [])
        )

    def test_other_language_and_all_original_invariants_still_apply(self):
        model = Model("Я перепиcываю записку.", language="English")
        self.assertEqual(
            process_record(
                model,
                "translate",
                {"id": 1, "english": "I copy the note.", "russian": ""},
            )[0],
            "Я перепиcываю записку.",
        )
        with self.assertRaisesRegex(ValueError, "теги или переменные"):
            process_record(
                Model("Я перепиcываю записку.", "Я перепиcываю записку."),
                "translate",
                {"id": 1, "english": "[name] I copy the note.", "russian": ""},
            )

    def test_nonstring_and_already_valid_text_do_not_change(self):
        for text in [None, {}, "", "Я переписываю записку."]:
            self.assertEqual(normalize_russian_lookalikes("A note.", text), (text, []))

    def test_unfixed_latin_word_still_uses_model_repair(self):
        model = Model("Образец: Сemen.", "Образец: сперма.")
        self.assertEqual(
            process_record(
                model,
                "translate",
                {"id": 1, "english": "Sample: semen.", "russian": ""},
            )[0],
            "Образец: сперма.",
        )
        self.assertEqual(len(model.calls), 2)

    def test_worker_saves_fixed_word_and_history_reason_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = Store(root / "test.sqlite3")
            project = store.project(str(root))["id"]
            corpus = root / "text.json"
            corpus.write_text(json.dumps([{"source": "I copy the note."}]))
            store.import_files(project, [corpus])
            job = store.create_job(
                project,
                "translate",
                "local",
                {"model": "fixture", "auto_foreign": False},
            )

            class LocalModel:
                def __init__(self, project, kind, settings):
                    self.kind = kind
                    self.settings = settings

                def call(self, mode, entries):
                    self.calls += 1
                    return {
                        "items": [
                            {"id": entries[0]["id"], "text": "Я перепиcываю записку."}
                        ]
                    }

            with patch("job_runtime.launch"):
                run(store, job, LocalModel)
            row = store.record(1)
            self.assertEqual(row["text"], "Я переписываю записку.")
            with store.db() as db:
                self.assertEqual(
                    db.execute("select count(*) from errors").fetchone()[0], 0
                )
                self.assertIn(
                    "перепиcываю → переписываю",
                    db.execute("select reason from history").fetchone()[0],
                )
