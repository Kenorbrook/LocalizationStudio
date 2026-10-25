"""Verify optional-plugin UX in a hidden EXE; never download inside a UI probe."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from update_package import reject_links


def main():
    parent = ROOT / "qa-knowledge"
    fixture = parent / ("hermes-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    game = fixture / "game"
    game.mkdir()
    project = store.project(str(game))["id"]
    corpus = game / "text.json"
    corpus.write_text(json.dumps([{"source": "A neutral sentence."}]))
    store.import_files(project, [corpus])
    settings = {
        "model": "fixture-agent",
        "source_language": "English",
        "target_language": "Russian",
        "context": 8192,
        "context_before": 4,
        "context_after": 4,
        "max_output": 1200,
    }
    job = store.create_job(project, "translate", "local", settings)
    store.error(
        job, 1, "Локальная модель: не хватило оперативной памяти или видеопамяти"
    )
    with store.db() as db:
        db.execute("UPDATE jobs SET state='incomplete' WHERE id=?", (job,))
    report = fixture / "probe.json"
    gui = subprocess.Popen(
        [
            str(ROOT / "release/LocalizationStudio/LocalizationStudio.exe"),
            "--hidden",
            "--db",
            str(store.path),
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(ROOT / "tools/hermes_ui_probe.js"),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 75
        while (
            not report.exists() or not report.stat().st_size
        ) and time.monotonic() < deadline:
            if gui.poll() is not None:
                raise RuntimeError("Neutral Hermes UI exited early")
            time.sleep(0.25)
        result = json.loads(report.read_text(encoding="utf-8"))
        (ROOT / "reports/hermes-ui-result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        assert result["passed"], result
    finally:
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid fixture path")
        reject_links(fixture)
        for attempt in range(80):
            try:
                shutil.rmtree(fixture)
                break
            except (PermissionError, FileNotFoundError):
                if not fixture.exists():
                    break
                if attempt == 79:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
