"""Exercise file downloads, native install and hidden restart in a disposable EXE copy."""

import base64
import ctypes
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Store
from inspection_client import inspect_app
from update_files import file_hash
from update_package import reject_links
from version import REPOSITORY, VERSION
from prepare_updater_test import fixture_processes, fixture_browser_processes
from updater import wait_for_exit


def wait_until(action, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = action()
        if result:
            return result
        time.sleep(0.25)
    raise RuntimeError("Timed out waiting for the neutral update fixture")


def main():
    parent = ROOT / "qa-update-flow"
    fixture = parent / ("files-" + str(time.time_ns()))
    fixture.mkdir(parents=True)
    assets = ROOT / f"release-assets/v{VERSION}-files"
    checksums = ROOT / f"release-assets/SHA256SUMS-v{VERSION}.txt"
    index = json.loads((assets / "update-files.json").read_text())
    requests = []
    release = {"tag_name": f"v{VERSION}", "assets": []}
    for path, name in [
        *((p, p.name) for p in assets.iterdir() if p.is_file()),
        (checksums, "SHA256SUMS.txt"),
        (
            ROOT / f"release-assets/LocalizationStudio-v{VERSION}-windows-x64.zip",
            f"LocalizationStudio-v{VERSION}-windows-x64.zip",
        ),
    ]:
        release["assets"].append(
            {
                "name": name,
                "size": path.stat().st_size,
                "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/v{VERSION}/{name}",
            }
        )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.removeprefix("/")
            if name == "latest":
                content = json.dumps(release).encode()
            else:
                requests.append(name)
                path = checksums if name == "SHA256SUMS.txt" else assets / name
                if not path.is_file() or Path(name).name != name:
                    self.send_error(404)
                    return
                content = path.read_bytes()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    gui = None
    home = fixture / "release/LocalizationStudio"
    try:
        for name in ["src", "docs", "packaging"]:
            shutil.copytree(
                ROOT / name,
                fixture / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        (fixture / "src/version.py").write_text(
            f'VERSION="0.0.0"\nREPOSITORY="{REPOSITORY}"\n'
        )
        feed = fixture / "src/update_feed.py"
        feed.write_text(
            feed.read_text(encoding="utf-8")
            .replace(
                "def open_remote(url):\n",
                f'def open_remote(url):\n    url = "{address}/" + ("latest" if url == API else url.rsplit("/", 1)[-1])\n',
                1,
            )
            .replace(
                "return urllib.request.urlopen(message, timeout=20)",
                "return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(message, timeout=20)",
            ),
            encoding="utf-8",
        )
        with (ROOT / "reports/file-update-build.log").open(
            "w", encoding="utf-8"
        ) as log:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "PyInstaller",
                    "--noconfirm",
                    "--distpath",
                    str(fixture / "release"),
                    "--workpath",
                    str(fixture / "build"),
                    str(fixture / "packaging/LocalizationStudio.spec"),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        data = home / "data"
        data.mkdir()
        store = Store(data / "studio.sqlite3")
        (data / "desktop_preferences.json").write_text('{"close_choice":"exit"}')
        (data / "sentinel.txt").write_text("preserve user data")
        game = fixture / "neutral-game"
        game.mkdir()
        project = store.project(str(game))["id"]
        corpus = game / "text.json"
        corpus.write_text(
            json.dumps(
                [
                    {"source": "Hello.", "translation": "Привет."},
                    {"source": "Good night.", "translation": ""},
                ]
            ),
            encoding="utf-8",
        )
        store.import_files(project, [corpus])
        job = store.create_job(project, "translate", "local", {})
        with store.db() as db:
            db.execute("UPDATE jobs SET state='paused' WHERE id=?", (job,))
        expected = {
            entry["asset"]
            for name, entry in index["files"].items()
            if not (home / name).is_file() or file_hash(home / name) != entry["sha256"]
        }
        library = index["files"]["_internal/lingua/lingua.cp312-win_amd64.pyd"]["asset"]
        assert (
            library not in expected
        ), "Test baseline must use the identical Lingua binary"
        script = fixture / "probe.js"
        script.write_text(
            """setTimeout(()=>{(async()=>{try{
const until=async phase=>{for(let i=0;i<240;i++){const state=await window.pywebview.api.get_update_state();displayUpdateState(state);if(state.phase==='error')throw Error(state.message);if(state.phase===phase)return state;await new Promise(r=>setTimeout(r,500));}throw Error('timeout');};
openAppSettings();await window.pywebview.api.check_updates();await until('available');await window.pywebview.api.download_update();const state=await until('ready');
window.__featureProbe={passed:state.mode==='files'&&state.reused_files>0,state,transfer:$('updateTransfer').textContent};
await new Promise(r=>setTimeout(r,22000));
await window.pywebview.api.install_update();
}catch(error){window.__featureProbe={passed:false,error:error.message};}})();},0);""",
            encoding="utf-8",
        )
        report = fixture / "probe.json"
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        foreground = user32.GetForegroundWindow()
        gui = subprocess.Popen(
            [
                str(home / "LocalizationStudio.exe"),
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

        def probe_ready():
            if gui.poll() is not None:
                raise RuntimeError("Neutral GUI exited early")
            if report.exists() and report.stat().st_size:
                return json.loads(report.read_text(encoding="utf-8"))

        result = wait_until(probe_ready, 150)
        assert result["passed"], result
        assert (
            set(name for name in requests if name.startswith("file-")) == expected
        ), requests
        assert len([name for name in requests if name.startswith("file-")]) == len(
            expected
        )
        assert library not in requests
        assert not any(name.endswith(".zip") for name in requests)
        capture_error = None
        time.sleep(2)
        try:
            screenshot = inspect_app(store.path, True)
            (ROOT / "reports/file-update-ready.png").write_bytes(
                base64.b64decode(screenshot["image"]["data"])
            )
        except RuntimeError as error:
            capture_error = str(error)
            # A hidden WebView can defer painting; still verify the native install.
            assert inspect_app(store.path)["ui"]
        result_path = wait_until(
            lambda: next((data / "updates").glob("*/result.json"), None), 90
        )
        installed = json.loads(result_path.read_text(encoding="utf-8"))
        assert installed.get("installed"), installed
        gui.wait(timeout=20)
        wait_until(
            lambda: fixture_processes(home)
            and not result_path.with_name("bundle").exists(),
            60,
        )

        def restarted():
            try:
                return inspect_app(store.path)
            except RuntimeError:
                return None

        wait_until(restarted, 45)
        assert (data / "sentinel.txt").read_text() == "preserve user data"
        with store.db() as db:
            assert db.execute("SELECT text FROM records").fetchone()[0] == "Привет."
            assert (
                db.execute("SELECT state FROM jobs WHERE id=?", (job,)).fetchone()[0]
                == "paused"
            )
        for name, entry in index["files"].items():
            assert file_hash(home / name) == entry["sha256"], name
        assert (
            user32.GetForegroundWindow() == foreground
        ), "Test changed foreground window"
        summary = {
            "passed": True,
            "version": VERSION,
            "only_changed_files": True,
            "lingua_not_downloaded": True,
            "downloaded_bytes": result["state"]["downloaded"],
            "reused_bytes": result["state"]["reused_bytes"],
            "downloaded_files": len(expected),
            "native_install_restart": True,
            "records_and_paused_queue_preserved": True,
            "foreground_preserved": True,
            "screenshot": capture_error is None,
            "capture_error": capture_error,
            "ui": result["transfer"],
        }
        (ROOT / "reports/file-update-native-result.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(summary))
    finally:
        server.shutdown()
        server.server_close()
        if gui and gui.poll() is None:
            gui.terminate()
            gui.wait(timeout=20)
        browser_children = fixture_browser_processes(home)
        for pid in fixture_processes(home):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=True,
            )
        for pid in browser_children:
            wait_for_exit(pid, 25)
        if fixture.resolve().parent != parent.resolve():
            raise RuntimeError("Unexpected fixture path")
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
