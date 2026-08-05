import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path
    max_photo_bytes: int

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.environ.get("VERDANT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("VERDANT_TOKEN non configurato")

        data_dir = Path(os.environ.get("VERDANT_DATA_DIR", "/data"))
        max_photo_mb = int(os.environ.get("VERDANT_MAX_PHOTO_MB", "20"))
        return cls(
            token=token,
            data_dir=data_dir,
            max_photo_bytes=max_photo_mb * 1024 * 1024,
        )

