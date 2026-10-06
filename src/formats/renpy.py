"""renpy source adapter."""

import hashlib, re
from serialization import dump
from formats.literals import SPAN, literal
from localize import source_scene_map


def parse(path, text):
    suffix = path.suffix.lower()
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
