import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen


SUPPORTED_DEVICE_CLASSES = {
    "temperature": "temperature", "humidity": "humidity", "illuminance": "illuminance",
    "moisture": "moisture", "conductivity": "conductivity",
}


@dataclass(frozen=True)
class HomeAssistantSensorProvider:
    base_url: str
    token: str
    exposed_entities: frozenset[str]

    def sensors(self) -> list[dict[str, Any]]:
        if not self.token or not self.exposed_entities:
            return []
        request = Request(f"{self.base_url}/states", headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json"
        })
        with urlopen(request, timeout=10) as response:
            states = json.load(response)
        return normalize_exposed_sensors(states, self.exposed_entities)


def normalize_exposed_sensors(states: list[dict[str, Any]], exposed_entities: frozenset[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for state in states:
        entity_id = str(state.get("entity_id", ""))
        if entity_id not in exposed_entities:
            continue
        attributes = state.get("attributes") or {}
        kind = SUPPORTED_DEVICE_CLASSES.get(str(attributes.get("device_class", "")).lower())
        if kind is None:
            continue
        try:
            value = float(state.get("state"))
        except (TypeError, ValueError):
            continue
        updated_at = state.get("last_updated") or state.get("last_changed")
        try:
            datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            updated_at = None
        result.append({"entityID": entity_id, "name": attributes.get("friendly_name") or entity_id,
                       "kind": kind, "value": value, "unit": attributes.get("unit_of_measurement"),
                       "updatedAt": updated_at})
    return sorted(result, key=lambda item: (item["name"].lower(), item["entityID"]))
