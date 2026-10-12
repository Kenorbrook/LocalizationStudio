"""Verify the packaged dictionary UI against a disposable neutral corpus."""

import json
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from project_knowledge import validate_facts, save_batch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true")
    args = parser.parse_args()
    fixture = ROOT / "qa-knowledge" / str(time.time_ns())
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    pid = store.project(str(fixture))["id"]
    corpus = fixture / "corpus.json"
    corpus.write_text(
        json.dumps(
            [
                {
                    "source": "I am Alice, the captain of Aurora.",
                    "speaker": "alice",
                    "scene": "bridge",
                },
                {
                    "source": "Alice, is Bob your brother?",
                    "speaker": "bob",
                    "scene": "bridge",
                },
            ]
        ),
        encoding="utf-8",
    )
    store.import_files(pid, [corpus])
    with store.db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM records ORDER BY id")]
    facts = validate_facts(
        {
            "facts": [
                {
                    "kind": "character",
                    "subject": "alice",
                    "attribute": "role",
                    "value": "Капитан Авроры",
                    "mentions": ["Alice", "Aurora"],
                    "confidence": 0.98,
                    "uncertain": False,
                    "reason": "Представляется капитаном",
                    "evidence": [
                        {"id": rows[0]["id"], "quote": "the captain of Aurora"}
                    ],
                },
                {
                    "kind": "relationship",
                    "subject": "alice",
                    "attribute": "bob",
                    "value": "Возможно, родственники",
                    "mentions": ["Alice", "Bob"],
                    "confidence": 0.4,
                    "uncertain": True,
                    "reason": "Вопрос не подтверждает родство. Проверить отношения Алисы и Боба.",
                    "evidence": [
                        {"id": rows[1]["id"], "quote": "is Bob your brother?"}
                    ],
                },
            ]
        },
        rows,
    )
    save_batch(store, pid, "ui-fixture", facts, rows)
    (fixture / "desktop_preferences.json").write_text('{"close_choice":"exit"}')
    report = fixture / "probe.json"
    exe = (
        ROOT / "LocalizationStudio.exe"
        if args.installed
        else ROOT / "release/LocalizationStudio/LocalizationStudio.exe"
    )
    gui = subprocess.Popen(
        [
            str(exe),
            "--db",
            str(store.path),
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(ROOT / "tools/knowledge_ui_probe.js"),
        ]
    )
    try:
        deadline = time.monotonic() + 80
        while not report.exists() and time.monotonic() < deadline:
            if gui.poll() is not None:
                raise RuntimeError("Packaged UI exited before probe")
            time.sleep(0.5)
        result = json.loads(report.read_text(encoding="utf-8"))
        (ROOT / "reports/knowledge-ui-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert result.get("passed"), result
        # Keep the neutral fixture window available for visual inspection.
        print(
            "Visual fixture ready; close this test window after inspection.", flush=True
        )
        gui.wait(timeout=600)
    finally:
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=15)
        if fixture.resolve().parent != (ROOT / "qa-knowledge").resolve():
            raise RuntimeError("Unexpected fixture location")
        shutil.rmtree(fixture)


if __name__ == "__main__":
    main()
