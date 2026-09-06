import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path
    max_photo_bytes: int
    exposed_entities: frozenset[str]
    home_assistant_url: str
    home_assistant_token: str
    # Nuove impostazioni Care Engine
    poll_interval_minutes: int
    gemini_api_key: str
    openai_api_key: str
    ai_provider: str  # "gemini" o "openai"
    ai_plan_frequency_days: int
    ai_plan_update_time: str

    @classmethod
    def from_environment(cls) -> "Settings":
        token = os.environ.get("VERDANT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("VERDANT_TOKEN non configurato")

        data_dir = Path(os.environ.get("VERDANT_DATA_DIR", "/data"))
        max_photo_mb = int(os.environ.get("VERDANT_MAX_PHOTO_MB", "20"))
        exposed_raw = os.environ.get("VERDANT_EXPOSED_ENTITIES", "[]").strip()
        try:
            exposed_value = json.loads(exposed_raw)
            exposed = exposed_value if isinstance(exposed_value, list) else []
        except json.JSONDecodeError:
            exposed = [value.strip() for value in exposed_raw.split(",") if value.strip()]
        options_path = data_dir / "options.json"
        if options_path.is_file():
            try:
                options = json.loads(options_path.read_text(encoding="utf-8"))
                configured = options.get("exposed_entities")
                if isinstance(configured, list):
                    exposed = configured
            except (OSError, json.JSONDecodeError):
                pass
        return cls(
            token=token,
            data_dir=data_dir,
            max_photo_bytes=max_photo_mb * 1024 * 1024,
            exposed_entities=frozenset(str(value).strip() for value in exposed if str(value).strip()),
            home_assistant_url=os.environ.get("VERDANT_HOME_ASSISTANT_URL", "http://supervisor/core/api").rstrip("/"),
            home_assistant_token=os.environ.get("SUPERVISOR_TOKEN", "").strip(),
            poll_interval_minutes=int(os.environ.get("VERDANT_POLL_INTERVAL_MINUTES", "15")),
            gemini_api_key=os.environ.get("VERDANT_GEMINI_API_KEY", "").strip(),
            openai_api_key=os.environ.get("VERDANT_OPENAI_API_KEY", "").strip(),
            ai_provider=os.environ.get("VERDANT_AI_PROVIDER", "none").strip().lower(),
            ai_plan_frequency_days=max(1, int(os.environ.get("VERDANT_AI_PLAN_FREQUENCY_DAYS", "1"))),
            ai_plan_update_time=os.environ.get("VERDANT_AI_PLAN_UPDATE_TIME", "03:00").strip(),
        )

    @property
    def ai_available(self) -> bool:
        """Verifica se almeno un provider AI è configurato."""
        if self.ai_provider == "gemini":
            return bool(self.gemini_api_key)
        if self.ai_provider == "openai":
            return bool(self.openai_api_key)
        return False
