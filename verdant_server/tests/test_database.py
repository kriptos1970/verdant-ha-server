import sys
import tempfile
import unittest
from pathlib import Path


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from database import VerdantDatabase, VersionConflict
from photo_storage import PhotoStorage
from home_assistant import normalize_exposed_sensors


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

    def test_photo_storage_validates_and_replaces_files(self):
        storage = PhotoStorage(self.root / "photos", 1024)
        first = storage.save("photo-1", "image/png", b"first")
        second = storage.save("photo-1", "image/jpeg", b"second")

        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        self.assertEqual(storage.find("photo-1").size, 6)

    def test_server_state_round_trip(self):
        mappings = [{"entityID": "sensor.balcone", "room": "Balcone", "plantID": None, "kind": "temperature"}]
        self.database.set_state("sensor-mappings", mappings)
        self.assertEqual(self.database.get_state("sensor-mappings", []), mappings)

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


if __name__ == "__main__":
    unittest.main()
