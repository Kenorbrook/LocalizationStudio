import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cloud_connections import discover, job_key, listing, save
from core import Store
from queries import state


class CloudConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = Store(root / "test.sqlite3")
        self.first = self.store.project(str(root))["id"]
        other = root / "other"
        other.mkdir()
        self.second = self.store.project(str(other))["id"]
        self.keys = {}
        secret_patch = patch("providers.secret", side_effect=self.secret)
        secret_patch.start()
        self.addCleanup(secret_patch.stop)
        self.url = "https://example.com/v1"

    def secret(self, identity, value=None):
        if value is not None:
            self.keys[identity] = value
            return True
        return self.keys.get(identity, "")

    def test_connection_scope_and_endpoint_are_enforced_for_job(self):
        identity = save(
            self.store,
            {
                "name": "Example",
                "endpoint": self.url + "/",
                "key": "test credential",
                "projects": [self.first],
            },
        )
        options = {"cloud_connection": identity, "endpoint": self.url}
        self.assertEqual(
            job_key(self.store.path, self.first, options), "test credential"
        )
        self.assertNotIn("test credential", json.dumps(listing(self.store)))
        with self.assertRaisesRegex(ValueError, "недоступно для проекта"):
            job_key(self.store.path, self.second, options)
        with self.assertRaisesRegex(ValueError, "для этого адреса"):
            job_key(
                self.store.path,
                self.first,
                {**options, "endpoint": "https://other.example"},
            )
        save(
            self.store,
            {"id": identity, "name": "Global", "endpoint": self.url, "projects": None},
        )
        self.assertEqual(
            job_key(self.store.path, self.second, options), "test credential"
        )

    def test_model_discovery_keeps_distinct_ids_and_authenticates(self):
        identity = save(
            self.store, {"name": "Example", "endpoint": self.url, "key": "fixture"}
        )
        response = io.BytesIO(
            b'{"data":[{"id":"text-b"},{"id":"text-a"},{"id":"text-a"}]}'
        )
        opener = Mock()
        opener.open.return_value = response
        with patch(
            "cloud_connections.urllib.request.build_opener", return_value=opener
        ):
            self.assertEqual(discover(self.store, identity), ["text-a", "text-b"])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, self.url + "/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture")
        self.assertEqual(listing(self.store)[0]["models"], ["text-a", "text-b"])

    def test_legacy_credential_and_foreign_project_activity(self):
        self.keys[self.second] = "legacy fixture"
        with self.store.db() as db:
            db.execute(
                "UPDATE projects SET settings=? WHERE id=?",
                (
                    json.dumps({"endpoint": self.url, "cloud_model": "text-a"}),
                    self.second,
                ),
            )
            db.execute(
                "INSERT INTO jobs(project,stage,provider,state,settings,done,total) VALUES (?,'translate','cloud','paused',?,4,9)",
                (self.second, json.dumps({"cloud_model": "text-a"})),
            )
        item = listing(self.store)[0]
        self.assertEqual(item["projects"], [self.second])
        self.assertEqual(
            job_key(
                self.store.path,
                self.second,
                {"cloud_connection": item["id"], "endpoint": self.url},
            ),
            "legacy fixture",
        )
        snapshot = state(self.store, self.first)
        self.assertFalse(snapshot["jobs"])
        self.assertEqual(snapshot["activity"][0]["model"], "text-a")
        self.assertEqual(snapshot["activity"][0]["done"], 4)
