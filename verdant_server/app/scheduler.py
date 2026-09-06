"""Scheduler autonomo per il campionamento periodico dei sensori e l'esecuzione del Care Engine."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from home_assistant import HomeAssistantSensorProvider, SUPPORTED_DEVICE_CLASSES
from database import VerdantDatabase


logger = logging.getLogger("verdant.scheduler")


class CareScheduler:
    """Loop asincrono che ogni N minuti legge i sensori da HA e salva le misurazioni."""

    def __init__(
        self,
        database: VerdantDatabase,
        sensors: HomeAssistantSensorProvider,
        poll_interval_minutes: int = 15,
    ):
        self._database = database
        self._sensors = sensors
        self._poll_interval = poll_interval_minutes * 60  # in secondi
        self._last_cycle: str | None = None
        self._last_error: str | None = None
        self._cycle_count: int = 0
        self._measurements_saved: int = 0
        self._active: bool = False

    @property
    def status(self) -> dict[str, Any]:
        """Stato corrente dello scheduler per l'endpoint /v1/care/status."""
        return {
            "active": self._active,
            "pollIntervalMinutes": self._poll_interval // 60,
            "lastCycle": self._last_cycle,
            "lastError": self._last_error,
            "cycleCount": self._cycle_count,
            "measurementsSaved": self._measurements_saved,
            "totalMeasurements": self._database.measurement_count(),
        }

    async def run(self) -> None:
        """Loop principale — da avviare con asyncio.create_task nel lifespan."""
        self._active = True
        logger.info(
            "Care Scheduler avviato — polling ogni %d minuti",
            self._poll_interval // 60,
        )
        # Piccolo ritardo iniziale per permettere al server di avviarsi completamente
        await asyncio.sleep(5)
        try:
            while True:
                try:
                    await self._run_cycle()
                    self._last_error = None
                except asyncio.CancelledError:
                    logger.info("Care Scheduler arrestato")
                    raise
                except Exception as error:
                    self._last_error = f"{type(error).__name__}: {error}"
                    logger.error("Errore nel ciclo dello scheduler: %s", error, exc_info=True)
                await asyncio.sleep(self._poll_interval)
        finally:
            self._active = False

    async def _run_cycle(self) -> None:
        """Esegue un singolo ciclo di campionamento."""
        cycle_start = datetime.now(timezone.utc)
        logger.info("Inizio ciclo di campionamento #%d", self._cycle_count + 1)

        # 1. Recupera le associazioni sensore-pianta dal database
        mappings = self._database.get_state("sensor-mappings", [])
        if not mappings:
            logger.info("Nessuna associazione sensore configurata — ciclo saltato")
            self._last_cycle = cycle_start.isoformat()
            self._cycle_count += 1
            return

        # 2. Recupera le letture correnti da Home Assistant
        try:
            sensor_readings = await self._sensors.sensors_async()
        except Exception as error:
            logger.warning("Impossibile leggere i sensori da HA: %s", error)
            raise

        if not sensor_readings:
            logger.info("Nessuna lettura sensore disponibile")
            self._last_cycle = cycle_start.isoformat()
            self._cycle_count += 1
            return

        # 3. Crea un indice delle letture per entity_id
        readings_by_entity: dict[str, dict[str, Any]] = {
            reading["entityID"]: reading for reading in sensor_readings
        }

        # 4. Per ogni mapping, salva la misurazione nel database
        saved_count = 0
        for mapping in mappings:
            entity_id = mapping.get("entityID", "")
            reading = readings_by_entity.get(entity_id)
            if reading is None:
                continue

            kind = reading.get("kind")
            if kind not in SUPPORTED_DEVICE_CLASSES.values():
                continue

            value = reading.get("value")
            if value is None or not isinstance(value, (int, float)):
                continue

            # La cronologia rappresenta i campionamenti eseguiti da Verdant: tutte le
            # letture dello stesso ciclo devono quindi avere l'orario del ciclo, non
            # l'ultimo aggiornamento indipendente della singola entità in HA.
            measured_at = cycle_start.isoformat()

            plant_id = mapping.get("plantID")
            unit = reading.get("unit")

            was_saved = self._database.insert_measurement(
                entity_id=entity_id,
                kind=kind,
                value=float(value),
                measured_at=measured_at,
                plant_id=plant_id,
                unit=unit,
            )
            if was_saved:
                saved_count += 1

        self._measurements_saved += saved_count
        self._cycle_count += 1
        self._last_cycle = cycle_start.isoformat()

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info(
            "Ciclo #%d completato in %.1fs — %d letture, %d nuove misurazioni salvate",
            self._cycle_count,
            elapsed,
            len(sensor_readings),
            saved_count,
        )

        # 5. Salva lo stato dell'ultimo ciclo per consultazione
        self._database.set_state("scheduler-status", {
            "lastCycle": self._last_cycle,
            "cycleCount": self._cycle_count,
            "lastSaved": saved_count,
            "totalSaved": self._measurements_saved,
        })
