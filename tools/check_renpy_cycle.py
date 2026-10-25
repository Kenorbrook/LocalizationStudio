"""Exercise a real engine on a disposable neutral game, without model inference."""

import argparse
import hashlib
import json
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from core import Store
from update_package import reject_links
from plugins.renpy.controller import RenPyPlugin
from plugins.renpy.filesystem import tree
from ensure_renpy_test_sdk import ensure
from validation import validate


def fixture_game(root, sdk):
    root.mkdir()
    for folder in ["renpy", "lib"]:
        shutil.copytree(sdk / folder, root / folder)
    shutil.copyfile(sdk / "renpy.py", root / "renpy.py")
    game = root / "game"
    game.mkdir()
    (game / "script.rpy").write_text(
        """define config.name = "Neutral Studio test"
define config.version = "1.0"
define config.screen_width = 640
define config.screen_height = 480
define config.save_directory = "original-neutral-studio"

label start:
    "A neutral sentence."
    menu:
        "Stay.":
            pass
        "Leave.":
            pass
    "Goodbye."
    return
""",
        encoding="utf-8",
    )
    (game / "screens.rpy").write_text(
        """screen preferences():
    tag menu
    vbox:
        text _("Preferences")
        textbutton _("Return") action Return()
""",
        encoding="utf-8",
    )


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in tree(root)
    }


def fill_fixture(store, project, translations):
    # Fixture data only: common engine strings remain unchanged; six game/UI phrases are Russian.
    with store.db() as db:
        rows = db.execute(
            "SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",
            (project,),
        ).fetchall()
        for row in rows:
            if row["status"] == "preserved":
                continue
            text = translations.get(row["source"], row["source"])
            validate(row["source"], text)
            db.execute(
                "UPDATE records SET text=?,status='edited',reviewer='local:neutral-fixture',revision=revision+1 WHERE id=?",
                (text, row["id"]),
            )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sdk", type=Path)
    args = parser.parse_args()
    sdk = (args.sdk or ensure()).resolve()
    parent = ROOT / "qa-renpy"
    parent.mkdir(exist_ok=True)
    fixture = parent / uuid.uuid4().hex
    fixture.mkdir()
    plugin = None
    result = {"passed": False, "engine": "8.5.3", "model_requests": 0}
    try:
        source = fixture / "source"
        fixture_game(source, sdk)
        before = hashes(source)
        store = Store(fixture / "data/test.sqlite3")
        store.launch_lock = threading.RLock()
        project = store.project(str(source))["id"]
        plugin = RenPyPlugin(store)

        def wait():
            deadline = time.monotonic() + 180
            while plugin.busy and time.monotonic() < deadline:
                time.sleep(0.1)
            if plugin.busy:
                plugin.cancel()
                plugin.thread.join(20)
                raise RuntimeError("Neutral engine operation timed out")
            state = plugin.read(project)
            if not state or state.get("phase") == "error":
                raise RuntimeError(plugin.status)
            return state

        plugin.prepare(project, str(fixture / "copy"))
        state = wait()
        assert state["prepared"]
        child = state["workspace_project"]
        translations = {
            "A neutral sentence.": "Нейтральная фраза.",
            "Stay.": "Остаться.",
            "Leave.": "Уйти.",
            "Goodbye.": "До свидания.",
            "Preferences": "Настройки",
            "Return": "Назад",
        }
        rows = fill_fixture(store, child, translations)
        assert set(translations).issubset({row["source"] for row in rows})
        result["records"] = len(rows)
        result["neutral_russian_phrases"] = len(translations)
        result["modes"] = {}
        for mode in ["menu", "launcher"]:
            plugin.build(child, mode)
            state = wait()
            assert state["built"]
            result["modes"][mode] = state["verification"]
            assert hashes(source) == before, "Source game changed"
        plugin.rollback(child)
        state = plugin.read(project)
        assert state["built"] and state["mode"] == "menu"
        plugin.build(child, "menu")
        assert wait()["built"]
        # Exercise a second game without the standard preferences screen.
        second = fixture / "without-preferences"
        fixture_game(second, sdk)
        screen = second / "game/screens.rpy"
        screen.write_text(
            screen.read_text(encoding="utf-8").replace(
                "screen preferences():", "screen custom_settings():"
            ),
            encoding="utf-8",
        )
        other = store.project(str(second))["id"]
        plugin.prepare(other, str(fixture / "fallback-copy"))
        plugin.thread.join(180)
        fallback = plugin.read(other)
        assert fallback and fallback.get("prepared"), plugin.status
        fill_fixture(store, fallback["workspace_project"], translations)
        plugin.build(fallback["workspace_project"], "menu")
        plugin.thread.join(180)
        fallback = plugin.read(other)
        assert fallback.get("built"), plugin.status
        result["fallback_menu"] = fallback["verification"]
        result.update(
            passed=True,
            source_unchanged=hashes(source) == before,
            rollback=True,
            original_language=True,
        )
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        if plugin:
            plugin.dispose()
            if plugin.thread:
                plugin.thread.join(20)
                if plugin.thread.is_alive():
                    raise RuntimeError(
                        "Neutral engine thread still running; fixture retained"
                    )
            log = plugin.root / "projects/1/engine.log"
            if log.is_file():
                shutil.copyfile(log, ROOT / "reports/v070-renpy-engine.log")
            for log in (plugin.root / "projects").glob("*/engine.log"):
                shutil.copyfile(
                    log,
                    ROOT / ("reports/v070-renpy-engine-" + log.parent.name + ".log"),
                )
        (ROOT / "reports/v070-renpy-engine.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid test cleanup path")
        reject_links(fixture)
        shutil.rmtree(fixture)


if __name__ == "__main__":
    main()
