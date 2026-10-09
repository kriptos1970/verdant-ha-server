from datetime import datetime
from typing import Any


def apply_safe_ai_plan_revision(
    plant: dict[str, Any], proposal: dict[str, Any], now: datetime, provider: Any
) -> None:
    def bounded(name: str, current: int, absolute_min: int, absolute_max: int) -> int:
        proposed = int(proposal.get(name, current))
        delta = max(1, round(current * 0.25))
        return max(absolute_min, min(absolute_max, max(current - delta, min(current + delta, proposed))))

    current_water = int(plant.get("wateringInterval", 7))
    current_feed = int(plant.get("fertilizingInterval") or 28)
    water = bounded("wateringDays", current_water, 1, 120)
    feed = bounded("fertilizingDays", current_feed, 7, 365)
    plant["wateringInterval"] = water
    plant["fertilizingInterval"] = feed

    inspection_enabled = plant.get("inspectionInterval") is not None
    inspection = None
    if inspection_enabled:
        current_inspection = int(plant["inspectionInterval"])
        inspection = bounded("inspectionDays", current_inspection, 1, 30)
        plant["inspectionInterval"] = inspection
    else:
        plant["inspectionInterval"] = None

    plan = plant.get("adaptivePlan")
    if not isinstance(plan, dict):
        raise ValueError("Piano adattivo mancante")
    plan_update = {
        "wateringDays": water,
        "fertilizingDays": feed,
        "revisionSource": "AI",
        "revisionProvider": f"{provider.provider_name} · {provider.model_name}",
        "revisedAt": now.timestamp() - 978307200,
    }
    if inspection is not None:
        plan_update["inspectionDays"] = inspection
    plan.update(plan_update)
    reason = str(proposal.get("reason", "Revisione AI basata sui dati disponibili."))[:300]
    rationale = plan.get("rationale") if isinstance(plan.get("rationale"), list) else []
    plan["rationale"] = [reason] + [item for item in rationale if item != reason][:7]
