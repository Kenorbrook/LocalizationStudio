"""Explain lookalike Latin/Cyrillic letters without silently changing words."""

import re
from validation import TOKEN

MIXED_SCRIPT_PREFIX = "Смешаны латиница и кириллица внутри слова: "
WORDS = re.compile(r"[A-Za-zА-Яа-яЁё]+")
LATIN_LOOKALIKES = dict(zip("ABCEHKMOPTXYaceopxy", "АВСЕНКМОРТХУасеорху"))


def normalize_russian_lookalikes(source, text):
    """Correct isolated visual typos only; never translate a mostly Latin word."""
    if not isinstance(text, str):
        return text, []
    original = {word.casefold() for word in WORDS.findall(source)}
    protected = [(m.start(), m.end()) for m in TOKEN.finditer(text)]
    changes = []

    def replace(match):
        word = match.group()
        if word.casefold() in original or any(
            a <= match.start() < b for a, b in protected
        ):
            return word
        # Do not rewrite untagged identifiers, numbers or Unicode compounds.
        for index in (match.start() - 1, match.end()):
            if 0 <= index < len(text) and (text[index].isalnum() or text[index] == "_"):
                return word
        latin = re.findall("[A-Za-z]", word)
        cyrillic = re.findall("[А-Яа-яЁё]", word)
        if not 1 <= len(latin) <= 2 or len(cyrillic) < 4 * len(latin):
            return word
        if any(character not in LATIN_LOOKALIKES for character in latin):
            return word
        if not word.isupper() and any(
            character.isupper()
            for character in word[1:]
            if character in LATIN_LOOKALIKES
        ):
            return word
        fixed = "".join(
            LATIN_LOOKALIKES.get(character, character) for character in word
        )
        changes.append((word, fixed))
        return fixed

    return WORDS.sub(replace, text), changes


class ScriptRepairFailed(ValueError):
    """The targeted repair was exhausted; do not retry the whole record again."""

    def __init__(self, message, candidate=""):
        super().__init__(message)
        self.candidate = candidate if isinstance(candidate, str) else ""


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


def retry_feedback(message):
    match = re.search(re.escape(MIXED_SCRIPT_PREFIX) + r"([A-Za-zА-Яа-яЁё]+)", message)
    if match and mixed_script_word("", match[1]):
        message = mixed_script_message(match[1])
    return (
        "A previous translation attempt FAILED validation: "
        + message[:1200]
        + "\nGenerate a fresh natural Russian translation of TARGET english using the scene. "
        "Do not copy the invalid spelling from this diagnostic. Russian words must be entirely "
        "Cyrillic; preserve genuine names, source identifiers and protected tokens. "
        "Translate English words rather than just replacing lookalike letters. "
        "Preserve meaning, grammar and register. Return only the required items JSON."
    )
