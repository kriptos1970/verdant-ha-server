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
    "measurements",
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
    SCHEMA_VERSION = 4

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        database_existed = path.is_file()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if database_existed:
            self._backup_before_migration(path)
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

                CREATE TABLE IF NOT EXISTS server_state (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS measurements_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL,
                    plant_id TEXT,
                    kind TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    measured_at TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'home_assistant',
                    reliability REAL NOT NULL DEFAULT 0.85,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_misurazioni_entita_tempo
                ON measurements_log(entity_id, measured_at);

                CREATE INDEX IF NOT EXISTS idx_misurazioni_pianta_tipo_tempo
                ON measurements_log(plant_id, kind, measured_at);
                """
            )
            self._connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    def _backup_before_migration(self, path: Path) -> None:
        """Crea una copia SQLite consistente prima del primo avvio 0.4.0."""
        current_version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        backup_path = path.with_name(f"{path.name}.pre-0.4.0.bak")
        if current_version >= self.SCHEMA_VERSION or backup_path.exists():
            return
        backup_connection = sqlite3.connect(backup_path)
        try:
            self._connection.backup(backup_connection)
        finally:
            backup_connection.close()

    # ── Stato del server ──────────────────────────────────────

    def get_state(self, key: str, default: Any) -> Any:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM server_state WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["payload"]) if row else default

    def set_state(self, key: str, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO server_state(key, payload, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (key, encoded, updated_at),
            )

    # ── Entità sincronizzate ──────────────────────────────────

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

    def get_entity(self, collection: str, entity_id: str) -> StoredEntity | None:
        self._validate_collection(collection)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT e.*, COALESCE((
                    SELECT MAX(c.sequence) FROM changes c
                    WHERE c.collection = e.collection AND c.entity_id = e.entity_id
                ), 0) AS sequence
                FROM entities e
                WHERE e.collection = ? AND e.entity_id = ? AND e.deleted = 0
                """,
                (collection, entity_id),
            ).fetchone()
        return self._entity_from_row(row) if row else None

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

    # ── Misurazioni sensori (serie temporale) ─────────────────

    def insert_measurement(
        self,
        entity_id: str,
        kind: str,
        value: float,
        measured_at: str,
        plant_id: str | None = None,
        unit: str | None = None,
        source: str = "home_assistant",
        reliability: float = 0.85,
    ) -> bool:
        """Inserisce una misurazione. Restituisce False se è un duplicato (stessa entità e timestamp entro 60s)."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            # Deduplicazione: controlla se esiste una lettura recente per la stessa entità
            existing = self._connection.execute(
                """
                SELECT id FROM measurements_log
                WHERE entity_id = ? AND kind = ?
                  AND ABS(julianday(measured_at) - julianday(?)) * 86400 < 60
                LIMIT 1
                """,
                (entity_id, kind, measured_at),
            ).fetchone()
            if existing:
                return False
            self._connection.execute(
                """
                INSERT INTO measurements_log(entity_id, plant_id, kind, value, unit,
                                             measured_at, source, reliability, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (entity_id, plant_id, kind, value, unit, measured_at, source, reliability, created_at),
            )
        return True

    def get_measurements(
        self, entity_id: str, since: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Restituisce la serie temporale per un'entità sensore."""
        with self._lock:
            if since:
                rows = self._connection.execute(
                    """
                    SELECT * FROM measurements_log
                    WHERE entity_id = ? AND measured_at >= ?
                    ORDER BY measured_at DESC LIMIT ?
                    """,
                    (entity_id, since, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM measurements_log
                    WHERE entity_id = ?
                    ORDER BY measured_at DESC LIMIT ?
                    """,
                    (entity_id, limit),
                ).fetchall()
        return [self._measurement_from_row(row) for row in rows]

    def get_plant_measurements(
        self, plant_id: str, kind: str | None = None, since: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Restituisce le misurazioni per una pianta, opzionalmente filtrate per tipo."""
        conditions = ["plant_id = ?"]
        params: list[Any] = [plant_id]
        if kind:
            conditions.append("kind = ?")
            params.append(kind)
        if since:
            conditions.append("measured_at >= ?")
            params.append(since)
        where = " AND ".join(conditions)
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM measurements_log WHERE {where} ORDER BY measured_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._measurement_from_row(row) for row in rows]

    def get_latest_measurements(self, entity_ids: list[str] | None = None) -> list[dict[str, Any]]:
        """Restituisce l'ultima misurazione per ogni entità (o per le entità specificate)."""
        with self._lock:
            if entity_ids:
                placeholders = ",".join("?" for _ in entity_ids)
                rows = self._connection.execute(
                    f"""
                    SELECT m.* FROM measurements_log m
                    INNER JOIN (
                        SELECT entity_id, kind, MAX(measured_at) AS max_time
                        FROM measurements_log
                        WHERE entity_id IN ({placeholders})
                        GROUP BY entity_id, kind
                    ) latest ON m.entity_id = latest.entity_id
                              AND m.kind = latest.kind
                              AND m.measured_at = latest.max_time
                    ORDER BY m.entity_id, m.kind
                    """,
                    entity_ids,
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT m.* FROM measurements_log m
                    INNER JOIN (
                        SELECT entity_id, kind, MAX(measured_at) AS max_time
                        FROM measurements_log
                        GROUP BY entity_id, kind
                    ) latest ON m.entity_id = latest.entity_id
                              AND m.kind = latest.kind
                              AND m.measured_at = latest.max_time
                    ORDER BY m.entity_id, m.kind
                    """,
                ).fetchall()
        return [self._measurement_from_row(row) for row in rows]

    def measurement_count(self) -> int:
        """Restituisce il numero totale di misurazioni registrate."""
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS cnt FROM measurements_log").fetchone()
        return int(row["cnt"]) if row else 0

    # ── Utilità ───────────────────────────────────────────────

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

    @staticmethod
    def _measurement_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "entityID": row["entity_id"],
            "plantID": row["plant_id"],
            "kind": row["kind"],
            "value": row["value"],
            "unit": row["unit"],
            "measuredAt": row["measured_at"],
            "source": row["source"],
            "reliability": row["reliability"],
            "createdAt": row["created_at"],
        }
