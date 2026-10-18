"""Check fresh renderer capture and MCP/CLI without activating a test window."""

import argparse
import base64
import ctypes
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
    fixture = ROOT / "qa-knowledge" / ("inspection-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    store = Store(fixture / "test.sqlite3")
    project = store.project(str(fixture))["id"]
    corpus = fixture / "corpus.json"
    corpus.write_text(
        json.dumps([{"source": "Alice is the captain of Aurora.", "speaker": "alice"}]),
        encoding="utf-8",
    )
    store.import_files(project, [corpus])
    report = fixture / "probe.json"
    bundle = ROOT / "release/LocalizationStudio"
    gui_command = (
        [sys.executable, str(ROOT / "src/desktop.py")]
        if args.source
        else [str(bundle / "LocalizationStudio.exe")]
    )
    console_command = (
        [sys.executable, str(ROOT / "src/desktop.py")]
        if args.source
        else [str(bundle / "LocalizationWorker.exe")]
    )
    user32 = ctypes.WinDLL("user32")
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    foreground = user32.GetForegroundWindow()
    gui = subprocess.Popen(
        [
            *gui_command,
            "--db",
            str(store.path),
            "--hidden",
            "--smoke-report",
            str(report),
            "--ui-test-script",
            str(ROOT / "tools/inspection_ui_probe.js"),
        ]
    )
    checks = {}
    try:
        deadline = time.monotonic() + 70
        while not report.exists() and time.monotonic() < deadline:
            if gui.poll() is not None:
                raise RuntimeError("Test GUI exited")
            time.sleep(0.25)
        assert json.loads(report.read_text())["passed"]
        checks["hidden_launch_preserved_foreground"] = (
            user32.GetForegroundWindow() == foreground
        )
        state = inspect_app(store.path)
        checks["live_state"] = (
            state["ui"]["project"] == project and state["ui"]["view"] == "text"
        )
        first = inspect_app(store.path, True)
        time.sleep(1.2)
        second = inspect_app(store.path, True)
        for label, result in [("first", first), ("second", second)]:
            png = base64.b64decode(result["image"]["data"])
            assert png.startswith(b"\x89PNG\r\n\x1a\n")
            (ROOT / "reports" / ("inspection-" + label + ".png")).write_bytes(png)
        checks["fresh_images"] = first["image"]["data"] != second["image"]["data"]
        checks["fresh_state"] = first["ui"]["title"] != second["ui"]["title"]
        cli = subprocess.run(
            [
                *console_command,
                "--db",
                str(store.path),
                "--inspect",
                "screenshot",
                "--output",
                str(fixture / "cli.png"),
            ],
            capture_output=True,
            timeout=45,
            encoding="utf-8",
        )
        checks["cli_png"] = cli.returncode == 0 and (fixture / "cli.png").exists()
        if cli.returncode:
            raise RuntimeError(cli.stderr)
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "app_ui_state", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "app_screenshot", "arguments": {}},
            },
        ]
        mcp = subprocess.run(
            [*console_command, "--db", str(store.path), "--mcp"],
            input="".join(json.dumps(r) + "\n" for r in requests),
            capture_output=True,
            timeout=45,
            encoding="utf-8",
        )
        responses = [json.loads(line) for line in mcp.stdout.splitlines()]
        checks["mcp_tools"] = {"app_ui_state", "app_screenshot"} <= {
            t["name"] for t in responses[0]["result"]["tools"]
        }
        checks["mcp_state"] = not responses[1]["result"].get("isError")
        checks["mcp_image"] = responses[2]["result"]["content"][-1]["type"] == "image"
        checks["inspection_preserved_foreground"] = (
            user32.GetForegroundWindow() == foreground
        )
        with store.db() as db:
            checks["records_unchanged"] = (
                db.execute(
                    "SELECT count(*) FROM records WHERE text<>'' OR revision<>0"
                ).fetchone()[0]
                == 0
            )
            checks["no_jobs_started"] = (
                db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
            )
        result = {
            "passed": all(checks.values()),
            "checks": checks,
            "source": args.source,
            "surface": "hidden WebView2",
            "pid": gui.pid,
        }
        (ROOT / "reports/inspection-ui-result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result))
        assert result["passed"], checks
    finally:
        if gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=15)
        if (
            fixture.resolve().parent != (ROOT / "qa-knowledge").resolve()
            or fixture.is_symlink()
        ):
            raise RuntimeError("Unexpected fixture location")
        reject_links(fixture)
        # WebView2 children can release their cache shortly after the GUI exits.
        for attempt in range(60):
            try:
                shutil.rmtree(fixture)
                break
            except PermissionError:
                if attempt == 59:
                    raise
                time.sleep(0.25)


if __name__ == "__main__":
    main()
