import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import COLLECTIONS, VerdantDatabase, VersionConflict
from photo_storage import PhotoStorage
from settings import Settings
from home_assistant import HomeAssistantSensorProvider
from scheduler import CareScheduler
from care_engine import CareEngine
from ai_provider import create_ai_provider


# ── Configurazione ────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("verdant.main")

settings = Settings.from_environment()
database = VerdantDatabase(settings.data_dir / "verdant.sqlite3")
photos = PhotoStorage(settings.data_dir / "photos", settings.max_photo_bytes)
sensors = HomeAssistantSensorProvider(
    settings.home_assistant_url, settings.home_assistant_token, settings.exposed_entities
)
care_engine = CareEngine()
ai_provider = create_ai_provider(settings.ai_provider, settings.gemini_api_key, settings.openai_api_key)
scheduler = CareScheduler(database, sensors, settings.poll_interval_minutes)
ai_plan_refresh_lock = asyncio.Lock()
manual_ai_refresh_task: asyncio.Task | None = None


# ── Ciclo di vita dell'applicazione ───────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler_task = asyncio.create_task(scheduler.run())
    ai_plan_task = asyncio.create_task(_run_ai_plan_scheduler())
    yield
    scheduler_task.cancel()
    ai_plan_task.cancel()
    try:
        await asyncio.gather(scheduler_task, ai_plan_task)
    except asyncio.CancelledError:
        pass
    database.close()


app = FastAPI(title="Verdant Server", version="0.4.9", lifespan=lifespan)


# ── Modelli Pydantic ──────────────────────────────────────────

class EntityWrite(BaseModel):
    payload: dict[str, Any]
    expected_version: int | None = Field(default=None, alias="expectedVersion", ge=0)


class SensorMapping(BaseModel):
    entity_id: str = Field(alias="entityID")
    room: str | None = None
    plant_id: str | None = Field(default=None, alias="plantID")
    kind: str


class SensorMappingsWrite(BaseModel):
    items: list[SensorMapping]


# ── Middleware di autenticazione ──────────────────────────────

def authorize(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token non valido")


def validate_collection(collection: str) -> str:
    if collection not in COLLECTIONS:
        raise HTTPException(status_code=404, detail="Collezione non supportata")
    return collection


# ── Rotte esistenti ───────────────────────────────────────────

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "verdant-server",
        "version": "0.4.9",
        "capabilities": [
            "species-profiles", "measurements", "home-assistant-sensors",
            "sensor-mappings", "conditional-photos",
            "photo-catalog",
            "auto-ingestion", "care-engine", "ai-daily-digest",
        ],
        "scheduler": scheduler.status,
    }


@app.get("/admin", include_in_schema=False)
def admin_panel():
    return FileResponse(Path(__file__).with_name("admin.html"), media_type="text/html")


@app.get("/v1/sensors", dependencies=[Depends(authorize)])
def exposed_sensors():
    try:
        return {"items": sensors.sensors(), "exposedCount": len(settings.exposed_entities)}
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Home Assistant non raggiungibile: {error}") from error


@app.get("/v1/sensor-mappings", dependencies=[Depends(authorize)])
def get_sensor_mappings():
    return {"items": database.get_state("sensor-mappings", [])}


@app.put("/v1/sensor-mappings", dependencies=[Depends(authorize)])
def put_sensor_mappings(body: SensorMappingsWrite):
    items = [item.model_dump(by_alias=True) for item in body.items]
    database.set_state("sensor-mappings", items)
    return {"items": items}


@app.get("/v1/sync", dependencies=[Depends(authorize)])
def sync(since: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=1000)):
    changes = database.changes_since(since, limit)
    next_sequence = changes[-1].sequence if changes else since
    return {"changes": [change.as_dict() for change in changes], "nextSequence": next_sequence}


@app.get("/v1/entities/{collection}", dependencies=[Depends(authorize)])
def list_entities(collection: str):
    validate_collection(collection)
    return {"items": [entity.as_dict() for entity in database.list_entities(collection)]}


@app.put("/v1/entities/{collection}/{entity_id}", dependencies=[Depends(authorize)])
def put_entity(collection: str, entity_id: str, body: EntityWrite):
    validate_collection(collection)
    try:
        return database.upsert(collection, entity_id, body.payload, body.expected_version).as_dict()
    except VersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/v1/entities/{collection}/{entity_id}", dependencies=[Depends(authorize)])
def delete_entity(collection: str, entity_id: str, expected_version: int | None = Query(default=None, alias="expectedVersion", ge=0)):
    validate_collection(collection)
    try:
        return database.delete(collection, entity_id, expected_version).as_dict()
    except VersionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.put("/v1/photos/{photo_id}", dependencies=[Depends(authorize)])
async def put_photo(photo_id: str, request: Request, content_type: str | None = Header(default=None)):
    if content_type is None:
        raise HTTPException(status_code=415, detail="Content-Type mancante")
    data = await request.body()
    try:
        photo = photos.save(photo_id, content_type, data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"id": photo_id, "size": photo.size, "checksum": photo.checksum}


@app.head("/v1/photos/{photo_id}", dependencies=[Depends(authorize)])
def head_photo(photo_id: str):
    photo = photos.find(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Fotografia non trovata")
    return Response(headers={
        "Content-Type": photo.content_type,
        "Content-Length": str(photo.size),
        "ETag": f'"{photo.checksum}"',
    })


@app.get("/v1/photos/{photo_id}", dependencies=[Depends(authorize)])
def get_photo(photo_id: str, if_none_match: str | None = Header(default=None)):
    photo = photos.find(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Fotografia non trovata")
    etag = f'"{photo.checksum}"'
    if if_none_match is not None and if_none_match.strip() in {etag, photo.checksum}:
        return Response(status_code=304, headers={"ETag": etag})
    return FileResponse(photo.path, media_type=photo.content_type, headers={"ETag": etag})


@app.delete("/v1/photos/{photo_id}", dependencies=[Depends(authorize)])
def delete_photo(photo_id: str):
    if not photos.delete(photo_id):
        raise HTTPException(status_code=404, detail="Fotografia non trovata")
    return {"id": photo_id, "deleted": True}


# ── Nuove rotte: Misurazioni ─────────────────────────────────

@app.get("/v1/measurements/{entity_id}", dependencies=[Depends(authorize)])
def get_measurements(entity_id: str, since: str | None = Query(default=None), limit: int = Query(default=500, ge=1, le=5000)):
    """Serie temporale delle misurazioni per un'entità sensore."""
    return {"items": database.get_measurements(entity_id, since, limit)}


@app.get("/v1/measurements/plant/{plant_id}", dependencies=[Depends(authorize)])
def get_plant_measurements(
    plant_id: str,
    kind: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
):
    """Serie temporale delle misurazioni per una pianta."""
    plant = database.get_entity("plants", plant_id)
    room = plant.payload.get("room") if plant else None
    items = _measurements_for_plant(plant_id, room, limit=limit)
    if kind:
        items = [item for item in items if item.get("kind") == kind]
    if since:
        items = [item for item in items if item.get("measuredAt", "") >= since]
    unique = {item.get("id"): item for item in items if item.get("id")}
    return {"items": sorted(unique.values(), key=lambda item: item.get("measuredAt", ""), reverse=True)[:limit]}


# ── Nuove rotte: Care Engine ─────────────────────────────────

@app.get("/v1/care/status", dependencies=[Depends(authorize)])
def care_status():
    """Stato dello scheduler e del Care Engine."""
    return {
        "scheduler": scheduler.status,
        "aiProvider": settings.ai_provider if ai_provider else None,
        "aiAvailable": ai_provider is not None,
    }


@app.get("/v1/care/recommendations", dependencies=[Depends(authorize)])
def get_care_recommendations():
    """Raccomandazioni correnti per tutte le piante.

    Il calcolo deterministico è economico e usa sempre le misurazioni più recenti;
    non restituiamo una cache potenzialmente obsoleta tra due cicli sensori.
    """
    return _compute_recommendations_for_all()


@app.get("/v1/care/recommendations/{plant_id}", dependencies=[Depends(authorize)])
def get_plant_care_recommendation(plant_id: str):
    """Raccomandazione per una singola pianta, calcolata al volo."""
    measurements = database.get_plant_measurements(plant_id, limit=50)
    if not measurements:
        # Prova anche con le misurazioni per entità associate
        mappings = database.get_state("sensor-mappings", [])
        entity_ids = [m["entityID"] for m in mappings if m.get("plantID") == plant_id]
        for eid in entity_ids:
            measurements.extend(database.get_measurements(eid, limit=20))

    result = care_engine.evaluate_plant(plant_id, measurements)
    return result


@app.get("/v1/care/daily-digest", dependencies=[Depends(authorize)])
async def get_daily_digest():
    """Report giornaliero generato dall'AI."""
    if ai_provider is None:
        raise HTTPException(
            status_code=503,
            detail="Nessun provider AI configurato. Imposta VERDANT_AI_PROVIDER e la relativa chiave API.",
        )

    # Controlla la cache
    cached_digest = database.get_state("daily-digest", None)
    if cached_digest:
        from datetime import datetime, timezone
        cached_at = cached_digest.get("generatedAt", "")
        try:
            cache_time = datetime.fromisoformat(cached_at)
            age_hours = (datetime.now(timezone.utc) - cache_time).total_seconds() / 3600
            if age_hours < 12:
                return cached_digest
        except (ValueError, TypeError):
            pass

    # Genera un nuovo digest
    all_recommendations = _compute_recommendations_for_all()
    plants_summary = []
    for plant_data in all_recommendations.get("plants", []):
        plants_summary.append({
            "plantName": plant_data.get("plantID", "Sconosciuta"),
            "criticalAlerts": plant_data.get("criticalAlerts", []),
        })

    digest_text = await ai_provider.generate_daily_digest(plants_summary)

    result = {
        "digest": digest_text,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "plantCount": len(plants_summary),
    }
    database.set_state("daily-digest", result)
    return result


@app.get("/v1/care/ai-plan-status", dependencies=[Depends(authorize)])
def get_ai_plan_status():
    stored = database.get_state("ai-plan-status", {
        "provider": ai_provider.provider_name if ai_provider else None,
        "model": ai_provider.model_name if ai_provider else None,
        "lastRunAt": None, "lastSuccessAt": None, "lastError": None, "updatedPlantCount": 0,
        "outcome": "never-run", "nextRunAt": _next_ai_plan_run().isoformat(),
        "frequencyDays": settings.ai_plan_frequency_days, "updateTime": settings.ai_plan_update_time,
    })
    return {
        **stored,
        "provider": ai_provider.provider_name if ai_provider else stored.get("provider"),
        "model": ai_provider.model_name if ai_provider else stored.get("model"),
        "nextRunAt": None if stored.get("outcome") == "running" else _next_ai_plan_run().isoformat(),
        "frequencyDays": settings.ai_plan_frequency_days,
        "updateTime": settings.ai_plan_update_time,
    }


@app.post("/v1/care/ai-plans/refresh", dependencies=[Depends(authorize)], status_code=202)
async def refresh_ai_plans(force: bool = Query(default=False)):
    global manual_ai_refresh_task
    if ai_provider is None:
        raise HTTPException(status_code=503, detail="Nessun provider AI configurato")
    if ai_plan_refresh_lock.locked() or (manual_ai_refresh_task and not manual_ai_refresh_task.done()):
        raise HTTPException(status_code=409, detail="Un aggiornamento AI è già in corso")
    started_at = datetime.now(timezone.utc)
    previous = database.get_state("ai-plan-status", {})
    accepted = {
        "provider": ai_provider.provider_name if ai_provider else None,
        "model": ai_provider.model_name if ai_provider else None,
        "lastRunAt": started_at.isoformat(), "lastSuccessAt": previous.get("lastSuccessAt"),
        "lastError": None, "updatedPlantCount": 0, "skipped": False,
        "outcome": "running", "nextRunAt": None,
        "frequencyDays": settings.ai_plan_frequency_days, "updateTime": settings.ai_plan_update_time,
    }
    database.set_state("ai-plan-status", accepted)
    manual_ai_refresh_task = asyncio.create_task(_refresh_ai_plans(force=force))
    manual_ai_refresh_task.add_done_callback(_log_background_ai_result)
    return accepted


def _log_background_ai_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Aggiornamento AI manuale in background fallito")


async def _refresh_ai_plans(force: bool = False):
    if ai_plan_refresh_lock.locked():
        raise HTTPException(status_code=409, detail="Un aggiornamento AI è già in corso")
    async with ai_plan_refresh_lock:
        return await _refresh_ai_plans_locked(force)


async def _refresh_ai_plans_locked(force: bool = False):
    if ai_provider is None:
        raise HTTPException(status_code=503, detail="Nessun provider AI configurato")
    now = datetime.now(timezone.utc)
    previous = database.get_state("ai-plan-status", {})
    if not force and previous.get("lastSuccessAt"):
        try:
            if now - datetime.fromisoformat(previous["lastSuccessAt"]) < timedelta(hours=24):
                return {**previous, "skipped": True, "reason": "not-due"}
        except (ValueError, TypeError):
            pass
    database.set_state("ai-plan-status", {
        "provider": ai_provider.provider_name, "model": ai_provider.model_name,
        "lastRunAt": now.isoformat(), "lastSuccessAt": previous.get("lastSuccessAt"),
        "lastError": None, "updatedPlantCount": 0, "skipped": False,
        "outcome": "running", "nextRunAt": None,
        "frequencyDays": settings.ai_plan_frequency_days, "updateTime": settings.ai_plan_update_time,
    })
    updated = 0
    errors: list[str] = []
    for entity in database.list_entities("plants"):
        plant = dict(entity.payload)
        if plant.get("carePlanMode") != "AI":
            continue
        try:
            measurements = _measurements_for_plant(entity.entity_id, plant.get("room"), limit=20)
            care_results = care_engine.evaluate_plant(entity.entity_id, measurements)
            proposal = await ai_provider.revise_care_plan(plant, care_results)
            _save_ai_plan_on_latest_entity(entity.entity_id, proposal, now)
            updated += 1
        except Exception as error:
            logger.exception("Revisione AI fallita per %s", entity.entity_id)
            errors.append(f"{entity.entity_id}: {type(error).__name__}")
    status_payload = {
        "provider": ai_provider.provider_name, "model": ai_provider.model_name,
        "lastRunAt": now.isoformat(),
        "lastSuccessAt": now.isoformat() if not errors else previous.get("lastSuccessAt"),
        "lastError": "; ".join(errors) if errors else None,
        "updatedPlantCount": updated, "skipped": False,
        "outcome": "partial-failure" if errors else "success",
        "nextRunAt": _scheduled_ai_plan_run_after(now).isoformat(),
        "frequencyDays": settings.ai_plan_frequency_days,
        "updateTime": settings.ai_plan_update_time,
    }
    database.set_state("ai-plan-status", status_payload)
    return status_payload


def _configured_ai_plan_time() -> tuple[int, int]:
    try:
        hour_text, minute_text = settings.ai_plan_update_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError
        return hour, minute
    except (ValueError, AttributeError):
        logger.warning("Ora aggiornamento AI non valida (%s): uso 03:00", settings.ai_plan_update_time)
        return 3, 0


def _scheduled_ai_plan_run_after(reference: datetime) -> datetime:
    local_reference = reference.astimezone()
    hour, minute = _configured_ai_plan_time()
    return (local_reference + timedelta(days=settings.ai_plan_frequency_days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def _next_ai_plan_run(reference: datetime | None = None) -> datetime:
    local_now = (reference or datetime.now(timezone.utc)).astimezone()
    hour, minute = _configured_ai_plan_time()
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    status_value = database.get_state("ai-plan-status", {})
    last_run_text = status_value.get("lastRunAt")
    if status_value.get("outcome") == "running" and last_run_text:
        try:
            return datetime.fromisoformat(last_run_text).astimezone(local_now.tzinfo)
        except (ValueError, TypeError):
            pass
    if last_run_text:
        try:
            last_run = datetime.fromisoformat(last_run_text).astimezone(local_now.tzinfo)
            candidate = last_run.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
                days=settings.ai_plan_frequency_days
            )
        except (ValueError, TypeError):
            pass
    return candidate


async def _run_ai_plan_scheduler() -> None:
    while True:
        try:
            if (ai_provider is not None and not ai_plan_refresh_lock.locked()
                    and datetime.now().astimezone() >= _next_ai_plan_run()):
                await _refresh_ai_plans(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduler aggiornamento piani AI fallito")
        await asyncio.sleep(30)


# ── Utilità interne ───────────────────────────────────────────

def _compute_recommendations_for_all() -> dict[str, Any]:
    """Calcola le raccomandazioni per tutte le piante sincronizzate."""
    from datetime import datetime, timezone

    mappings = database.get_state("sensor-mappings", [])
    plant_entities = database.list_entities("plants")
    # Includi anche le piante senza sensori: il client deve poter mostrare
    # esplicitamente quali dati mancano invece di nascondere l'intera sezione.
    plants_data: dict[str, list[dict[str, Any]]] = {
        entity.entity_id: [] for entity in plant_entities
    }
    plant_ids_by_room: dict[str, list[str]] = {}
    for entity in plant_entities:
        room = entity.payload.get("room")
        if isinstance(room, str) and room:
            plant_ids_by_room.setdefault(room, []).append(entity.entity_id)

    for mapping in mappings:
        entity_id = mapping.get("entityID", "")
        measurements = database.get_measurements(entity_id, limit=20)
        plant_id = mapping.get("plantID")
        if plant_id:
            plants_data.setdefault(plant_id, []).extend(measurements)

        # I sensori ambientali associati a una stanza valgono per tutte le
        # piante collocate in quella stanza, anche se non hanno sensori propri.
        room = mapping.get("room")
        if isinstance(room, str) and room:
            for room_plant_id in plant_ids_by_room.get(room, []):
                plants_data[room_plant_id].extend(measurements)

    results = care_engine.evaluate_all_plants(plants_data)

    output = {
        "plants": results,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
        "plantCount": len(results),
    }
    return output


def _measurements_for_plant(plant_id: str, room: str | None, limit: int = 20) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for mapping in database.get_state("sensor-mappings", []):
        if mapping.get("plantID") == plant_id or (room and mapping.get("room") == room):
            result.extend(database.get_measurements(mapping.get("entityID", ""), limit=limit))
    return result


def _apply_safe_ai_plan_revision(plant: dict[str, Any], proposal: dict[str, Any], now: datetime, provider) -> None:
    def bounded(name: str, current: int, absolute_min: int, absolute_max: int) -> int:
        proposed = int(proposal.get(name, current))
        delta = max(1, round(current * 0.25))
        return max(absolute_min, min(absolute_max, max(current - delta, min(current + delta, proposed))))
    current_water = int(plant.get("wateringInterval", 7))
    current_feed = int(plant.get("fertilizingInterval") or 28)
    current_inspection = int(plant.get("inspectionInterval") or 7)
    water = bounded("wateringDays", current_water, 1, 120)
    feed = bounded("fertilizingDays", current_feed, 7, 365)
    inspection = bounded("inspectionDays", current_inspection, 1, 30)
    plant["wateringInterval"] = water
    plant["fertilizingInterval"] = feed
    plant["inspectionInterval"] = inspection
    plan = plant.get("adaptivePlan")
    if not isinstance(plan, dict):
        raise ValueError("Piano adattivo mancante")
    plan.update({"wateringDays": water, "fertilizingDays": feed, "inspectionDays": inspection,
                 "revisionSource": "AI", "revisionProvider": f"{provider.provider_name} · {provider.model_name}",
                 "revisedAt": now.timestamp() - 978307200})
    reason = str(proposal.get("reason", "Revisione AI basata sui dati disponibili."))[:300]
    rationale = plan.get("rationale") if isinstance(plan.get("rationale"), list) else []
    plan["rationale"] = [reason] + [item for item in rationale if item != reason][:7]


def _save_ai_plan_on_latest_entity(entity_id: str, proposal: dict[str, Any], now: datetime) -> None:
    for attempt in range(3):
        latest = database.get_entity("plants", entity_id)
        if latest is None:
            raise ValueError("Pianta rimossa durante l'aggiornamento")
        plant = dict(latest.payload)
        if plant.get("carePlanMode") != "AI":
            return
        _apply_safe_ai_plan_revision(plant, proposal, now, ai_provider)
        try:
            database.upsert("plants", entity_id, plant, latest.version)
            return
        except VersionConflict:
            if attempt == 2:
                raise
