import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.request import Request, urlopen

import httpx


logger = logging.getLogger("verdant.home_assistant")


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
        """Lettura sincrona dei sensori (compatibilità con le rotte API esistenti)."""
        if not self.token or not self.exposed_entities:
            return []
        request = Request(f"{self.base_url}/states", headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/json"
        })
        with urlopen(request, timeout=10) as response:
            states = json.load(response)
        return normalize_exposed_sensors(states, self.exposed_entities)

    async def sensors_async(self) -> list[dict[str, Any]]:
        """Lettura asincrona dei sensori per lo scheduler."""
        if not self.token or not self.exposed_entities:
            return []
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{self.base_url}/states",
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            )
            response.raise_for_status()
            states = response.json()
        return normalize_exposed_sensors(states, self.exposed_entities)

    async def fetch_entity_state(self, entity_id: str) -> dict[str, Any] | None:
        """Recupera lo stato corrente di una singola entità."""
        if not self.token:
            return None
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.base_url}/states/{entity_id}",
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def fetch_history(
        self,
        entity_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Recupera lo storico di un'entità da Home Assistant (/api/history/period)."""
        if not self.token:
            return []
        if start is None:
            start = datetime.now(timezone.utc) - timedelta(hours=24)
        path = f"{self.base_url}/history/period/{start.isoformat()}"
        params = {"filter_entity_id": entity_id, "minimal_response": "true"}
        if end:
            params["end_time"] = end.isoformat()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
        # L'API restituisce una lista di liste; ogni sotto-lista contiene gli stati dell'entità
        if not data or not isinstance(data, list):
            return []
        return data[0] if data else []

    async def send_persistent_notification(self, title: str, message: str) -> bool:
        """Invia una notifica persistente in Home Assistant."""
        if not self.token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self.base_url}/services/persistent_notification/create",
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                    },
                    json={"title": title, "message": message},
                )
                response.raise_for_status()
            logger.info("Notifica HA inviata: %s", title)
            return True
        except Exception as error:
            logger.warning("Errore invio notifica HA: %s", error)
            return False


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
