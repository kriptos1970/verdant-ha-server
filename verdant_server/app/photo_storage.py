import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path


ALLOWED_CONTENT_TYPES = {
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


@dataclass(frozen=True)
class StoredPhoto:
    path: Path
    content_type: str
    checksum: str
    size: int


class PhotoStorage:
    def __init__(self, directory: Path, max_bytes: int):
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def save(self, photo_id: str, content_type: str, data: bytes) -> StoredPhoto:
        extension = ALLOWED_CONTENT_TYPES.get(content_type.lower())
        if extension is None:
            raise ValueError("Formato immagine non supportato")
        if not data or len(data) > self._max_bytes:
            raise ValueError("Dimensione immagine non valida")

        self._remove_existing(photo_id)
        path = self._directory / f"{photo_id}{extension}"
        path.write_bytes(data)
        return StoredPhoto(path, content_type.lower(), hashlib.sha256(data).hexdigest(), len(data))

    def find(self, photo_id: str) -> StoredPhoto | None:
        matches = list(self._directory.glob(f"{photo_id}.*"))
        if not matches:
            return None
        path = matches[0]
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return StoredPhoto(path, content_type, hashlib.sha256(data).hexdigest(), len(data))

    def _remove_existing(self, photo_id: str) -> None:
        for path in self._directory.glob(f"{photo_id}.*"):
            path.unlink()

