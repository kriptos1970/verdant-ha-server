import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


APP_DIRECTORY = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIRECTORY))

from ai_plan_revision import apply_safe_ai_plan_revision


class AIPlanRevisionTests(unittest.TestCase):
    def setUp(self):
        self.provider = SimpleNamespace(provider_name="test", model_name="test-model")
        self.now = datetime(2026, 10, 9, tzinfo=timezone.utc)

    def test_disabled_health_check_stays_disabled(self):
        plant = {
            "wateringInterval": 7,
            "fertilizingInterval": 28,
            "inspectionInterval": None,
            "adaptivePlan": {"inspectionDays": 7, "rationale": []},
        }
        apply_safe_ai_plan_revision(
            plant,
            {"wateringDays": 8, "fertilizingDays": 30, "inspectionDays": 3},
            self.now,
            self.provider,
        )
        self.assertIsNone(plant["inspectionInterval"])

    def test_enabled_health_check_can_be_safely_tuned(self):
        plant = {
            "wateringInterval": 7,
            "fertilizingInterval": 28,
            "inspectionInterval": 8,
            "adaptivePlan": {"inspectionDays": 8, "rationale": []},
        }
        apply_safe_ai_plan_revision(
            plant,
            {"wateringDays": 7, "fertilizingDays": 28, "inspectionDays": 6},
            self.now,
            self.provider,
        )
        self.assertEqual(plant["inspectionInterval"], 6)
        self.assertEqual(plant["adaptivePlan"]["inspectionDays"], 6)


if __name__ == "__main__":
    unittest.main()
