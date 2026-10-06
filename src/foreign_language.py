"""Conservative offline language gate; no model generation or network requests."""

import hashlib, json, re
from functools import lru_cache
from core import TOKEN, dump

ALIASES = {
    "en": "ENGLISH",
    "английский": "ENGLISH",
    "ru": "RUSSIAN",
    "русский": "RUSSIAN",
    "fr": "FRENCH",
    "французский": "FRENCH",
    "la": "LATIN",
    "латынь": "LATIN",
    "de": "GERMAN",
    "немецкий": "GERMAN",
    "es": "SPANISH",
    "испанский": "SPANISH",
    "it": "ITALIAN",
    "итальянский": "ITALIAN",
    "ja": "JAPANESE",
    "японский": "JAPANESE",
    "zh": "CHINESE",
    "китайский": "CHINESE",
    "pt": "PORTUGUESE",
    "ko": "KOREAN",
    "uk": "UKRAINIAN",
}
VERSION = 2
NAMES = {
    "FRENCH": "французский",
    "LATIN": "латынь",
    "ENGLISH": "английский",
    "GERMAN": "немецкий",
    "SPANISH": "испанский",
    "ITALIAN": "итальянский",
    "RUSSIAN": "русский",
    "JAPANESE": "японский",
    "CHINESE": "китайский",
    "PORTUGUESE": "португальский",
}


@lru_cache(maxsize=1)
def detector():
    from lingua import LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()


@lru_cache(maxsize=4)
def short_detector(base_name):
    from lingua import Language, LanguageDetectorBuilder

    names = {
        "ENGLISH",
        "FRENCH",
        "LATIN",
        "SPANISH",
        "GERMAN",
        "ITALIAN",
        "PORTUGUESE",
        base_name,
    }
    return LanguageDetectorBuilder.from_languages(
        *(getattr(Language, name) for name in sorted(names))
    ).build()


@lru_cache(maxsize=8)
def pair_detector(base_name, other_name):
    from lingua import Language, LanguageDetectorBuilder

    return LanguageDetectorBuilder.from_languages(
        getattr(Language, base_name), getattr(Language, other_name)
    ).build()


def detect(text, source_language):
    from lingua import Language

    clean = TOKEN.sub(" ", text)
    clean = re.sub(r"https?://\S+", " ", clean)
    words = re.findall(r"[^\W\d_]+", clean, re.UNICODE)
    letters = sum(c.isalpha() for c in clean)
    if len(words) < 3 or letters < 12 or all(w[0].isupper() for w in words):
        return {"action": "none"}
    name = ALIASES.get(
        str(source_language).strip().lower(), str(source_language).strip().upper()
    )
    base = getattr(Language, name, None)
    if base is None:
        return {"action": "none"}
    scores = detector().compute_language_confidence_values(clean[:10000])
    if not scores:
        return {"action": "none"}
    all_top = scores[0]
    if len(clean) < 200:
        common = short_detector(base.name).compute_language_confidence_values(clean)
        if common and common[0].language == base and common[0].value >= 0.40:
            return {"action": "none"}
        common_names = {x.language.name for x in common}
        if scores[0].language.name in common_names or scores[0].value < 0.55:
            scores = common
        else:
            scores = pair_detector(
                base.name, scores[0].language.name
            ).compute_language_confidence_values(clean)
    top = scores[0]
    language = top.language.name
    if top.language == base:
        return {"action": "none"}
    base_score = next((x.value for x in scores if x.language == base), 0)
    second = scores[1].value if len(scores) > 1 else 0
    latin_evidence = (
        language == "LATIN"
        and top.value >= 0.40
        and top.value - second >= 0.20
        and base_score <= 0.05
    )
    if not latin_evidence and (
        top.value < 0.85 or top.value - second < 0.40 or base_score > 0.15
    ):
        return {"action": "none"}
    mixed = any(
        x.language == base and x.word_count >= 2
        for x in detector().detect_multiple_languages_of(clean[:10000])
    )
    segments = [
        part.strip()
        for part in re.split(r'[.!?;\n:«»“”"]+', clean[:10000])
        if part.strip()
    ]
    if len(segments) > 1 and not mixed:
        for part in segments:
            if len(re.findall(r"[^\W\d_]+", part, re.UNICODE)) < 2:
                continue
            values = short_detector(base.name).compute_language_confidence_values(part)
            if values and values[0].language == base and values[0].value >= 0.55:
                mixed = True
                break
    certain = (
        not all(w[0].isupper() for w in words)
        and len(clean) <= 10000
        and len(words) >= 3
        and letters >= 12
        and (
            top.value >= 0.97
            or (
                len(words) >= 6
                and top.value >= 0.95
                and all_top.language == top.language
                and all_top.value >= 0.99
            )
        )
        and top.value - second >= 0.75
        and base_score <= 0.08
        and not mixed
    )
    action = "preserve" if certain else "review"
    label = NAMES.get(language, language.title())
    reason = (
        "Другой язык: "
        + label
        + ". Основной язык проекта: "
        + str(source_language)
        + ". "
        + (
            "Оригинал сохранён автоматически, чтобы сохранить иноязычную реплику."
            if certain
            else "Возможно иноязычная или смешанная реплика; требуется проверка человеком."
        )
    )
    return {
        "action": action,
        "language": language,
        "score": round(top.value, 3),
        "reason": reason,
    }


def check_record(store, rid, settings, worker_job=None):
    r = store.record(rid)
    if (
        not settings.get("auto_foreign", True)
        or r["manual"]
        or r["preserve_override"]
        or r["status"] != "empty"
    ):
        return r
    base = settings.get("source_language", "English")
    source_hash = hashlib.sha256(r["source"].encode()).hexdigest()
    with store.db() as db:
        prior = db.execute(
            "SELECT * FROM language_checks WHERE record=?", (rid,)
        ).fetchone()
    if (
        prior
        and prior["source_hash"] == source_hash
        and prior["base_language"] == base
        and json.loads(prior["result"]).get("detector_version") == VERSION
    ):
        return r
    result = {**detect(r["source"], base), "detector_version": VERSION}
    if result["action"] == "preserve":
        r = store.preserve(
            rid, r["revision"], "foreign", result["reason"], worker_job=worker_job
        )
    elif result["action"] == "review" and not r["flag"]:
        r = store.mark(rid, r["revision"], "review", origin="language")
    with store.db() as db:
        db.execute(
            "INSERT OR REPLACE INTO language_checks VALUES (?,?,?,?)",
            (rid, source_hash, base, dump(result)),
        )
    return store.record(rid)


def refresh_language_reviews(store):
    """Refresh old language labels; only proven automatic flags can be removed."""
    with store.db() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT c.*,r.source,r.revision,r.manual,m.kind,m.origin FROM language_checks c JOIN records r ON r.id=c.record LEFT JOIN record_marks m ON m.record=r.id WHERE json_extract(c.result,'$.action')='review'"
            )
        ]
    removed = 0
    cleared_notes = 0
    for row in rows:
        if json.loads(row["result"]).get("detector_version") == VERSION:
            continue
        result = {
            **detect(row["source"], row["base_language"]),
            "detector_version": VERSION,
        }
        if result["action"] == "preserve":
            result["action"] = "review"
            result["reason"] = (
                "Вероятный другой язык: "
                + NAMES.get(result["language"], result["language"].title())
                + ". Существующий перевод оставлен без изменений; проверьте контекст."
            )
        with store.db() as db:
            current = db.execute(
                "SELECT revision,manual FROM records WHERE id=?", (row["record"],)
            ).fetchone()
            if not current or current["revision"] != row["revision"]:
                continue
            db.execute(
                "UPDATE language_checks SET result=? WHERE record=?",
                (dump(result), row["record"]),
            )
            if result["action"] == "none":
                cleared_notes += 1
                if (
                    row["origin"] == "language"
                    and row["kind"] == "review"
                    and not current["manual"]
                ):
                    db.execute(
                        "DELETE FROM record_marks WHERE record=? AND origin='language'",
                        (row["record"],),
                    )
                    db.execute(
                        "UPDATE records SET revision=revision+1 WHERE id=?",
                        (row["record"],),
                    )
                    removed += 1
    return {"removed_auto_marks": removed, "cleared_incorrect_notes": cleared_notes}
