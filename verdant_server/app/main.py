import hmac
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from database import COLLECTIONS, VerdantDatabase, VersionConflict
from photo_storage import PhotoStorage
from settings import Settings
from home_assistant import HomeAssistantSensorProvider


settings = Settings.from_environment()
database = VerdantDatabase(settings.data_dir / "verdant.sqlite3")
photos = PhotoStorage(settings.data_dir / "photos", settings.max_photo_bytes)
sensors = HomeAssistantSensorProvider(
    settings.home_assistant_url, settings.home_assistant_token, settings.exposed_entities
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    database.close()


app = FastAPI(title="Verdant Server", version="0.3.3", lifespan=lifespan)


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


def authorize(authorization: str | None = Header(default=None)) -> None:
    expected = f"Bearer {settings.token}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token non valido")


def validate_collection(collection: str) -> str:
    if collection not in COLLECTIONS:
        raise HTTPException(status_code=404, detail="Collezione non supportata")
    return collection


@app.get("/health")
def health() -> dict[str, str | list[str]]:
    return {
        "status": "ok",
        "service": "verdant-server",
        "version": "0.3.3",
        "capabilities": ["species-profiles", "measurements", "home-assistant-sensors", "sensor-mappings"],
    }


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


@app.get("/v1/photos/{photo_id}", dependencies=[Depends(authorize)])
def get_photo(photo_id: str, response: Response):
    photo = photos.find(photo_id)
    if photo is None:
        raise HTTPException(status_code=404, detail="Fotografia non trovata")
    return FileResponse(photo.path, media_type=photo.content_type, headers={"ETag": photo.checksum})
