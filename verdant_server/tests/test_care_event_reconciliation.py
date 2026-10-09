import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "reconcile_care_events_v2.py"
SPEC = importlib.util.spec_from_file_location("reconcile_care_events_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def event(event_id="event-1", plant_id="plant-1", deleted_at=None):
    return {
        "id": event_id,
        "plantID": plant_id,
        "kind": "Annaffia",
        "date": "2026-01-02T00:00:00Z",
        "createdAt": "2026-01-02T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "deletedAt": deleted_at,
        "revision": 1,
        "schemaVersion": 2,
        "origin": "legacy",
        "status": "completed",
        "note": None,
        "health": None,
        "postponedUntil": None,
        "productID": None,
        "treatmentPlanID": None,
    }


class CareEventReconciliationTests(unittest.TestCase):
    def test_clean_snapshot_v1_and_v2(self):
        item = event()
        plant = {
            "id": "plant-1", "name": "Test", "history": [item],
            "lastWatered": "2026-01-02T00:00:00Z", "inspectionInterval": None,
        }
        report = module.reconcile([plant], [plant], {item["id"]: item})
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertEqual(report["summary"]["disabledHealthChecks"], 1)

    def test_reports_orphans_conflicts_and_anchor_mismatch(self):
        expected = event()
        remote = dict(expected, date="2026-01-03T00:00:00Z")
        plant = {
            "id": "plant-1", "name": "Test", "history": [expected],
            "lastWatered": "2026-01-01T00:00:00Z", "inspectionInterval": None,
        }
        orphan = event("orphan", "missing")
        report = module.reconcile([plant], [plant], {"event-1": remote, "orphan": orphan})
        self.assertGreater(report["summary"]["errors"], 0)
        self.assertEqual(report["orphanEventIDs"], ["orphan"])
        self.assertEqual(len(report["anchorMismatches"]), 1)


if __name__ == "__main__":
    unittest.main()
