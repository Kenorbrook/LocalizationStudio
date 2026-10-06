import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from core import Store
from http_server import serve


class HttpTransportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name) / "workspace.sqlite3")
        self.ready = threading.Event()
        self.server = None
        self.thread = threading.Thread(
            target=serve, args=(self.store, 0, False, self.started), daemon=True
        )
        self.thread.start()
        self.assertTrue(self.ready.wait(5))
        self.port = self.server.server_port
        self.token = self.store.path.with_name("server.token").read_text()

    def started(self, server):
        self.server = server
        self.ready.set()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.folder.cleanup()

    def request(self, path, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return (
                response.status,
                dict(response.getheaders()),
                response.read().decode(),
            )
        finally:
            connection.close()

    def test_packaged_resources_and_token_injection(self):
        status, headers, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(self.token, html)
        self.assertNotIn("__TOKEN__", html)
        for resource, kind in [
            ("studio.js", "javascript"),
            ("lifecycle.js", "javascript"),
            ("studio.css", "css"),
        ]:
            status, headers, content = self.request("/" + resource)
            self.assertEqual(status, 200)
            self.assertIn(kind, headers["Content-Type"])
            self.assertTrue(content)

    def test_authorization_and_origin_are_still_required(self):
        self.assertEqual(self.request("/api/state")[0], 403)
        headers = {"X-Studio-Token": self.token}
        status, _, content = self.request("/api/state", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["projects"], [])
        self.assertEqual(
            self.request(
                "/api/state", headers={**headers, "Origin": "https://example.com"}
            )[0],
            400,
        )
        self.assertEqual(
            self.request("/api/state", headers={**headers, "Host": "example.com"})[0],
            400,
        )
        self.assertEqual(self.request("/api/missing", headers=headers)[0], 404)
        self.assertEqual(self.request("/api/records", headers=headers)[0], 400)

    def test_negative_body_length_is_rejected_without_waiting(self):
        headers = {"X-Studio-Token": self.token, "Content-Length": "-1"}
        self.assertEqual(self.request("/api/save", "POST", headers=headers)[0], 400)

    def test_query_and_command_use_project_scoped_handlers(self):
        pid = self.store.project(self.folder.name)["id"]
        headers = {"X-Studio-Token": self.token}
        status, _, body = self.request(
            "/api/hide-project", "POST", headers, json.dumps({"project": pid})
        )
        self.assertEqual(status, 200)
        status, _, body = self.request("/api/hidden-projects", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)[0]["id"], pid)
