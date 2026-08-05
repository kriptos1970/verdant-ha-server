import sys
import tempfile
import unittest
from pathlib import Path


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from database import VerdantDatabase, VersionConflict
from photo_storage import PhotoStorage


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

    def test_photo_storage_validates_and_replaces_files(self):
        storage = PhotoStorage(self.root / "photos", 1024)
        first = storage.save("photo-1", "image/png", b"first")
        second = storage.save("photo-1", "image/jpeg", b"second")

        self.assertFalse(first.path.exists())
        self.assertTrue(second.path.exists())
        self.assertEqual(storage.find("photo-1").size, 6)


if __name__ == "__main__":
    unittest.main()

