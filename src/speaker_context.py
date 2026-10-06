"""Source speaker metadata; project rules and confirmed references only."""

import json
from pathlib import Path

GUIDANCE = """Speaker IDs identify voices, not necessarily character identities or genders. Context records contain source text and speaker metadata. Treat narration, internal thoughts, quoted speech and dialogue separately. An unnamed line is not automatically the protagonist speaking. self_reference_gender controls first-person grammatical agreement; reference_gender controls references to that character, not the addressee. These can differ from gender/appearance. Never infer gender from an ID, name, avatar or voice alone. Use explicit project profiles, source pronouns and same-scene evidence. Confirmed reference examples support consistent wording but do not override the source or scene-specific identity changes. Do not propagate a possibly wrong Russian draft as a gender fact. If evidence conflicts or is insufficient, retain ambiguity or use natural gender-neutral wording; in review return uncertain rather than invent a gender correction. Context is reference only, never a translation target."""


def project_rules(project):
    path = Path(project["root"]) / "translation_tools" / "localization_config.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def speaker_info(record, rules, profiles=None):
    raw = record.get("speaker") or ""
    known = dict(rules.get("speakers", {}))
    for key, profile in (profiles or {}).items():
        known[key] = {**known.get(key, {}), **profile}
    key = (
        raw
        if raw in known
        else raw.split(" ", 1)[0] if raw.split(" ", 1)[0] in known else raw
    )
    profile = known.get(key, {})
    locator = (
        json.loads(record.get("locator") or "{}")
        if isinstance(record.get("locator"), str)
        else record.get("locator", {})
    )
    kind = (
        profile.get("kind")
        or locator.get("utterance_kind")
        or (
            "interface"
            if raw == "interface"
            else (
                "narration_or_thought_unspecified"
                if not raw and record.get("kind") == "renpy"
                else "unknown"
            )
        )
    )
    return {
        "id": key,
        "source_label": raw,
        "kind": kind,
        "profile": profile,
        "has_project_rules": bool(profile),
    }


def neighbor_settings(settings):
    result = {}
    for key, default in [("context_before", 12), ("context_after", 8)]:
        try:
            value = float(settings.get(key, default))
        except (ValueError, TypeError):
            raise ValueError("Число соседних фраз должно быть целым от 0 до 100")
        if not 0 <= value <= 100 or value != int(value):
            raise ValueError("Число соседних фраз должно быть целым от 0 до 100")
        result[key] = int(value)
    return result


def context_entry(store, rid, rules=None, profiles=None, settings=None):
    r = store.record(rid)
    if rules is None:
        with store.db() as db:
            project = dict(
                db.execute(
                    "SELECT * FROM projects WHERE id=?", (r["project"],)
                ).fetchone()
            )
        rules = project_rules(project)
        profiles = json.loads(project["settings"]).get("speaker_profiles", {})
    if settings is None:
        with store.db() as db:
            settings = json.loads(
                db.execute(
                    "SELECT settings FROM projects WHERE id=?", (r["project"],)
                ).fetchone()[0]
            )
    counts = neighbor_settings(settings)
    with store.db() as db:
        # Limit actual same-scene phrases rather than physical offsets across other scenes.
        query = "SELECT r.*,f.kind FROM records r JOIN files f ON f.id=r.file WHERE r.file=? AND r.scene=? AND r.position "
        before = [
            dict(x)
            for x in db.execute(
                query + "<? ORDER BY r.position DESC LIMIT ?",
                (r["file"], r["scene"], r["position"], counts["context_before"]),
            )
        ][::-1]
        after = [
            dict(x)
            for x in db.execute(
                query + ">? ORDER BY r.position LIMIT ?",
                (r["file"], r["scene"], r["position"], counts["context_after"]),
            )
        ]
        current = dict(r)
        current["kind"] = db.execute(
            "SELECT kind FROM files WHERE id=?", (r["file"],)
        ).fetchone()[0]
        neighbors = before + [current] + after
        examples = []
        if r["speaker"]:
            examples = [
                dict(x)
                for x in db.execute(
                    "SELECT r.source,r.text translation,r.speaker FROM records r JOIN files f ON f.id=r.file WHERE f.project=? AND r.speaker=? AND r.id<>? AND r.status='verified' AND r.manual=1 AND NOT EXISTS(SELECT 1 FROM record_marks m WHERE m.record=r.id) ORDER BY r.id DESC LIMIT 4",
                    (r["project"], r["speaker"], rid),
                )
            ]

    def line(x):
        return {
            "id": x["id"],
            "source": x["source"],
            "speaker": speaker_info(x, rules, profiles),
        }

    r["kind"] = next((x["kind"] for x in neighbors if x["id"] == rid), "unknown")
    return {
        "id": rid,
        "translate_symbols": bool(r.get("preserve_override")),
        "english": r["source"],
        "russian": r["text"],
        "speaker": r["speaker"],
        "speaker_metadata": speaker_info(r, rules, profiles),
        "context_before": [line(x) for x in neighbors if x["position"] < r["position"]],
        "context_after": [line(x) for x in neighbors if x["position"] > r["position"]],
        "confirmed_character_examples": examples,
    }
