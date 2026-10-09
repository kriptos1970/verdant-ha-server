#!/usr/bin/env python3
"""Audit the v1 plant view and the v2 care-event ledger after migration.

This command is deliberately read-only.  It compares a canonical SwiftData
snapshot, the server's v1 plant payloads, and the v2 ledger, then reports every
discrepancy without choosing a winner.  Repairs belong to an explicit follow-up
once the conflicting records have been reviewed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from migrate_care_events_v2 import (
    events_from_plants,
    plants_from_swiftdata,
    request_json,
    server_events,
)


def server_plant_records(base: str, token: str) -> list[dict[str, Any]]:
    return request_json(base, token, "GET", "/v1/entities/plants").get("items", [])


def event_differences(
    expected: dict[str, dict[str, Any]], actual: dict[str, dict[str, Any]]
) -> dict[str, list[str]]:
    expected_ids = set(expected)
    actual_ids = set(actual)
    return {
        "missing": sorted(expected_ids - actual_ids),
        "unexpected": sorted(actual_ids - expected_ids),
        "conflicting": sorted(
            event_id
            for event_id in expected_ids & actual_ids
            if expected[event_id] != actual[event_id]
        ),
    }


def completed(event: dict[str, Any]) -> bool:
    return event.get("deletedAt") is None and event.get("status") != "postponed"


def anchor_mismatches(plants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for plant in plants:
        events = list(events_from_plants([plant]).values())
        for kind, field in (("Annaffia", "lastWatered"), ("Concima", "lastFertilized")):
            dates = [event["date"] for event in events if event.get("kind") == kind and completed(event)]
            if not dates or plant.get(field) is None:
                continue
            expected = max(dates)
            # Plant dates can still be Swift reference-date numbers in legacy payloads.
            from migrate_care_events_v2 import iso_date

            actual = iso_date(plant[field])
            if actual != expected:
                mismatches.append({
                    "plantID": plant["id"],
                    "plantName": plant.get("name"),
                    "field": field,
                    "plantValue": actual,
                    "ledgerValue": expected,
                })
    return mismatches


def reconcile(
    snapshot_plants: list[dict[str, Any]],
    remote_plants: list[dict[str, Any]],
    ledger: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    snapshot_ids = {plant["id"] for plant in snapshot_plants}
    remote_ids = {plant["id"] for plant in remote_plants}
    snapshot_events = events_from_plants(snapshot_plants)
    remote_events = events_from_plants(remote_plants)
    active_ledger = {
        event_id: event for event_id, event in ledger.items() if event.get("deletedAt") is None
    }
    orphan_ids = sorted(
        event_id for event_id, event in ledger.items() if event.get("plantID") not in remote_ids
    )
    disabled_inspections = sorted(
        plant["id"] for plant in remote_plants if plant.get("inspectionInterval") is None
    )
    counts = Counter(event["plantID"] for event in ledger.values())
    snapshot_diff = event_differences(snapshot_events, ledger)
    active_diff = event_differences(remote_events, active_ledger)
    anchors = anchor_mismatches(remote_plants)
    errors = sum(len(values) for values in snapshot_diff.values())
    errors += sum(len(values) for values in active_diff.values()) + len(orphan_ids) + len(anchors)
    errors += len(snapshot_ids - remote_ids) + len(remote_ids - snapshot_ids)
    return {
        "summary": {
            "snapshotPlants": len(snapshot_plants),
            "serverPlants": len(remote_plants),
            "snapshotEvents": len(snapshot_events),
            "serverV1ActiveEvents": len(remote_events),
            "serverV2Events": len(ledger),
            "serverV2ActiveEvents": len(active_ledger),
            "serverV2Tombstones": len(ledger) - len(active_ledger),
            "disabledHealthChecks": len(disabled_inspections),
            "errors": errors,
        },
        "plants": {
            "missingOnServer": sorted(snapshot_ids - remote_ids),
            "unexpectedOnServer": sorted(remote_ids - snapshot_ids),
            "disabledHealthCheckIDs": disabled_inspections,
            "eventCounts": dict(sorted(counts.items())),
        },
        "snapshotVsV2": snapshot_diff,
        "serverV1VsV2Active": active_diff,
        "orphanEventIDs": orphan_ids,
        "anchorMismatches": anchors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("VERDANT_BASE"))
    parser.add_argument("--token", default=os.environ.get("VERDANT_TOKEN"))
    parser.add_argument("--swiftdata-store", type=Path, required=True)
    parser.add_argument("--apply-v1-view", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.base or not args.token:
        raise ValueError("Imposta --base/--token oppure VERDANT_BASE/VERDANT_TOKEN")
    if args.apply_v1_view and (not args.backup_dir or not args.backup_dir.is_dir()):
        raise ValueError("--apply-v1-view richiede una --backup-dir già esistente")
    snapshot_plants = plants_from_swiftdata(args.swiftdata_store)
    records = server_plant_records(args.base, args.token)
    remote_plants = [record["payload"] for record in records]
    ledger = server_events(args.base, args.token)
    report = reconcile(snapshot_plants, remote_plants, ledger)
    if args.apply_v1_view and report["summary"]["errors"]:
        snapshot_diff = report["snapshotVsV2"]
        active_diff = report["serverV1VsV2Active"]
        repairable = (
            not any(snapshot_diff.values())
            and not active_diff["missing"]
            and not active_diff["conflicting"]
            and not report["orphanEventIDs"]
            and not report["anchorMismatches"]
            and not report["plants"]["missingOnServer"]
            and not report["plants"]["unexpectedOnServer"]
        )
        if not repairable:
            raise RuntimeError("Riconciliazione automatica rifiutata: sono presenti conflitti non additivi")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = args.backup_dir / f"plants-v1-before-reconciliation-{stamp}.json"
        backup.write_text(json.dumps({"items": records}, ensure_ascii=False, indent=2) + "\n")
        missing_ids = set(active_diff["unexpected"])
        for record in records:
            payload = dict(record["payload"])
            present = {event["id"] for event in payload.get("history", [])}
            # Legacy events do not carry plantID; use snapshot ownership instead.
            additions = []
            for plant in snapshot_plants:
                if plant["id"] != payload["id"]:
                    continue
                additions = [event for event in plant.get("history", [])
                             if event["id"] in missing_ids and event["id"] not in present]
            if not additions:
                continue
            payload["history"] = payload.get("history", []) + additions
            quoted = urllib.parse.quote(payload["id"], safe="")
            request_json(args.base, args.token, "PUT", f"/v1/entities/plants/{quoted}", {
                "payload": payload,
                "expectedVersion": record["version"],
            })
        records = server_plant_records(args.base, args.token)
        remote_plants = [record["payload"] for record in records]
        report = reconcile(snapshot_plants, remote_plants, ledger)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
    if report["summary"]["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
