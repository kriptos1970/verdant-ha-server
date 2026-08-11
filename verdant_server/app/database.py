import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COLLECTIONS = frozenset({
    "plants",
    "fertilizers",
    "species-profiles",
    "care-events",
    "growth-entries",
})


class VersionConflict(Exception):
    pass


@dataclass(frozen=True)
class StoredEntity:
    collection: str
    entity_id: str
    payload: dict[str, Any]
    version: int
    updated_at: str
    deleted: bool
    sequence: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "collection": self.collection,
            "id": self.entity_id,
            "payload": self.payload,
            "version": self.version,
            "updatedAt": self.updated_at,
            "deleted": self.deleted,
            "sequence": self.sequence,
        }


class VerdantDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS entities (
                    collection TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (collection, entity_id)
                );

                CREATE TABLE IF NOT EXISTS changes (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS changes_sequence_idx
                ON changes(sequence);
                """
            )

    def list_entities(self, collection: str) -> list[StoredEntity]:
        self._validate_collection(collection)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT e.*, COALESCE((
                    SELECT MAX(c.sequence) FROM changes c
                    WHERE c.collection = e.collection AND c.entity_id = e.entity_id
                ), 0) AS sequence
                FROM entities e
                WHERE e.collection = ? AND e.deleted = 0
                ORDER BY e.updated_at, e.entity_id
                """,
                (collection,),
            ).fetchall()
        return [self._entity_from_row(row) for row in rows]

    def changes_since(self, sequence: int, limit: int = 500) -> list[StoredEntity]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT collection, entity_id, payload, version, updated_at, deleted, sequence
                FROM changes
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (sequence, limit),
            ).fetchall()
        return [self._entity_from_row(row) for row in rows]

    def upsert(
        self,
        collection: str,
        entity_id: str,
        payload: dict[str, Any],
        expected_version: int | None,
    ) -> StoredEntity:
        self._validate_collection(collection)
        encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        updated_at = datetime.now(timezone.utc).isoformat()

        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT version FROM entities WHERE collection = ? AND entity_id = ?",
                (collection, entity_id),
            ).fetchone()
            current_version = int(current["version"]) if current else 0
            if expected_version is not None and expected_version != current_version:
                raise VersionConflict(f"Versione server: {current_version}")

            version = current_version + 1
            self._connection.execute(
                """
                INSERT INTO entities(collection, entity_id, payload, version, updated_at, deleted)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(collection, entity_id) DO UPDATE SET
                    payload = excluded.payload,
                    version = excluded.version,
                    updated_at = excluded.updated_at,
                    deleted = 0
                """,
                (collection, entity_id, encoded_payload, version, updated_at),
            )
            cursor = self._connection.execute(
                """
                INSERT INTO changes(collection, entity_id, payload, version, updated_at, deleted)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (collection, entity_id, encoded_payload, version, updated_at),
            )
            sequence = int(cursor.lastrowid)

        return StoredEntity(collection, entity_id, payload, version, updated_at, False, sequence)

    def delete(self, collection: str, entity_id: str, expected_version: int | None) -> StoredEntity:
        self._validate_collection(collection)
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT payload, version FROM entities WHERE collection = ? AND entity_id = ?",
                (collection, entity_id),
            ).fetchone()
            current_version = int(current["version"]) if current else 0
            if expected_version is not None and expected_version != current_version:
                raise VersionConflict(f"Versione server: {current_version}")

            payload = json.loads(current["payload"]) if current else {}
            encoded_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            version = current_version + 1
            self._connection.execute(
                """
                INSERT INTO entities(collection, entity_id, payload, version, updated_at, deleted)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(collection, entity_id) DO UPDATE SET
                    version = excluded.version,
                    updated_at = excluded.updated_at,
                    deleted = 1
                """,
                (collection, entity_id, encoded_payload, version, updated_at),
            )
            cursor = self._connection.execute(
                """
                INSERT INTO changes(collection, entity_id, payload, version, updated_at, deleted)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (collection, entity_id, encoded_payload, version, updated_at),
            )
            sequence = int(cursor.lastrowid)

        return StoredEntity(collection, entity_id, payload, version, updated_at, True, sequence)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _validate_collection(collection: str) -> None:
        if collection not in COLLECTIONS:
            raise ValueError("Collezione non supportata")

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> StoredEntity:
        return StoredEntity(
            collection=row["collection"],
            entity_id=row["entity_id"],
            payload=json.loads(row["payload"]),
            version=int(row["version"]),
            updated_at=row["updated_at"],
            deleted=bool(row["deleted"]),
            sequence=int(row["sequence"]),
        )
