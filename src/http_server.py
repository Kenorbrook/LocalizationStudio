"""Loopback HTTP transport: authentication, resources and request handling."""

import json, secrets, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from core import UI_ROOT, dump
from read_routes import read_request, UnknownRoute
from commands import dispatch

UI_RESOURCES = {
    "/"
    + path.name: (
        path.name,
        (
            "text/css; charset=utf-8"
            if path.suffix == ".css"
            else "text/javascript; charset=utf-8"
        ),
    )
    for path in UI_ROOT.iterdir()
    if path.suffix in {".js", ".css"}
}


def serve(store, port, open_browser, ready=None):
    token_path = store.path.with_name("server.token")
    if token_path.exists():
        token = token_path.read_text(encoding="ascii").strip()
    else:
        token = secrets.token_urlsafe(32)
        token_path.write_text(token, encoding="ascii")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200, kind="application/json; charset=utf-8"):
            body = data.encode() if isinstance(data, str) else dump(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def guard(self):
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                raise ValueError("Недопустимый адрес сервера")
            origin = self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{port}":
                raise ValueError("Недопустимый источник запроса")

        def do_GET(self):
            try:
                self.guard()
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/":
                    html = (
                        (UI_ROOT / "index.html")
                        .read_text(encoding="utf-8")
                        .replace("__TOKEN__", token)
                    )
                    return self.respond(html, kind="text/html; charset=utf-8")
                resource = UI_RESOURCES.get(url.path)
                if resource:
                    filename, content_type = resource
                    return self.respond(
                        (UI_ROOT / filename).read_text(encoding="utf-8"),
                        kind=content_type,
                    )
                if self.headers.get("X-Studio-Token") != token:
                    return self.respond({"error": "Нужен токен приложения"}, 403)
                try:
                    data = read_request(store, url.path, q, port)
                except UnknownRoute:
                    return self.respond({"error": "Не найдено"}, 404)
                self.respond(data)
            except Exception as e:
                self.respond({"error": str(e)}, 400)

        def do_POST(self):
            try:
                self.guard()
                if self.headers.get("X-Studio-Token") != token:
                    return self.respond({"error": "Нужен токен приложения"}, 403)
                length = int(self.headers.get("Content-Length", 0))
                if length < 0:
                    raise ValueError("Отрицательный размер запроса")
                if length > 20_000_000:
                    raise ValueError("Размер запроса больше 20 МБ")
                data = json.loads(self.rfile.read(length))
                self.respond(dispatch(store, self.path.removeprefix("/api/"), data))
            except Exception as e:
                self.respond({"error": str(e)}, 400)

    # Workers survive closing the app; recover abandoned queue on restart.
    with store.db() as db:
        db.execute(
            "UPDATE jobs SET state='paused',error='Обработчик завершился; продолжите задачу' WHERE state IN ('running','queued') AND heartbeat<?",
            (time.time() - 60,),
        )
        db.execute(
            "UPDATE jobs SET worker_active=0 WHERE heartbeat<?", (time.time() - 60,)
        )
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = server.server_port
    url = f"http://127.0.0.1:{port}/"
    print("Localization Studio: " + url, flush=True)
    if open_browser:
        webbrowser.open(url)
    if ready:
        ready(server)
    server.serve_forever()
