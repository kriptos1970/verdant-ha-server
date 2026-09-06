import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from database import VerdantDatabase, VersionConflict
from photo_storage import PhotoStorage
from home_assistant import normalize_exposed_sensors
from settings import Settings


class VerdantDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = VerdantDatabase(self.root / "verdant.sqlite3")

    def tearDown(self):
        self.database.close()
        self.temporary_directory.cleanup()

    def test_upsert_sync_and_delete(self):
        created = self.database.upsert("plants", "plant-1", {"name": "Zamia"}, None)
        self.assertEqual(created.version, 1)
        self.assertEqual(len(self.database.list_entities("plants")), 1)

        updated = self.database.upsert("plants", "plant-1", {"name": "Zamia adulta"}, 1)
        self.assertEqual(updated.version, 2)

        deleted = self.database.delete("plants", "plant-1", 2)
        self.assertTrue(deleted.deleted)
        self.assertEqual(self.database.list_entities("plants"), [])
        self.assertEqual([change.version for change in self.database.changes_since(0)], [1, 2, 3])

    def test_rejects_stale_version(self):
        self.database.upsert("fertilizers", "product-1", {"name": "Concime"}, None)
        with self.assertRaises(VersionConflict):
            self.database.upsert("fertilizers", "product-1", {"name": "Altro"}, 0)

    def test_species_profiles_are_supported(self):
        created = self.database.upsert(
            "species-profiles",
            "species-1",
            {"canonicalScientificName": "Monstera deliciosa"},
            None,
        )

        self.assertEqual(created.collection, "species-profiles")
        self.assertEqual(
            self.database.list_entities("species-profiles")[0].payload["canonicalScientificName"],
            "Monstera deliciosa",
        )

    def test_species_profile_round_trips_ecology_classification_and_evidence(self):
        payload = {
            "canonicalScientificName": "Monstera deliciosa",
            "ecology": {
                "climate": "tropical",
                "naturalSunExposure": "filteredShade",
                "evidence": [
                    {
                        "source": "botanicalProvider",
                        "sourceName": "OpenPlantbook",
                        "sourceURL": "https://open.plantbook.io/example",
                        "summary": "bright indirect light",
                    }
                ],
            },
            "lightClassification": {
                "profileID": "TROPICAL_FILTERED_LIGHT",
                "confidence": 0.93,
                "source": "curatedSpecies",
            },
        }
        self.database.upsert("species-profiles", "species-complete", payload, None)
        restored = self.database.list_entities("species-profiles")[0].payload

        self.assertEqual(restored, payload)
        self.assertEqual(
            restored["ecology"]["evidence"][0]["sourceName"],
            "OpenPlantbook",
        )

    def test_measurements_are_supported(self):
        created = self.database.upsert(
            "measurements",
            "measurement-1",
            {"value": 21.5, "kind": "temperature"},
            None,
        )

        self.assertEqual(created.collection, "measurements")
        self.assertEqual(self.database.list_entities("measurements")[0].payload["value"], 21.5)

    def test_lists_all_plants_for_server_side_care_evaluation(self):
        self.database.upsert("plants", "plant-without-sensors", {"name": "Calathea"}, None)
        entities = self.database.list_entities("plants")
        self.assertEqual([entity.entity_id for entity in entities], ["plant-without-sensors"])

    def test_photo_storage_validates_and_replaces_files(self):
        storage = PhotoStorage(self.root / "photos", 1024)
        first = storage.save("photo-1", "image/png", b"first")
        second = storage.save("photo-1", "image/jpeg", b"second")

        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        self.assertEqual(storage.find("photo-1").size, 6)
        self.assertEqual(storage.find("photo-1").checksum, hashlib.sha256(b"second").hexdigest())

    def test_photo_storage_delete_is_idempotent(self):
        storage = PhotoStorage(self.root / "photos", 1024)
        storage.save("photo-1", "image/jpeg", b"photo")

        self.assertTrue(storage.delete("photo-1"))
        self.assertIsNone(storage.find("photo-1"))
        self.assertFalse(storage.delete("photo-1"))

    def test_server_state_round_trip(self):
        mappings = [{"entityID": "sensor.balcone", "room": "Balcone", "plantID": None, "kind": "temperature"}]
        self.database.set_state("sensor-mappings", mappings)
        self.assertEqual(self.database.get_state("sensor-mappings", []), mappings)

    def test_migrates_legacy_database_with_backup_and_preserves_entities(self):
        self.database.close()
        legacy_path = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        with connection:
            connection.executescript(
                """
                CREATE TABLE entities (
                    collection TEXT NOT NULL, entity_id TEXT NOT NULL,
                    payload TEXT NOT NULL, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (collection, entity_id)
                );
                CREATE TABLE changes (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL, entity_id TEXT NOT NULL,
                    payload TEXT NOT NULL, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, deleted INTEGER NOT NULL
                );
                INSERT INTO entities VALUES (
                    'plants', 'legacy-plant', '{"name":"Monstera"}', 7,
                    '2026-08-12T10:00:00+00:00', 0
                );
                """
            )
        connection.close()

        migrated = VerdantDatabase(legacy_path)
        try:
            plants = migrated.list_entities("plants")
            self.assertEqual(len(plants), 1)
            self.assertEqual(plants[0].entity_id, "legacy-plant")
            self.assertEqual(plants[0].version, 7)
            self.assertEqual(plants[0].payload["name"], "Monstera")
            self.assertEqual(
                migrated._connection.execute("PRAGMA user_version").fetchone()[0],
                VerdantDatabase.SCHEMA_VERSION,
            )
            self.assertIsNotNone(
                migrated._connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='measurements_log'"
                ).fetchone()
            )
        finally:
            migrated.close()

        backup_path = legacy_path.with_name("legacy.sqlite3.pre-0.4.0.bak")
        self.assertTrue(backup_path.is_file())
        backup = sqlite3.connect(backup_path)
        try:
            row = backup.execute(
                "SELECT entity_id, version FROM entities WHERE collection='plants'"
            ).fetchone()
            self.assertEqual(row, ("legacy-plant", 7))
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
        finally:
            backup.close()

    def test_only_allowlisted_supported_numeric_sensors_are_exposed(self):
        states = [
            {"entity_id": "sensor.balcone_temperature", "state": "21.4", "last_updated": "2026-08-11T10:00:00Z",
             "attributes": {"friendly_name": "Balcone", "device_class": "temperature", "unit_of_measurement": "°C"}},
            {"entity_id": "sensor.private_motion", "state": "on", "attributes": {"device_class": "motion"}},
            {"entity_id": "sensor.hidden_humidity", "state": "55", "attributes": {"device_class": "humidity"}},
        ]
        result = normalize_exposed_sensors(states, frozenset({"sensor.balcone_temperature", "sensor.private_motion"}))
        self.assertEqual([item["entityID"] for item in result], ["sensor.balcone_temperature"])
        self.assertEqual(result[0]["value"], 21.4)

    def test_settings_prefer_supervisor_options_for_exposed_entities(self):
        options = self.root / "options.json"
        options.write_text(json.dumps({"exposed_entities": ["sensor.balcone_temperature", "sensor.dracena_moisture"]}))
        environment = {
            "VERDANT_TOKEN": "test-token",
            "VERDANT_DATA_DIR": str(self.root),
            "VERDANT_EXPOSED_ENTITIES": "[]",
        }
        with patch.dict("os.environ", environment, clear=True):
            settings = Settings.from_environment()
        self.assertEqual(settings.exposed_entities, frozenset({"sensor.balcone_temperature", "sensor.dracena_moisture"}))


if __name__ == "__main__":
    unittest.main()
