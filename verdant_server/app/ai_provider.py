"""Provider AI per la generazione di raccomandazioni in linguaggio naturale."""

import json
import logging
from abc import ABC, abstractmethod
from typing import Any


logger = logging.getLogger("verdant.ai_provider")


# ── Prompt di sistema ─────────────────────────────────────────

SYSTEM_PROMPT = """Sei un esperto botanico e agronomo italiano specializzato nella cura delle piante da appartamento e da giardino.
Il tuo compito è analizzare i dati dei sensori e le valutazioni del Care Engine per generare raccomandazioni pratiche e comprensibili.

Regole:
- Rispondi SEMPRE in italiano
- Sii conciso e pratico: massimo 3-4 frasi per raccomandazione
- Usa un tono amichevole ma competente
- Se i dati sono insufficienti, dillo chiaramente senza inventare
- Quando possibile, spiega il PERCHÉ delle tue raccomandazioni
- Non ripetere i numeri esatti dei sensori, concentrati sull'azione da compiere
"""

RECOMMENDATION_PROMPT = """Analizza i dati di questa pianta e genera una raccomandazione pratica.

Pianta: {plant_name}
Specie: {species}
Posizione: {room}

Dati dei sensori (ultime letture):
{sensor_data}

Valutazioni del Care Engine:
{care_results}

Genera una raccomandazione breve e pratica in italiano, concentrandoti sulle azioni prioritarie.
"""

DAILY_DIGEST_PROMPT = """Genera un report mattutino sullo stato di tutte le piante.

{plants_summary}

Scrivi un breve report giornaliero in italiano (massimo 200 parole) che:
1. Riassuma la situazione generale
2. Evidenzi le piante che necessitano attenzione immediata
3. Suggerisca le azioni prioritarie per oggi
"""


# ── Classe base ───────────────────────────────────────────────

class AIProvider(ABC):
    provider_name = "unknown"
    model_name = "unknown"

    """Interfaccia astratta per i provider AI."""

    @abstractmethod
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Genera una risposta dal modello AI."""
        ...

    async def generate_recommendation(
        self, plant_context: dict[str, Any], care_results: dict[str, Any]
    ) -> str:
        """Genera una raccomandazione per una singola pianta."""
        sensor_lines = []
        for rec in care_results.get("recommendations", []):
            for factor in rec.get("factors", []):
                value = factor.get("value")
                unit = factor.get("unit", "")
                if value is not None:
                    sensor_lines.append(f"- {factor['description']}: {value} {unit}")

        care_lines = []
        for rec in care_results.get("recommendations", []):
            if rec.get("action") != "insufficient_data":
                care_lines.append(f"- [{rec['domain']}] {rec['message']} (urgenza: {rec['urgency']})")

        prompt = RECOMMENDATION_PROMPT.format(
            plant_name=plant_context.get("name", "Sconosciuta"),
            species=plant_context.get("species", "Non specificata"),
            room=plant_context.get("room", "Non specificata"),
            sensor_data="\n".join(sensor_lines) if sensor_lines else "Nessun dato disponibile",
            care_results="\n".join(care_lines) if care_lines else "Nessuna valutazione disponibile",
        )

        try:
            return await self.generate(SYSTEM_PROMPT, prompt)
        except Exception as error:
            logger.error("Errore nella generazione AI: %s", error)
            return ""

    async def generate_daily_digest(self, plants_summary: list[dict[str, Any]]) -> str:
        """Genera il report giornaliero per tutte le piante."""
        summary_lines = []
        for plant in plants_summary:
            alerts = plant.get("criticalAlerts", [])
            status = "⚠️ " + " | ".join(alerts) if alerts else "✅ OK"
            summary_lines.append(f"- {plant.get('plantName', 'Pianta')}: {status}")

        prompt = DAILY_DIGEST_PROMPT.format(
            plants_summary="\n".join(summary_lines) if summary_lines else "Nessuna pianta configurata."
        )

        try:
            return await self.generate(SYSTEM_PROMPT, prompt)
        except Exception as error:
            logger.error("Errore nella generazione del report giornaliero: %s", error)
            return ""

    async def revise_care_plan(self, plant: dict[str, Any], care_results: dict[str, Any]) -> dict[str, Any]:
        current_water = int(plant.get("wateringInterval", 7))
        current_feed = int(plant.get("fertilizingInterval") or 28)
        current_inspection = int(plant.get("inspectionInterval") or 7)
        prompt = f"""Revisiona prudentemente il piano di cura e restituisci SOLO JSON valido.
Pianta: {plant.get('name', 'Pianta')} ({plant.get('species', 'specie non indicata')})
Posizione: {plant.get('room', 'non indicata')}
Intervalli attuali: acqua {current_water} giorni, concime {current_feed} giorni, controllo {current_inspection} giorni.
Valutazioni sensori: {json.dumps(care_results, ensure_ascii=False)}
Formato obbligatorio: {{"wateringDays": intero, "fertilizingDays": intero, "inspectionDays": intero, "reason": "testo breve in italiano"}}
Non proporre variazioni drastiche. Se i dati sono insufficienti mantieni gli intervalli attuali."""
        raw = await self.generate(SYSTEM_PROMPT, prompt)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(cleaned)
        if not isinstance(result, dict):
            raise ValueError("Risposta AI non strutturata")
        return result


# ── Provider Gemini ───────────────────────────────────────────

class GeminiProvider(AIProvider):
    """Provider che usa Google Gemini."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash-lite"):
        self.provider_name = "Gemini"
        self.model_name = model
        self._api_key = api_key
        self._model = model

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        from google import genai

        client = genai.Client(api_key=self._api_key)
        response = await client.aio.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=500,
                temperature=0.7,
            ),
        )
        return response.text or ""


# ── Provider OpenAI ───────────────────────────────────────────

class OpenAIProvider(AIProvider):
    """Provider che usa OpenAI ChatGPT."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.provider_name = "OpenAI"
        self.model_name = model
        self._api_key = api_key
        self._model = model

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=500,
            temperature=0.7,
        )
        choice = response.choices[0] if response.choices else None
        return choice.message.content or "" if choice else ""


# ── Factory ───────────────────────────────────────────────────

def create_ai_provider(provider_name: str, gemini_key: str, openai_key: str) -> AIProvider | None:
    """Crea il provider AI scelto dall'utente. Restituisce None se non configurato."""
    if provider_name == "gemini" and gemini_key:
        logger.info("Provider AI configurato: Gemini")
        return GeminiProvider(api_key=gemini_key)
    elif provider_name == "openai" and openai_key:
        logger.info("Provider AI configurato: OpenAI")
        return OpenAIProvider(api_key=openai_key)
    else:
        logger.info("Nessun provider AI configurato (provider=%s)", provider_name)
        return None
