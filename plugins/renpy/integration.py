"""Generate owned language controls; never rewrite arbitrary game Python."""

import json
import re
import struct
from pathlib import Path

from .filesystem import guard, tree

LANGUAGE = "studio_russian"


def has_cyrillic(font):
    points = [0x401, *range(0x410, 0x450), 0x451]
    data = font.read_bytes()
    if len(data) < 12:
        return False
    tables = {}
    for index in range(struct.unpack_from(">H", data, 4)[0]):
        tag, _, offset, size = struct.unpack_from(">4sIII", data, 12 + index * 16)
        tables[tag] = (offset, size)
    base, _ = tables.get(b"cmap", (0, 0))
    if not base:
        return False
    for index in range(struct.unpack_from(">H", data, base + 2)[0]):
        platform, encoding, offset = struct.unpack_from(
            ">HHI", data, base + 4 + index * 8
        )
        table = base + offset
        if platform not in {0, 3}:
            continue
        fmt = struct.unpack_from(">H", data, table)[0]
        if fmt == 4:
            count = struct.unpack_from(">H", data, table + 6)[0] // 2
            ends = struct.unpack_from(">" + "H" * count, data, table + 14)
            starts = struct.unpack_from(">" + "H" * count, data, table + 16 + count * 2)
            deltas = struct.unpack_from(">" + "h" * count, data, table + 16 + count * 4)
            offsets_base = table + 16 + count * 6
            offsets = struct.unpack_from(">" + "H" * count, data, offsets_base)

            def mapped(point):
                for i, (start, end) in enumerate(zip(starts, ends)):
                    if start <= point <= end:
                        if offsets[i] == 0:
                            return (point + deltas[i]) & 0xFFFF
                        glyph = struct.unpack_from(
                            ">H",
                            data,
                            offsets_base + 2 * i + offsets[i] + 2 * (point - start),
                        )[0]
                        return ((glyph + deltas[i]) & 0xFFFF) if glyph else 0
                return 0

            if all(mapped(point) for point in points):
                return True
        if fmt == 12:
            count = struct.unpack_from(">I", data, table + 12)[0]
            groups = [
                struct.unpack_from(">III", data, table + 16 + i * 12)
                for i in range(count)
            ]
            if all(
                any(
                    a <= point <= b and glyph + point - a != 0 for a, b, glyph in groups
                )
                for point in points
            ):
                return True
    return False


def font_path(root):
    path = guard(Path(root) / "renpy/common/DejaVuSans.ttf")
    if not path.is_file() or not has_cyrillic(path):
        raise ValueError("Не найден штатный шрифт Ren’Py с кириллицей")
    return path


def plan_controls(root, mode, previous=None):
    if mode not in {"menu", "launcher"}:
        raise ValueError("Выберите переключение в меню или перед запуском")
    root = Path(root)
    font = font_path(root)
    assets = guard(root / "game/studio_localization")
    font_target = guard(assets / "DejaVuSans.ttf")
    if font_target.exists() and "game/studio_localization/DejaVuSans.ttf" not in (
        previous or {}
    ):
        raise ValueError("Имя studio_localization занято файлами игры")
    output = {"game/studio_localization/DejaVuSans.ttf": font.read_bytes()}
    common = """# Owned by Localization Studio; applies only to the copied game.
translate studio_russian python:
    for _studio_style_name in ("default", "say_dialogue", "say_label", "button_text", "label_text", "input", "choice_button_text", "text"):
        try:
            getattr(style, _studio_style_name).font = "studio_localization/DejaVuSans.ttf"
        except Exception:
            pass
"""
    common += '\ninit 999 python:\n    config.save_directory = ""\n'
    common += """    import os as _studio_os
    if _studio_os.environ.get("STUDIO_RENPY_LANGUAGE") == "original":
        config.language = None
        _preferences.language = None
    elif _studio_os.environ.get("STUDIO_RENPY_LANGUAGE") == "russian":
        config.language = "studio_russian"
        _preferences.language = "studio_russian"
"""
    if mode == "menu":
        # A permanent reachable overlay bypasses game-specific filtering of language lists.
        common += """
screen _studio_language_selector():
    zorder 10000
    if main_menu or _menu:
        frame:
            xalign 0.99
            yalign 0.01
            vbox:
                textbutton "Русский" action Language("studio_russian") text_font "studio_localization/DejaVuSans.ttf"
                textbutton "Original" action Language(None) text_font "studio_localization/DejaVuSans.ttf"

"""
        # Standard preferences screens can also embed the same owned control.
        patched = []
        for file in tree(root / "game"):
            if (
                file.suffix != ".rpy"
                or "/tl/" in file.as_posix()
                or file.parent == assets
            ):
                continue
            content = file.read_text(encoding="utf-8-sig")
            if file.relative_to(root).as_posix() in (previous or {}):
                content = content.replace("    use _studio_language_selector\n", "")
            match = re.search(
                r"(?m)^screen preferences\s*\([^\n]*\)\s*:\s*(?:#[^\n]*)?\n", content
            )
            if match:
                position = match.end()
                output[file.relative_to(root).as_posix()] = (
                    content[:position]
                    + "    use _studio_language_selector\n"
                    + content[position:]
                ).encode("utf-8")
                patched.append(file.relative_to(root).as_posix())
        common += "\n# Preferences integration: " + json.dumps(patched) + "\n"
        if not patched:
            common += """
init 999 python:
    if "_studio_language_selector" not in config.always_shown_screens:
        config.always_shown_screens.append("_studio_language_selector")
"""
    common += "\n" + (Path(__file__).parent / "verify.rpy").read_text(encoding="utf-8")
    controls = guard(root / "game/zz_studio_localization.rpy")
    if controls.exists() and "game/zz_studio_localization.rpy" not in (previous or {}):
        raise ValueError("Имя zz_studio_localization.rpy занято файлами игры")
    output["game/zz_studio_localization.rpy"] = common.encode("utf-8")
    return output
