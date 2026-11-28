"""Run workspace UI checks in the hidden Windows build with neutral data."""

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
    fixture = parent / ("workspace-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    process = None
    try:
        store = Store(fixture / "test.sqlite3")
        store.project(str(fixture))
        other = fixture / "Second"
        other.mkdir()
        project = store.project(str(other))["id"]
        corpus = other / "text.json"
        corpus.write_text(
            '[{"source":"The door is open."},{"source":"The light is on."}]',
            encoding="utf-8",
        )
        store.import_files(project, [corpus])
        store.update(1, 0, "Дверь открыта.", "translated", "cloud:fixture-model")
        store.update(2, 0, "Свет включён.", "translated", "local:fixture-local")
        with store.db() as db:
            db.execute(
                "INSERT INTO jobs(project,stage,provider,state,settings,done,total,current,error) VALUES (?,'translate','cloud','paused',?,1,2,1,'Fixture pause')",
                (project, json.dumps({"cloud_model": "fixture-model"})),
            )
            db.executemany(
                "INSERT INTO cloud_connections(id,name,endpoint,projects,models) VALUES (?,?,?,?,?)",
                [
                    (identity, identity, "https://example.com/v1", scope, models)
                    for identity, scope, models in [
                        ("fixture-global", "null", '["first-model","second-model"]'),
                        ("fixture-scoped", "[1]", '["scope-model"]'),
                    ]
                ],
            )
        report = fixture / "result.json"
        process = subprocess.Popen(
            [
                str(ROOT / "release/LocalizationStudio/LocalizationStudio.exe"),
                "--hidden",
                "--db",
                str(store.path),
                "--smoke-report",
                str(report),
                "--ui-test-script",
                str(ROOT / "tools/workspace_basics_probe.js"),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 75
        while not (report.exists() and report.stat().st_size):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("No workspace UI report")
            time.sleep(0.25)
        result = json.loads(report.read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False))
        assert result["passed"], result
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Invalid fixture path")
        reject_links(fixture)
        for attempt in range(40):
            try:
                shutil.rmtree(fixture)
                break
            except OSError:
                if attempt == 39:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
