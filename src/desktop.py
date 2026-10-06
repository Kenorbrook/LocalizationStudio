"""Windows EXE entry: native WebView2 window, console worker, stdio MCP."""

import argparse, ctypes, hashlib, json, os, sys, threading, time, traceback
from pathlib import Path
from core import APP_HOME, Store, dump
from paths import DEFAULT_DB
from http_server import serve


def main():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--worker", type=int)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--smoke-report")
    parser.add_argument("--ui-test-script")
    args = parser.parse_args()
    store = Store(args.db)
    if args.worker:
        from worker import run

        run(store, args.worker)
        return
    if args.mcp:
        from mcp_server import serve_stdio

        serve_stdio(store)
        return
    # Never start two desktop windows against the same workspace.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    name = (
        "Local\\LocalizationStudio-"
        + hashlib.sha256(str(store.path.resolve()).encode()).hexdigest()[:20]
    )
    mutex = kernel.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:
        user32 = ctypes.WinDLL("user32")
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        handle = user32.FindWindowW(None, "Localization Studio")
        if handle:
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user32.ShowWindow(handle, 9)
            user32.SetForegroundWindow(handle)
        return
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    from foreign_language import refresh_language_reviews

    refresh_language_reviews(store)
    logdir = APP_HOME / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = (logdir / "desktop.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    from close_behavior import CloseController, CloseApi

    close_controller = CloseController(store)
    ready = threading.Event()
    servers = []
    failures = []

    def backend():
        try:
            serve(store, 0, False, lambda server: (servers.append(server), ready.set()))
        except Exception as e:
            failures.append(str(e))
            ready.set()

    thread = threading.Thread(target=backend, daemon=True)
    thread.start()
    if not ready.wait(15) or failures:
        raise RuntimeError("Не удалось открыть рабочую базу: " + str(failures))
    import webview

    server = servers[0]
    url = f"http://127.0.0.1:{server.server_port}/"
    window = webview.create_window(
        "Localization Studio",
        url,
        width=1420,
        height=940,
        min_size=(1000, 650),
        background_color="#11171b",
        js_api=CloseApi(close_controller),
    )
    close_controller.window = window
    window.events.closing += close_controller.on_closing

    def loaded():
        close_controller.initialize()
        if args.ui_test_script:
            window.evaluate_js(Path(args.ui_test_script).read_text(encoding="utf-8"))
            for attempt in range(240):
                result = window.evaluate_js(
                    "JSON.stringify(window.__featureProbe||null)"
                )
                if result and result != "null":
                    Path(args.smoke_report).write_text(result, encoding="utf-8")
                    return
                time.sleep(0.25)
            Path(args.smoke_report).write_text(
                dump({"error": "UI test timeout"}), encoding="utf-8"
            )
            return
        if args.smoke_report:
            window.evaluate_js("showView('text');loadRows()")
            # Product runtime probe; do not invoke model inference or change records.
            for attempt in range(30):
                result = window.evaluate_js(
                    "JSON.stringify({title:document.title,records:document.querySelectorAll('.record').length,project:document.querySelector('#projects').value,ready:document.querySelector('#pageInfo').textContent})"
                )
                report = json.loads(result)
                if report["records"]:
                    break
                time.sleep(0.2)
            report.update(
                frozen=bool(getattr(sys, "frozen", False)),
                executable=sys.executable,
                database=str(store.path),
                engine="WebView2",
                url=url,
            )
            window.evaluate_js(
                "window.__probe={live:document.querySelector('#live').firstChild,files:document.querySelector('#files').firstChild,rows:document.querySelector('.record')};document.querySelector('.right').scrollTop=120;window.__probe.scroll=document.querySelector('.right').scrollTop"
            )
            time.sleep(6)
            report["polling"] = json.loads(
                window.evaluate_js(
                    "JSON.stringify({liveStable:window.__probe.live===document.querySelector('#live').firstChild,filesStable:window.__probe.files===document.querySelector('#files').firstChild,rowsStable:window.__probe.rows===document.querySelector('.record'),scrollStable:window.__probe.scroll===document.querySelector('.right').scrollTop,blockSelectors:document.querySelectorAll('.file.active select').length,logPresent:!!document.querySelector('#jobLog')})"
                )
            )
            Path(args.smoke_report).write_text(dump(report), encoding="utf-8")

    window.events.loaded += loaded
    try:
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=True,
            storage_path=str(APP_HOME / "webview-cache"),
        )
    finally:
        close_controller.dispose()
        server.shutdown()
        server.server_close()
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        folder = APP_HOME / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        error = traceback.format_exc()
        (folder / "startup-error.log").write_text(error, encoding="utf-8")
        if sys.stderr:
            print(error, file=sys.stderr)
        if "--worker" not in sys.argv and "--mcp" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(
                None,
                "Ошибка запуска. Подробности: " + str(folder / "startup-error.log"),
                "Localization Studio",
                0x10,
            )
        sys.exit(1)
