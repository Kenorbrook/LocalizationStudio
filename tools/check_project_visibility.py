"""Finished projects must survive real hidden app startup and restart."""

import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from update_package import reject_links


def main():
    parent = ROOT / "qa-knowledge"
    fixture = parent / ("visibility-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    completed = fixture / "completed"
    completed.mkdir()
    other = fixture / "other"
    other.mkdir()
    project = store.project(str(completed))["id"]
    corpus = completed / "text.json"
    corpus.write_text('[{"source":"Hello."}]')
    store.import_files(project, [corpus])
    store.update(1, 0, "Привет.", "translated", "fixture")
    with store.db() as db:
        # A completed translation run is exactly what used to hide this project.
        db.execute(
            "insert into jobs(project,stage,provider,settings,state,total,done) values (?,'translate','local','{}','done',1,1)",
            (project,),
        )
        before = tuple(
            db.execute("select text,revision from records where id=1").fetchone()
        )
    store.project(str(other))
    script = fixture / "probe.js"
    script.write_text(
        "window.__visibilityProject="
        + str(project)
        + ";\n"
        + (ROOT / "tools/project_visibility_probe.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    results = []
    process = None
    try:
        for attempt in range(2):
            report = fixture / f"probe-{attempt}.json"
            process = subprocess.Popen(
                [
                    str(ROOT / "release/LocalizationStudio/LocalizationStudio.exe"),
                    "--hidden",
                    "--db",
                    str(store.path),
                    "--smoke-report",
                    str(report),
                    "--ui-test-script",
                    str(script),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            deadline = time.monotonic() + 75
            while not (report.exists() and report.stat().st_size):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Visibility probe did not complete")
                time.sleep(0.25)
            result = json.loads(report.read_text(encoding="utf-8"))
            results.append(result)
            assert result.get("passed"), result
            process.terminate()
            process.wait(timeout=20)
            process = None
        with store.db() as db:
            assert tuple(
                db.execute(
                    "select hidden,completed,keep_visible from projects where id=?",
                    (project,),
                ).fetchone()
            ) == (0, 0, 0)
            assert (
                tuple(
                    db.execute(
                        "select text,revision from records where id=1"
                    ).fetchone()
                )
                == before
            )
        result = {
            "passed": True,
            "launches": results,
            "history_and_translation_unchanged": True,
            "no_inference": True,
        }
        (ROOT / "reports/project-visibility-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid fixture path")
        reject_links(fixture)
        for attempt in range(80):
            try:
                shutil.rmtree(fixture)
                break
            except OSError as error:
                if not fixture.exists():
                    break
                if (
                    not isinstance(error, (PermissionError, FileNotFoundError))
                    and getattr(error, "winerror", None) != 145
                ):
                    raise
                if attempt == 79:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
