#!/usr/bin/env python3
"""Safely migrate nested Verdant plant history into the v2 event ledger.

The default mode is a read-only dry run. Applying requires an explicit flag and
an existing backup directory. Existing v2 events are never overwritten: payload
differences are reported for the reconciliation phase.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


APPLE_REFERENCE_DATE = datetime(2001, 1, 1, tzinfo=timezone.utc)


def request_json(base: str, token: str, method: str, path: str, body: Any = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (APPLE_REFERENCE_DATE + timedelta(seconds=float(value))).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise ValueError(f"Data non riconosciuta: {value!r}")


def normalize_event(plant_id: str, source: dict[str, Any]) -> dict[str, Any]:
    event = dict(source)
    event["plantID"] = plant_id
    # Match the server's canonical Pydantic representation.  The API emits
    # optional fields explicitly as null, while older Swift payloads omit them.
    # Normalizing both shapes prevents harmless null expansion from being
    # reported as a conflict during verification/reconciliation.
    for key in ("status", "note", "health", "postponedUntil", "productID", "treatmentPlanID"):
        event.setdefault(key, None)
    event["schemaVersion"] = max(2, int(event.get("schemaVersion", 1)))
    event["date"] = iso_date(event["date"])
    event["createdAt"] = iso_date(event.get("createdAt", event["date"]))
    event["updatedAt"] = iso_date(event.get("updatedAt", event["createdAt"]))
    event["deletedAt"] = iso_date(event.get("deletedAt"))
    if "postponedUntil" in event:
        event["postponedUntil"] = iso_date(event.get("postponedUntil"))
    event["revision"] = max(1, int(event.get("revision", 0)))
    event["origin"] = event.get("origin") or "legacy"
    return event


def events_from_plants(plants: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for plant in plants:
        plant_id = plant["id"]
        for raw_event in plant.get("history", []):
            event = normalize_event(plant_id, raw_event)
            event_id = event["id"]
            if event_id in events and events[event_id] != event:
                raise ValueError(f"ID evento duplicato con contenuto diverso: {event_id}")
            events[event_id] = event
    return events


def plants_from_swiftdata(path: Path) -> list[dict[str, Any]]:
    uri = f"file:{path}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute("SELECT ZPAYLOAD FROM ZPLANTRECORD ORDER BY ZORDERINDEX").fetchall()
    return [json.loads(payload) for (payload,) in rows]


def server_plants(base: str, token: str) -> list[dict[str, Any]]:
    items = request_json(base, token, "GET", "/v1/entities/plants").get("items", [])
    return [item["payload"] for item in items]


def server_events(base: str, token: str) -> dict[str, dict[str, Any]]:
    path = "/v2/care-events?includeDeleted=true&limit=5000"
    items = request_json(base, token, "GET", path).get("items", [])
    return {item["id"]: item["payload"] for item in items}


@dataclass(frozen=True)
class MigrationPlan:
    source_count: int
    existing_count: int
    missing_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]

    def as_dict(self, include_ids: bool = False) -> dict[str, Any]:
        result = {
            "sourceEvents": self.source_count,
            "existingV2Events": self.existing_count,
            "eventsToUpload": len(self.missing_ids),
            "conflictsDeferredToStep7": len(self.conflict_ids),
        }
        if include_ids:
            result["missingIDs"] = list(self.missing_ids)
            result["conflictIDs"] = list(self.conflict_ids)
        return result


def build_plan(source: dict[str, dict[str, Any]], existing: dict[str, dict[str, Any]]) -> MigrationPlan:
    missing = tuple(sorted(set(source) - set(existing)))
    conflicts = tuple(sorted(key for key in set(source) & set(existing) if source[key] != existing[key]))
    return MigrationPlan(len(source), len(existing), missing, conflicts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("VERDANT_BASE"))
    parser.add_argument("--token", default=os.environ.get("VERDANT_TOKEN"))
    parser.add_argument("--swiftdata-store", type=Path)
    parser.add_argument("--plan-only", action="store_true", help="Non contatta il server")
    parser.add_argument("--apply", action="store_true", help="Carica soltanto gli eventi mancanti")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--details", action="store_true", help="Include gli ID nel report JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.plan_only and not args.swiftdata_store:
        raise ValueError("--plan-only richiede --swiftdata-store")
    if args.apply and (not args.backup_dir or not args.backup_dir.is_dir()):
        raise ValueError("--apply richiede una --backup-dir già esistente")
    if not args.plan_only and (not args.base or not args.token):
        raise ValueError("Imposta --base/--token oppure VERDANT_BASE/VERDANT_TOKEN")

    plants = plants_from_swiftdata(args.swiftdata_store) if args.swiftdata_store else server_plants(args.base, args.token)
    source = events_from_plants(plants)
    if args.plan_only:
        print(json.dumps(build_plan(source, {}).as_dict(args.details), ensure_ascii=False, indent=2))
        return

    health = request_json(args.base, args.token, "GET", "/health")
    if "care-events-v2" not in health.get("capabilities", []):
        raise RuntimeError(f"Server {health.get('version', 'sconosciuto')} privo di care-events-v2")
    existing = server_events(args.base, args.token)
    plan = build_plan(source, existing)

    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.backup_dir / f"care-events-v2-before-{stamp}.json"
        backup.write_text(json.dumps({"health": health, "items": existing}, ensure_ascii=False, indent=2))
        for event_id in plan.missing_ids:
            quoted = urllib.parse.quote(event_id, safe="")
            request_json(args.base, args.token, "PUT", f"/v2/care-events/{quoted}", source[event_id])
        verified = server_events(args.base, args.token)
        absent = sorted(set(source) - set(verified))
        if absent:
            raise RuntimeError(f"Verifica fallita, eventi assenti: {absent[:10]}")
        repeated = build_plan(source, verified)
        if repeated.missing_ids:
            raise RuntimeError("La seconda pianificazione non è idempotente")

    print(json.dumps(plan.as_dict(args.details), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Migrazione eventi non riuscita: {error}", file=sys.stderr)
        raise
