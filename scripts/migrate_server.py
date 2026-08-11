#!/usr/bin/env python3
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


COLLECTIONS = ("plants", "fertilizers", "species-profiles", "care-events", "growth-entries")


def request(base, token, method, path, body=None, content_type="application/json"):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return response.status, response.headers.get("Content-Type", "application/octet-stream"), raw


def json_request(base, token, method, path, body=None):
    _, _, raw = request(base, token, method, path, body)
    return json.loads(raw) if raw else {}


def collect_photo_ids(value, result):
    if isinstance(value, dict):
        for nested in value.values():
            collect_photo_ids(nested, result)
    elif isinstance(value, list):
        for nested in value:
            collect_photo_ids(nested, result)
    elif isinstance(value, str) and value.startswith("verdant-server://photos/"):
        result.add(value.rsplit("/", 1)[-1])


def main():
    old_base = os.environ["VERDANT_OLD_BASE"]
    new_base = os.environ["VERDANT_NEW_BASE"]
    old_token = os.environ["VERDANT_OLD_TOKEN"]
    new_token = os.environ["VERDANT_NEW_TOKEN"]
    counts = {}
    photo_ids = set()

    for collection in COLLECTIONS:
        items = json_request(old_base, old_token, "GET", f"/v1/entities/{collection}").get("items", [])
        counts[collection] = len(items)
        for item in items:
            entity_id = item["id"]
            payload = item["payload"]
            json_request(
                new_base,
                new_token,
                "PUT",
                f"/v1/entities/{collection}/{urllib.parse.quote(entity_id, safe='')}",
                {"payload": payload},
            )
            collect_photo_ids(payload, photo_ids)
            if collection in ("plants", "fertilizers"):
                photo_ids.add(entity_id)

    try:
        mappings = json_request(old_base, old_token, "GET", "/v1/sensor-mappings").get("items", [])
        json_request(new_base, new_token, "PUT", "/v1/sensor-mappings", {"items": mappings})
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        mappings = []

    copied_photos = 0
    missing_photos = 0
    for photo_id in sorted(photo_ids):
        path = f"/v1/photos/{urllib.parse.quote(photo_id, safe='')}"
        try:
            _, content_type, data = request(old_base, old_token, "GET", path)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                missing_photos += 1
                continue
            raise
        request(new_base, new_token, "PUT", path, data, content_type.split(";", 1)[0])
        copied_photos += 1

    verified = {}
    for collection, expected in counts.items():
        actual = len(json_request(new_base, new_token, "GET", f"/v1/entities/{collection}").get("items", []))
        verified[collection] = actual
        if actual != expected:
            raise RuntimeError(f"Conteggio non valido per {collection}: {actual} invece di {expected}")

    print(json.dumps({
        "entities": verified,
        "sensorMappings": len(mappings),
        "photosCopied": copied_photos,
        "photoCandidatesWithoutFile": missing_photos,
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Migrazione non riuscita: {error}", file=sys.stderr)
        raise
