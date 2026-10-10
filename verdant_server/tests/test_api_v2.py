import os
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # The lightweight database-only test environment omits web dependencies.
    TestClient = None


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

_bootstrap_directory = tempfile.TemporaryDirectory()
os.environ.setdefault("VERDANT_TOKEN", "v2-test-token")
os.environ.setdefault("VERDANT_DATA_DIR", _bootstrap_directory.name)

from database import VerdantDatabase

if TestClient is not None:
    import main


@unittest.skipIf(TestClient is None, "FastAPI test dependencies are not installed")
class CareEventV2APITests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = VerdantDatabase(Path(self.temporary_directory.name) / "verdant.sqlite3")
        self.previous_database = main.database
        main.database = self.database
        self.client = TestClient(main.app)
        self.headers = {"Authorization": "Bearer v2-test-token"}

    def tearDown(self):
        main.database = self.previous_database
        self.database.close()
        self.temporary_directory.cleanup()

    @staticmethod
    def event_payload(**overrides):
        payload = {
            "id": "event-1",
            "plantID": "plant-1",
            "kind": "Annaffia",
            "date": "2026-09-19T09:29:00+02:00",
            "status": "completed",
            "schemaVersion": 2,
            "createdAt": "2026-09-19T09:29:00+02:00",
            "updatedAt": "2026-09-19T09:29:00+02:00",
            "deletedAt": None,
            "revision": 1,
            "origin": "user",
        }
        payload.update(overrides)
        return payload

    def test_shared_proposals_preserve_resolution_against_old_device(self):
        url = "/v1/ai-plan-proposals/plant-1"
        self.assertEqual(self.client.put(url, json={"changedAt": 10, "proposal": {"plantID": "plant-1"}}, headers=self.headers).status_code, 200)
        self.client.put(url, json={"changedAt": 20, "proposal": None}, headers=self.headers)
        self.client.put(url, json={"changedAt": 10, "proposal": {"plantID": "plant-1"}}, headers=self.headers)
        items = self.client.get("/v1/ai-plan-proposals", headers=self.headers).json()["items"]
        self.assertIsNone(items["plant-1"]["proposal"])
        self.assertEqual(items["plant-1"]["changedAt"], 20)

    def test_shared_proposals_require_authentication_and_timestamp(self):
        url = "/v1/ai-plan-proposals/plant-1"
        self.assertIn(self.client.get("/v1/ai-plan-proposals").status_code, (401, 403))
        self.assertEqual(self.client.put(url, json={}, headers=self.headers).status_code, 422)

    def test_event_lifecycle_is_idempotent_and_incremental(self):
        payload = self.event_payload()
        first = self.client.put(
            "/v2/care-events/event-1", json=payload, headers=self.headers
        )
        repeated = self.client.put(
            "/v2/care-events/event-1", json=payload, headers=self.headers
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["applied"])
        self.assertFalse(repeated.json()["applied"])

        listed = self.client.get(
            "/v2/care-events", params={"plantID": "plant-1"}, headers=self.headers
        ).json()["items"]
        self.assertEqual([item["id"] for item in listed], ["event-1"])

        deletion = self.client.request(
            "DELETE",
            "/v2/care-events/event-1",
            json={
                "plantID": "plant-1",
                "updatedAt": "2026-09-20T10:00:00+02:00",
                "revision": 2,
                "origin": "user",
            },
            headers=self.headers,
        )
        self.assertEqual(deletion.status_code, 200)
        self.assertTrue(deletion.json()["event"]["deleted"])

        active = self.client.get("/v2/care-events", headers=self.headers).json()["items"]
        all_events = self.client.get(
            "/v2/care-events", params={"includeDeleted": "true"}, headers=self.headers
        ).json()["items"]
        changes = self.client.get(
            "/v2/care-events/changes", params={"since": 0}, headers=self.headers
        ).json()
        self.assertEqual(active, [])
        self.assertEqual(len(all_events), 1)
        self.assertEqual(len(changes["changes"]), 2)
        self.assertEqual(changes["nextSequence"], changes["changes"][-1]["sequence"])

        stale = self.client.put(
            "/v2/care-events/event-1",
            json=self.event_payload(revision=1),
            headers=self.headers,
        )
        self.assertFalse(stale.json()["applied"])
        self.assertTrue(stale.json()["event"]["deleted"])

    def test_rejects_path_payload_id_mismatch(self):
        response = self.client.put(
            "/v2/care-events/another-id",
            json=self.event_payload(),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_requires_authentication(self):
        response = self.client.get("/v2/care-events")
        self.assertEqual(response.status_code, 401)

    def test_health_declares_authoritative_v2_ledger(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["version"], "0.6.2")
        self.assertIn("care-events-v2-authoritative", health["capabilities"])


if __name__ == "__main__":
    unittest.main()
