import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "migrate_care_events_v2.py"
SPEC = importlib.util.spec_from_file_location("migrate_care_events_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class CareEventMigrationTests(unittest.TestCase):
    def test_normalizes_legacy_swift_dates_and_metadata(self):
        event = module.normalize_event("plant-1", {
            "id": "event-1", "kind": "Annaffia", "date": 0, "status": "completed"
        })
        self.assertEqual(event["date"], "2001-01-01T00:00:00Z")
        self.assertEqual(event["createdAt"], event["date"])
        self.assertEqual(event["updatedAt"], event["date"])
        self.assertEqual(event["schemaVersion"], 2)
        self.assertEqual(event["revision"], 1)
        self.assertEqual(event["origin"], "legacy")

    def test_plan_uploads_only_missing_and_defers_conflicts(self):
        source = {"same": {"value": 1}, "missing": {"value": 2}, "conflict": {"value": 3}}
        existing = {"same": {"value": 1}, "conflict": {"value": 4}, "remote": {"value": 5}}
        plan = module.build_plan(source, existing)
        self.assertEqual(plan.missing_ids, ("missing",))
        self.assertEqual(plan.conflict_ids, ("conflict",))

    def test_rejects_duplicate_event_ids_across_plants(self):
        plants = [
            {"id": "plant-1", "history": [{"id": "event-1", "kind": "Annaffia", "date": 0}]},
            {"id": "plant-2", "history": [{"id": "event-1", "kind": "Annaffia", "date": 0}]},
        ]
        with self.assertRaisesRegex(ValueError, "duplicato"):
            module.events_from_plants(plants)


if __name__ == "__main__":
    unittest.main()
