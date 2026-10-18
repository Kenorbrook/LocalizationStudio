"""Read the application's own renderer without desktop input or activation."""

import base64
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def discovery_path(database):
    return Path(database).with_suffix(".desktop.json")


class AppInspection:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.session = uuid.uuid4().hex
        self.window = None
        self.port = None
        self.ready = False
        self.lock = threading.Lock()

    def identity(self):
        return {
            "session": self.session,
            "pid": os.getpid(),
            "port": self.port,
            "database": str(self.database),
        }

    def publish(self):
        path = discovery_path(self.database)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.identity()), encoding="utf-8")
        temporary.replace(path)
        self.ready = True

    def dispose(self):
        self.ready = False
        path = discovery_path(self.database)
        try:
            if json.loads(path.read_text(encoding="utf-8"))["session"] == self.session:
                path.unlink()
        except (OSError, ValueError, KeyError):
            pass

    def capture_png(self):
        from System import Action
        from System.IO import MemoryStream
        from Microsoft.Web.WebView2.Core import CoreWebView2CapturePreviewImageFormat
        from webview.platforms.winforms import BrowserView

        form = BrowserView.instances[self.window.uid]
        started = threading.Event()
        pending = {}

        def begin():
            try:
                stream = MemoryStream()
                pending["stream"] = stream
                pending["task"] = form.webview.CoreWebView2.CapturePreviewAsync(
                    CoreWebView2CapturePreviewImageFormat.Png, stream
                )
            except Exception as error:
                pending["error"] = error
            finally:
                started.set()

        form.BeginInvoke(Action(begin))
        if not started.wait(10):
            raise RuntimeError("Интерфейс не ответил на запрос снимка")
        if "error" in pending:
            raise RuntimeError(
                "WebView2 не смог сделать снимок: " + str(pending["error"])
            )
        task = pending["task"]
        deadline = time.monotonic() + 15
        while not task.IsCompleted:
            if time.monotonic() > deadline:
                raise RuntimeError("Истекло время ожидания снимка WebView2")
            time.sleep(0.02)
        stream = pending["stream"]
        try:
            task.GetAwaiter().GetResult()
            return bytes(stream.ToArray())
        finally:
            stream.Dispose()

    def inspect(self, screenshot=False):
        if not self.ready or self.window is None:
            raise RuntimeError("Окно приложения ещё не готово")
        if not self.lock.acquire(timeout=2):
            raise RuntimeError("Другой запрос снимка ещё выполняется; повторите запрос")
        try:
            state = json.loads(
                self.window.evaluate_js("JSON.stringify(studioInspection.snapshot())")
            )
            result = {
                **self.identity(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "surface": "webview2-viewport",
                "ui": state,
            }
            if screenshot:
                if not state.get("screenshot_available"):
                    raise RuntimeError("Скройте открытый секретный ключ перед снимком")
                png = self.capture_png()
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("WebView2 вернул некорректный PNG")
                result["image"] = {
                    "mimeType": "image/png",
                    "data": base64.b64encode(png).decode("ascii"),
                }
                result["captured_at"] = datetime.now(timezone.utc).isoformat()
            return result
        finally:
            self.lock.release()
