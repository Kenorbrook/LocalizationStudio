"""Explain lookalike Latin/Cyrillic letters without silently changing words."""

import re
from validation import TOKEN

MIXED_SCRIPT_PREFIX = "Смешаны латиница и кириллица внутри слова: "
WORDS = re.compile(r"[A-Za-zА-Яа-яЁё]+")


class ScriptRepairFailed(ValueError):
    """The targeted repair was exhausted; do not retry the whole record again."""


def mixed_script_word(source, text):
    original = set(WORDS.findall(TOKEN.sub("", source)))
    for word in WORDS.findall(TOKEN.sub("", text)):
        if (
            re.search("[A-Za-z]", word)
            and re.search("[А-Яа-яЁё]", word)
            and word not in original
        ):
            return word
    return ""


def mixed_script_message(word):
    # One representative of each alphabet makes visually identical letters clear.
    latin = re.search("[A-Za-z]", word).group()
    cyrillic = re.search("[А-Яа-яЁё]", word).group()
    return (
        MIXED_SCRIPT_PREFIX
        + word
        + f". В ответе модели: «{cyrillic}» U+{ord(cyrillic):04X} — кириллица, "
        + f"«{latin}» U+{ord(latin):04X} — латиница. Проверяется перевод, не оригинал."
    )


def repair_entry(entry, rejected, error):
    return {
        **entry,
        "rejected_translation": rejected,
        "validation_feedback": (
            error
            + "\nThe rejected_translation is an invalid model draft, not a reference. "
            "Translate the TARGET english again into natural Russian using its scene. "
            "Rewrite the faulty word in Russian; replacing only a lookalike letter "
            "and leaving an untranslated English word is not a correction. "
            "Do not mix Latin and Cyrillic letters inside a translated word. "
            "Preserve source identifiers, names, placeholders, tags and meaning. "
            "Return the same JSON format and requested id only."
        ),
    }
