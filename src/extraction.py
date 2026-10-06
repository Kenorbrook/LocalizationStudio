"""Parse supported text formats into source records and source locators."""

import ast, csv, hashlib, json, re
from serialization import dump
import paths
from localize import source_scene_map

SPAN = re.compile(r'"(?:\\.|[^"\\])*"')


def literal(s):
    # Ren'Py permits unknown escape sequences (e.g. \%). Preserve them.
    try:
        return ast.literal_eval(s)
    except (ValueError, SyntaxError):
        raise ValueError("Не удалось прочитать строковый литерал")


def extract(path, text=None):
    if text is None:
        text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    if suffix == ".rpy":
        lines = text.splitlines(keepends=True)
        records = []
        source = None
        speaker = ""
        scene = "strings"
        old = None
        game = next((p for p in path.parents if p.name == "game"), None)
        native_scenes = source_scene_map(path) if "/tl/" not in path.as_posix() else {}
        for i, line in enumerate(lines):
            s = line.strip()
            match = SPAN.search(line)
            if s.startswith("translate "):
                scene = s.rstrip(":")
                source = old = None
            if s.startswith("label "):
                scene = s.rstrip(":")
            reference = re.match(r"# (game/.+\.rpy):(\d+)", s)
            if reference and game:
                original = (game.parent / reference[1]).resolve()
                if original.is_relative_to(game.resolve()):
                    key = source_scene_map(original).get(int(reference[2]))
                    if key:
                        scene = dump(key)
            if native_scenes:
                scene = dump(native_scenes.get(i + 1, ("start", ())))
            if s.startswith("#") and match:
                prefix = s[1:].strip().split('"', 1)[0].strip()
                if prefix.split(" ", 1)[0] in {"voice", "sound", "music"}:
                    source = None
                    continue
                source = literal(match.group())
                speaker = prefix
                continue
            if s.startswith("old ") and match:
                old = literal(match.group())
                continue
            if s.startswith("new ") and match and old is not None:
                records.append(
                    dict(
                        source=old,
                        text=literal(match.group()),
                        speaker="interface",
                        scene=scene,
                        locator={
                            "line": i,
                            "start": match.start(),
                            "end": match.end(),
                            "line_hash": hashlib.sha256(line.encode()).hexdigest(),
                        },
                    )
                )
                old = None
                continue
            if not match or s.startswith(("#", "translate ", "old ", "new ")):
                continue
            prefix = s.split('"', 1)[0].strip()
            if source is not None and not s.startswith("$"):
                records.append(
                    dict(
                        source=source,
                        text=literal(match.group()),
                        speaker=speaker,
                        scene=scene,
                        locator={
                            "line": i,
                            "start": match.start(),
                            "end": match.end(),
                            "line_hash": hashlib.sha256(line.encode()).hexdigest(),
                        },
                    )
                )
                source = None
            elif (
                not "/tl/" in path.as_posix()
                and (prefix == "" or re.fullmatch(r"[\w]+(?:\s+[\w]+)*", prefix))
                and prefix.split(" ", 1)[0]
                not in {
                    "voice",
                    "sound",
                    "music",
                    "image",
                    "define",
                    "default",
                    "play",
                    "queue",
                    "stop",
                    "show",
                    "scene",
                    "hide",
                    "menu",
                    "jump",
                    "call",
                    "return",
                }
            ):
                records.append(
                    dict(
                        source=literal(match.group()),
                        text="",
                        speaker=prefix,
                        scene=scene,
                        locator={
                            "line": i,
                            "start": match.start(),
                            "end": match.end(),
                            "line_hash": hashlib.sha256(line.encode()).hexdigest(),
                        },
                    )
                )
        return records, text, "renpy"
    if suffix in {".json", ".jsonl"}:
        data = (
            json.loads(text)
            if suffix == ".json"
            else [json.loads(l) for l in text.splitlines() if l.strip()]
        )
        records = []

        def walk(x, loc):
            if isinstance(x, dict) and isinstance(x.get("source"), str):
                records.append(
                    dict(
                        source=x["source"],
                        text=x.get("translation", ""),
                        speaker=str(
                            x.get("speaker")
                            or x.get("speaker_id")
                            or x.get("character")
                            or x.get("actor")
                            or ""
                        ),
                        scene=str(x.get("scene", "")),
                        locator={
                            "path": loc,
                            "field": "translation",
                            "utterance_kind": x.get("utterance_kind")
                            or x.get("line_type"),
                            "preserve_original": x.get("preserve_original") is True,
                            "preserve_reason": x.get("preserve_reason"),
                            "original_language": x.get("original_language"),
                        },
                    )
                )
            elif isinstance(x, dict):
                for k, v in x.items():
                    walk(v, loc + [k])
            elif isinstance(x, list):
                for k, v in enumerate(x):
                    walk(v, loc + [k])
            elif isinstance(x, str):
                records.append(
                    dict(
                        source=x,
                        text="",
                        speaker="",
                        scene="",
                        locator={"path": loc, "replace": True},
                    )
                )

        walk(data, [])
        return records, text, "jsonl" if suffix == ".jsonl" else "json"
    if suffix == ".csv":
        rows = list(csv.DictReader(text.splitlines()))
        if not rows or "source" not in rows[0]:
            raise ValueError(
                "CSV должен содержать столбец source; translation необязателен"
            )
        return (
            [
                dict(
                    source=r["source"],
                    text=r.get("translation", ""),
                    speaker=r.get("speaker")
                    or r.get("speaker_id")
                    or r.get("character")
                    or r.get("actor")
                    or "",
                    scene=r.get("scene", ""),
                    locator={
                        "row": i,
                        "utterance_kind": r.get("utterance_kind") or r.get("line_type"),
                        "preserve_original": str(r.get("preserve_original", "")).lower()
                        in {"true", "1", "yes"},
                        "preserve_reason": r.get("preserve_reason"),
                        "original_language": r.get("original_language"),
                    },
                )
                for i, r in enumerate(rows)
            ],
            text,
            "csv",
        )
    if suffix == ".txt":
        return (
            [
                dict(
                    source=l.strip(), text="", speaker="", scene="", locator={"line": i}
                )
                for i, l in enumerate(text.splitlines())
                if l.strip()
            ],
            text,
            "text",
        )
    raise ValueError("Поддерживаются .rpy, .txt, .json, .jsonl и .csv")
