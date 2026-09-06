"""Test per la tabella measurements_log e i metodi di accesso."""

import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Permetti l'importazione dei moduli dell'app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from database import VerdantDatabase


@pytest.fixture
def db(tmp_path):
    """Crea un database temporaneo per ogni test."""
    database = VerdantDatabase(tmp_path / "test.sqlite3")
    yield database
    database.close()


class TestInsertMeasurement:
    def test_inserisce_misurazione(self, db):
        saved = db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.5,
            measured_at="2026-08-13T10:00:00+00:00",
            plant_id="abc-123",
            unit="°C",
        )
        assert saved is True
        assert db.measurement_count() == 1

    def test_deduplicazione_entro_60_secondi(self, db):
        db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.5,
            measured_at="2026-08-13T10:00:00+00:00",
        )
        # Stesso entity_id e timestamp simile (30 secondi dopo) → duplicato
        saved = db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.6,
            measured_at="2026-08-13T10:00:30+00:00",
        )
        assert saved is False
        assert db.measurement_count() == 1

    def test_non_deduplicazione_dopo_60_secondi(self, db):
        db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.5,
            measured_at="2026-08-13T10:00:00+00:00",
        )
        # 2 minuti dopo → non è duplicato
        saved = db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.8,
            measured_at="2026-08-13T10:02:00+00:00",
        )
        assert saved is True
        assert db.measurement_count() == 2

    def test_entita_diverse_non_deduplicano(self, db):
        db.insert_measurement(
            entity_id="sensor.temp_salone",
            kind="temperature",
            value=23.5,
            measured_at="2026-08-13T10:00:00+00:00",
        )
        saved = db.insert_measurement(
            entity_id="sensor.temp_camera",
            kind="temperature",
            value=21.0,
            measured_at="2026-08-13T10:00:00+00:00",
        )
        assert saved is True
        assert db.measurement_count() == 2


class TestGetMeasurements:
    def test_restituisce_misurazioni_per_entita(self, db):
        for i in range(5):
            db.insert_measurement(
                entity_id="sensor.moisture_1",
                kind="moisture",
                value=50.0 - i * 5,
                measured_at=f"2026-08-13T{10+i}:00:00+00:00",
                unit="%",
            )
        results = db.get_measurements("sensor.moisture_1")
        assert len(results) == 5
        # Ordinate per measured_at DESC
        assert results[0]["value"] == 30.0  # ultima lettura
        assert results[4]["value"] == 50.0  # prima lettura

    def test_filtro_since(self, db):
        for i in range(5):
            db.insert_measurement(
                entity_id="sensor.moisture_1",
                kind="moisture",
                value=50.0 - i * 5,
                measured_at=f"2026-08-13T{10+i}:00:00+00:00",
            )
        results = db.get_measurements("sensor.moisture_1", since="2026-08-13T12:00:00+00:00")
        assert len(results) == 3  # 12:00, 13:00, 14:00


class TestGetPlantMeasurements:
    def test_restituisce_misurazioni_per_pianta(self, db):
        db.insert_measurement(
            entity_id="sensor.moisture_1", kind="moisture", value=45.0,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="pianta-1",
        )
        db.insert_measurement(
            entity_id="sensor.temp_1", kind="temperature", value=22.0,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="pianta-1",
        )
        db.insert_measurement(
            entity_id="sensor.moisture_2", kind="moisture", value=60.0,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="pianta-2",
        )
        results = db.get_plant_measurements("pianta-1")
        assert len(results) == 2

    def test_filtro_per_tipo(self, db):
        db.insert_measurement(
            entity_id="sensor.moisture_1", kind="moisture", value=45.0,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="pianta-1",
        )
        db.insert_measurement(
            entity_id="sensor.temp_1", kind="temperature", value=22.0,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="pianta-1",
        )
        results = db.get_plant_measurements("pianta-1", kind="moisture")
        assert len(results) == 1
        assert results[0]["kind"] == "moisture"


class TestGetLatestMeasurements:
    def test_ultima_lettura_per_entita(self, db):
        db.insert_measurement(
            entity_id="sensor.temp_1", kind="temperature", value=20.0,
            measured_at="2026-08-13T08:00:00+00:00",
        )
        db.insert_measurement(
            entity_id="sensor.temp_1", kind="temperature", value=23.5,
            measured_at="2026-08-13T12:00:00+00:00",
        )
        results = db.get_latest_measurements(["sensor.temp_1"])
        assert len(results) == 1
        assert results[0]["value"] == 23.5


class TestMeasurementFormat:
    def test_formato_output_camelcase(self, db):
        db.insert_measurement(
            entity_id="sensor.temp_1", kind="temperature", value=22.5,
            measured_at="2026-08-13T10:00:00+00:00", plant_id="abc",
            unit="°C", source="home_assistant", reliability=0.9,
        )
        result = db.get_measurements("sensor.temp_1")[0]
        # Verifica che le chiavi siano in camelCase per il client Swift
        assert "entityID" in result
        assert "plantID" in result
        assert "measuredAt" in result
        assert "createdAt" in result
        assert result["entityID"] == "sensor.temp_1"
        assert result["plantID"] == "abc"
        assert result["kind"] == "temperature"
        assert result["value"] == 22.5
        assert result["unit"] == "°C"
