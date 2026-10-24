"""Check preservation presets and input stability in a hidden neutral app instance."""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from inspection_client import inspect_app
from update_package import reject_links


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="store_true")
    args = parser.parse_args()
    parent = ROOT / "qa-knowledge"
    fixture = parent / ("preservation-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    project = store.project(str(fixture))["id"]
    corpus = fixture / "text.json"
    corpus.write_text(
        json.dumps(
            [
                {"source": "Keep this original sentence for an authorial effect."},
                {"source": "This other sentence can still be translated."},
            ]
        )
    )
    store.import_files(project, [corpus])
    with store.db() as db:
        first = db.execute("SELECT id,revision FROM records ORDER BY id").fetchone()
    store.mark(first["id"], first["revision"], "review")
    report = fixture / "probe.json"
    command = (
        [sys.executable, str(ROOT / "src/desktop.py")]
        if args.source
        else [str(ROOT / "release/LocalizationStudio/LocalizationStudio.exe")]
    )
    gui = subprocess.Popen(
        [
            *command,
            "--hidden",
            "--db",
            str(store.path),
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(ROOT / "tools/preservation_ui_probe.js"),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 70
        while (
            not report.exists() or not report.stat().st_size
        ) and time.monotonic() < deadline:
            if gui.poll() is not None:
                raise RuntimeError("Neutral UI exited before the probe completed")
            time.sleep(0.25)
        result = json.loads(report.read_text(encoding="utf-8"))
        result["source"] = args.source
        (ROOT / "reports/preservation-ui-result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(result), flush=True)
        assert result["passed"], result
        # A screenshot is optional for hidden renderers; DOM checks above are required.
        try:
            captured = inspect_app(store.path, True)
            (ROOT / "reports/preservation-ui.png").write_bytes(
                base64.b64decode(captured["image"]["data"])
            )
        except RuntimeError as error:
            print(json.dumps({"capture_error": str(error)}), flush=True)
    finally:
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=20)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Unexpected fixture location")
        reject_links(fixture)
        for attempt in range(80):
            try:
                shutil.rmtree(fixture)
                break
            except PermissionError:
                if attempt == 79:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
