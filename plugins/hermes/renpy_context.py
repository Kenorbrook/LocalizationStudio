"""Keep branch isolation while including shared ancestors and original menu syntax."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from formats.literals import SPAN, literal
from speaker_context import context_entry as base_context, speaker_info, project_rules
from project_knowledge import select_context


def scene_key(value):
    try:
        key = json.loads(value)
        if (
            isinstance(key, list)
            and len(key) == 2
            and isinstance(key[0], str)
            and isinstance(key[1], list)
            and all(type(i) is int for i in key[1])
        ):
            return key
    except (ValueError, TypeError):
        pass
    return None


def structure(project, file, row):
    """A translation block is not the source script; follow only its checked reference."""
    lines = (file["original"] or "").splitlines()
    line = json.loads(row["locator"] or "{}").get("line")
    if type(line) is not int or not 0 <= line < len(lines):
        return {"status": "no_source_line"}
    if any(
        re.match(r"\s*translate\s+", raw) for raw in lines[max(0, line - 5) : line + 1]
    ):
        reference = next(
            (
                re.fullmatch(r"\s*# (game/.+\.rpy):(\d+)\s*", raw)
                for raw in reversed(lines[max(0, line - 5) : line])
                if re.fullmatch(r"\s*# (game/.+\.rpy):(\d+)\s*", raw)
            ),
            None,
        )
        if reference is None:
            return {"status": "no_original_reference"}
        relative = reference[1]
        path = PurePosixPath(relative)
        if (
            "\\" in relative
            or ":" in relative
            or ".." in path.parts
            or path.is_absolute()
        ):
            return {"status": "unsafe_original_reference"}
        root = Path(project["root"]).resolve()
        target = root / path
        if not target.resolve().is_relative_to(root / "game") or any(
            p.is_symlink() or p.is_junction() for p in [target, *target.parents]
        ):
            return {"status": "unsafe_original_reference"}
        try:
            with target.open("rb") as stream:
                raw_source = stream.read(8_000_001)
            if len(raw_source) > 8_000_000:
                return {"status": "original_too_large"}
            lines = raw_source.decode("utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            return {"status": "original_unavailable", "path": relative}
        line = int(reference[2]) - 1
        origin = {"path": relative, "sha256": hashlib.sha256(raw_source).hexdigest()}
    else:
        origin = {"path": file["path"], "sha256": file["hash"]}
    if not 0 <= line < len(lines):
        return {**origin, "status": "original_line_missing"}
    token = SPAN.search(lines[line])
    try:
        matches = token is not None and literal(token[0]) == row["source"]
    except ValueError:
        matches = False
    if not matches:
        return {**origin, "status": "original_source_changed", "line": line + 1}
    menu_choice = bool(
        re.fullmatch(r'\s*"(?:\\.|[^"\\])*"(?:\s+if\s+.+)?\s*:\s*(?:#.*)?', lines[line])
    )
    excerpt = "\n".join(lines[max(0, line - 6) : line + 7])
    return {
        **origin,
        "status": "matched",
        "line": line + 1,
        "line_kind": "menu_choice" if menu_choice else "dialogue_or_narration",
        "excerpt": excerpt[:8000],
        "truncated": len(excerpt) > 8000,
    }


def context_entry(store, rid, rules=None, profiles=None, settings=None):
    entry = base_context(store, rid, rules, profiles, settings)
    row = store.record(rid)
    with store.db() as db:
        file = dict(
            db.execute("SELECT * FROM files WHERE id=?", (row["file"],)).fetchone()
        )
        if file["kind"] != "renpy":
            return entry
        project = dict(
            db.execute(
                "SELECT * FROM projects WHERE id=?", (row["project"],)
            ).fetchone()
        )
        candidates = [
            dict(r) | {"kind": "renpy"}
            for r in db.execute(
                "SELECT * FROM records WHERE file=? ORDER BY position", (row["file"],)
            )
        ]
    rules = project_rules(project) if rules is None else rules
    stored = json.loads(project["settings"])
    profiles = stored.get("speaker_profiles", {}) if profiles is None else profiles
    settings = stored if settings is None else settings
    current_key = scene_key(row["scene"])
    if current_key:

        def related(record):
            key = scene_key(record["scene"])
            return (
                key is not None
                and key[0] == current_key[0]
                and len(key[1]) <= len(current_key[1])
                and current_key[1][: len(key[1])] == key[1]
            )

        before = [
            r for r in candidates if r["position"] < row["position"] and related(r)
        ][-12:]
        after = [
            r for r in candidates if r["position"] > row["position"] and related(r)
        ][:8]

        def line(record):
            return {
                "id": record["id"],
                "revision": record["revision"],
                "source": record["source"],
                "speaker": speaker_info(record, rules, profiles),
                "relation": (
                    "same_branch"
                    if record["scene"] == row["scene"]
                    else "shared_ancestor"
                ),
            }

        entry["context_before"] = [line(r) for r in before]
        entry["context_after"] = [line(r) for r in after]
        entry["context_scope"] = (
            "Same label and branch, plus shared ancestor statements. Sibling branches and other labels are excluded. Alternative menu choices are not sequential dialogue."
        )
        for key in ("project_memory", "context_questions", "memory_guidance"):
            entry.pop(key, None)
        entry.update(select_context(store, row, before + [row] + after, settings))
    entry["source_structure"] = structure(project, file, row)
    if entry["source_structure"].get("line_kind") == "menu_choice":
        entry["speaker_metadata"]["kind"] = "menu_choice"
        entry["speaker_metadata"][
            "interpretation"
        ] = "Player's selectable action, not spoken imperative dialogue. An infinitive is appropriate."
    entry["context_summary"] = {
        "before": len(entry["context_before"]),
        "after": len(entry["context_after"]),
        "line_kind": entry["speaker_metadata"]["kind"],
        "original_status": entry["source_structure"]["status"],
    }
    return entry
