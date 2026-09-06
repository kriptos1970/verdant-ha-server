"""Care Engine deterministico — valutazioni idriche, climatiche e luminose."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


logger = logging.getLogger("verdant.care_engine")


# ── Enumerazioni ──────────────────────────────────────────────

class CareAction(str, Enum):
    """Azione consigliata dal motore di cura."""
    WATER_NOW = "water_now"
    CHECK_SUBSTRATE = "check_substrate"
    WAIT_TO_WATER = "wait_to_water"
    IMPROVE_LIGHT = "improve_light"
    MAINTAIN_LIGHT = "maintain_light"
    MONITOR_DIRECT_SUN = "monitor_direct_sun"
    PROTECT_FROM_COLD = "protect_from_cold"
    PROTECT_FROM_HEAT = "protect_from_heat"
    INCREASE_HUMIDITY = "increase_humidity"
    REDUCE_HUMIDITY = "reduce_humidity"
    OBSERVE = "observe"
    INSUFFICIENT_DATA = "insufficient_data"


class Urgency(str, Enum):
    """Livello di urgenza della raccomandazione."""
    CRITICAL = "critical"
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    INFO = "info"


class CareDomain(str, Enum):
    """Dominio di valutazione."""
    WATER = "water"
    LIGHT = "light"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"


# ── Modelli ───────────────────────────────────────────────────

@dataclass
class CareFactor:
    """Un singolo fattore che contribuisce alla valutazione."""
    code: str
    description: str
    value: float | None = None
    unit: str | None = None
    impact: str = "neutral"  # "positive", "negative", "neutral"


@dataclass
class CareRecommendation:
    """Raccomandazione generata dal Care Engine."""
    domain: str
    action: str
    urgency: str
    confidence: float
    factors: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    valid_from: str = ""
    valid_until: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "action": self.action,
            "urgency": self.urgency,
            "confidence": self.confidence,
            "factors": self.factors,
            "message": self.message,
            "validFrom": self.valid_from,
            "validUntil": self.valid_until,
        }


# ── Valutatori ────────────────────────────────────────────────

class WaterEvaluator:
    """Valutazione idrica basata sull'umidità del substrato."""

    # Soglie predefinite (possono variare per specie in futuro)
    MOISTURE_CRITICAL_LOW = 15.0   # % — irrigazione urgente
    MOISTURE_LOW = 25.0            # % — valuta irrigazione
    MOISTURE_OPTIMAL_MIN = 30.0    # %
    MOISTURE_OPTIMAL_MAX = 60.0    # %
    MOISTURE_HIGH = 70.0           # % — troppo umido
    MOISTURE_CRITICAL_HIGH = 80.0  # % — rischio ristagno

    def evaluate(self, measurements: list[dict[str, Any]]) -> CareRecommendation:
        """Valuta la condizione idrica basandosi sulle misurazioni di moisture."""
        moisture_readings = [m for m in measurements if m["kind"] == "moisture"]

        if not moisture_readings:
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.INSUFFICIENT_DATA.value,
                urgency=Urgency.INFO.value,
                confidence=0.0,
                message="Nessun sensore di umidità del substrato disponibile.",
            )

        # Prendi l'ultima lettura
        latest = moisture_readings[0]  # già ordinate per measured_at DESC
        current_moisture = latest["value"]

        # Analisi del trend se abbiamo abbastanza dati
        trend = self._calculate_trend(moisture_readings)
        factors = [
            {"code": "current_moisture", "description": "Umidità attuale del substrato",
             "value": current_moisture, "unit": "%", "impact": "neutral",
             "measuredAt": latest.get("measuredAt"), "source": latest.get("source")},
        ]
        if trend is not None:
            factors.append({
                "code": "moisture_trend", "description": "Trend umidità (variazione/ora)",
                "value": round(trend, 3), "unit": "%/h",
                "impact": "negative" if trend < -1.0 else "neutral",
            })

        # Valutazione
        if current_moisture <= self.MOISTURE_CRITICAL_LOW:
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.WATER_NOW.value,
                urgency=Urgency.CRITICAL.value,
                confidence=0.9,
                factors=factors,
                message=f"Substrato molto secco ({current_moisture:.0f}%). Irrigazione urgente consigliata.",
            )
        elif current_moisture <= self.MOISTURE_LOW:
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.CHECK_SUBSTRATE.value,
                urgency=Urgency.HIGH.value,
                confidence=0.8,
                factors=factors,
                message=f"Substrato asciutto ({current_moisture:.0f}%). Valuta un'irrigazione.",
            )
        elif current_moisture >= self.MOISTURE_CRITICAL_HIGH:
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.WAIT_TO_WATER.value,
                urgency=Urgency.HIGH.value,
                confidence=0.85,
                factors=factors,
                message=f"Substrato molto umido ({current_moisture:.0f}%). Rischio ristagno, non irrigare.",
            )
        elif current_moisture >= self.MOISTURE_HIGH:
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.WAIT_TO_WATER.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.8,
                factors=factors,
                message=f"Substrato ancora umido ({current_moisture:.0f}%). Rimanda l'irrigazione.",
            )
        else:
            # Zona ottimale
            eta = self._estimate_next_watering(current_moisture, trend)
            if eta:
                factors.append({
                    "code": "eta_watering", "description": "Stima prossima irrigazione",
                    "value": eta, "unit": "ore", "impact": "neutral",
                })
            return CareRecommendation(
                domain=CareDomain.WATER.value,
                action=CareAction.OBSERVE.value,
                urgency=Urgency.LOW.value,
                confidence=0.75,
                factors=factors,
                message=f"Umidità nella norma ({current_moisture:.0f}%)."
                        + (f" Stima prossima irrigazione tra ~{eta:.0f} ore." if eta else ""),
            )

    def _calculate_trend(self, readings: list[dict[str, Any]]) -> float | None:
        """Calcola il trend di variazione dell'umidità in %/ora."""
        if len(readings) < 2:
            return None
        try:
            latest = readings[0]
            oldest = readings[min(len(readings) - 1, 5)]  # max ultimi 6 punti
            t_latest = datetime.fromisoformat(latest["measuredAt"].replace("Z", "+00:00"))
            t_oldest = datetime.fromisoformat(oldest["measuredAt"].replace("Z", "+00:00"))
            hours = (t_latest - t_oldest).total_seconds() / 3600
            if hours < 0.25:  # meno di 15 minuti non è significativo
                return None
            return (latest["value"] - oldest["value"]) / hours
        except (KeyError, ValueError, TypeError):
            return None

    def _estimate_next_watering(self, current: float, trend: float | None) -> float | None:
        """Stima le ore alla prossima irrigazione basandosi sul trend Dry-Down."""
        if trend is None or trend >= 0:
            return None  # umidità stabile o in aumento
        # Stima il tempo per raggiungere la soglia di irrigazione
        target = self.MOISTURE_LOW
        if current <= target:
            return 0
        hours = (current - target) / abs(trend)
        return round(hours, 1) if hours < 720 else None  # max 30 giorni


class TemperatureEvaluator:
    """Valutazione della temperatura ambientale."""

    TEMP_CRITICAL_LOW = 5.0    # °C — rischio gelata
    TEMP_LOW = 10.0            # °C — freddo per molte piante
    TEMP_OPTIMAL_MIN = 16.0    # °C
    TEMP_OPTIMAL_MAX = 28.0    # °C
    TEMP_HIGH = 35.0           # °C — stress da calore
    TEMP_CRITICAL_HIGH = 40.0  # °C — calore pericoloso

    def evaluate(self, measurements: list[dict[str, Any]]) -> CareRecommendation:
        temp_readings = [m for m in measurements if m["kind"] == "temperature"]

        if not temp_readings:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.INSUFFICIENT_DATA.value,
                urgency=Urgency.INFO.value,
                confidence=0.0,
                message="Nessun sensore di temperatura disponibile.",
            )

        latest = temp_readings[0]
        temp = latest["value"]
        factors = [{"code": "current_temp", "description": "Temperatura attuale",
                     "value": temp, "unit": "°C", "impact": "neutral",
                     "measuredAt": latest.get("measuredAt"), "source": latest.get("source")}]

        if temp <= self.TEMP_CRITICAL_LOW:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.PROTECT_FROM_COLD.value,
                urgency=Urgency.CRITICAL.value,
                confidence=0.95,
                factors=factors,
                message=f"Temperatura critica ({temp:.1f}°C). Rischio gelata! Proteggere la pianta.",
            )
        elif temp <= self.TEMP_LOW:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.PROTECT_FROM_COLD.value,
                urgency=Urgency.HIGH.value,
                confidence=0.85,
                factors=factors,
                message=f"Temperatura bassa ({temp:.1f}°C). Troppo freddo per molte piante tropicali.",
            )
        elif temp >= self.TEMP_CRITICAL_HIGH:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.PROTECT_FROM_HEAT.value,
                urgency=Urgency.CRITICAL.value,
                confidence=0.95,
                factors=factors,
                message=f"Temperatura pericolosa ({temp:.1f}°C). Spostare la pianta all'ombra e irrigare.",
            )
        elif temp >= self.TEMP_HIGH:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.PROTECT_FROM_HEAT.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.8,
                factors=factors,
                message=f"Temperatura elevata ({temp:.1f}°C). Controllare esposizione e idratazione.",
            )
        else:
            return CareRecommendation(
                domain=CareDomain.TEMPERATURE.value,
                action=CareAction.OBSERVE.value,
                urgency=Urgency.LOW.value,
                confidence=0.8,
                factors=factors,
                message=f"Temperatura nella norma ({temp:.1f}°C).",
            )


class HumidityEvaluator:
    """Valutazione dell'umidità dell'aria."""

    HUMIDITY_LOW = 30.0    # % — aria molto secca
    HUMIDITY_OPTIMAL_MIN = 40.0
    HUMIDITY_OPTIMAL_MAX = 70.0
    HUMIDITY_HIGH = 80.0   # % — rischio muffe

    def evaluate(self, measurements: list[dict[str, Any]]) -> CareRecommendation:
        humidity_readings = [m for m in measurements if m["kind"] == "humidity"]

        if not humidity_readings:
            return CareRecommendation(
                domain=CareDomain.HUMIDITY.value,
                action=CareAction.INSUFFICIENT_DATA.value,
                urgency=Urgency.INFO.value,
                confidence=0.0,
                message="Nessun sensore di umidità dell'aria disponibile.",
            )

        latest = humidity_readings[0]
        humidity = latest["value"]
        factors = [{"code": "current_humidity", "description": "Umidità aria attuale",
                     "value": humidity, "unit": "%", "impact": "neutral",
                     "measuredAt": latest.get("measuredAt"), "source": latest.get("source")}]

        if humidity < self.HUMIDITY_LOW:
            return CareRecommendation(
                domain=CareDomain.HUMIDITY.value,
                action=CareAction.INCREASE_HUMIDITY.value,
                urgency=Urgency.HIGH.value,
                confidence=0.8,
                factors=factors,
                message=f"Aria molto secca ({humidity:.0f}%). Nebulizzare o usare un umidificatore.",
            )
        elif humidity < self.HUMIDITY_OPTIMAL_MIN:
            return CareRecommendation(
                domain=CareDomain.HUMIDITY.value,
                action=CareAction.INCREASE_HUMIDITY.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.7,
                factors=factors,
                message=f"Umidità bassa ({humidity:.0f}%). Molte piante tropicali preferiscono valori più alti.",
            )
        elif humidity > self.HUMIDITY_HIGH:
            return CareRecommendation(
                domain=CareDomain.HUMIDITY.value,
                action=CareAction.REDUCE_HUMIDITY.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.75,
                factors=factors,
                message=f"Umidità molto alta ({humidity:.0f}%). Rischio muffe, ventilare l'ambiente.",
            )
        else:
            return CareRecommendation(
                domain=CareDomain.HUMIDITY.value,
                action=CareAction.OBSERVE.value,
                urgency=Urgency.LOW.value,
                confidence=0.75,
                factors=factors,
                message=f"Umidità dell'aria nella norma ({humidity:.0f}%).",
            )


class LightEvaluator:
    """Valutazione dell'illuminamento."""

    LUX_VERY_LOW = 500      # lux — luce insufficiente per la maggior parte delle piante
    LUX_LOW = 1000           # lux — luce bassa
    LUX_MODERATE = 5000      # lux — luce moderata
    LUX_HIGH = 30000         # lux — luce intensa
    LUX_DIRECT_SUN = 50000   # lux — possibile sole diretto

    def evaluate(self, measurements: list[dict[str, Any]], evaluated_at: datetime | None = None) -> CareRecommendation:
        light_readings = [m for m in measurements if m["kind"] == "illuminance"]

        if not light_readings:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.INSUFFICIENT_DATA.value,
                urgency=Urgency.INFO.value,
                confidence=0.0,
                message="Nessun sensore di luminosità disponibile.",
            )

        latest = light_readings[0]
        lux = latest["value"]
        factors = [{"code": "current_lux", "description": "Illuminamento attuale",
                     "value": lux, "unit": "lux", "impact": "neutral",
                     "measuredAt": latest.get("measuredAt"), "source": latest.get("source")}]

        local_time = (evaluated_at or datetime.now(timezone.utc)).astimezone(ZoneInfo("Europe/Rome"))
        hour = local_time.hour
        factors.append({"code": "light_time_context", "description": "Fascia oraria della valutazione",
                        "value": hour, "unit": "ora", "impact": "neutral"})
        # La luminosità istantanea è significativa soltanto nelle ore in cui una
        # pianta dovrebbe ricevere luce. Al crepuscolo/notte evitare falsi avvisi.
        if hour < 8 or hour >= 18:
            period = "notturna" if hour < 7 or hour >= 21 else "serale/mattutina"
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.OBSERVE.value,
                urgency=Urgency.LOW.value,
                confidence=0.75,
                factors=factors,
                message=f"Lettura luce di fascia {period} ({lux:.0f} lux). La luce verrà valutata nelle ore diurne.",
            )

        if lux < self.LUX_VERY_LOW:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.IMPROVE_LIGHT.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.6,  # un singolo punto non basta per valutare la luce giornaliera
                factors=factors,
                message=f"Luce molto bassa ({lux:.0f} lux). Verifica l'andamento durante la giornata.",
            )
        elif lux < self.LUX_LOW:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.IMPROVE_LIGHT.value,
                urgency=Urgency.LOW.value,
                confidence=0.5,
                factors=factors,
                message=f"Luce scarsa ({lux:.0f} lux). Potrebbe non bastare per piante esigenti.",
            )
        elif lux >= self.LUX_DIRECT_SUN:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.MONITOR_DIRECT_SUN.value,
                urgency=Urgency.MODERATE.value,
                confidence=0.7,
                factors=factors,
                message=f"Luce molto intensa ({lux:.0f} lux). Possibile sole diretto, monitorare la pianta.",
            )
        elif lux >= self.LUX_HIGH:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.MAINTAIN_LIGHT.value,
                urgency=Urgency.LOW.value,
                confidence=0.7,
                factors=factors,
                message=f"Buona esposizione luminosa ({lux:.0f} lux).",
            )
        else:
            return CareRecommendation(
                domain=CareDomain.LIGHT.value,
                action=CareAction.MAINTAIN_LIGHT.value,
                urgency=Urgency.LOW.value,
                confidence=0.65,
                factors=factors,
                message=f"Luce moderata ({lux:.0f} lux). Adeguata per la maggior parte delle piante.",
            )


# ── Care Engine (orchestratore) ──────────────────────────────

class CareEngine:
    """Orchestratore che esegue tutti i valutatori per una pianta."""

    def __init__(self):
        self._water = WaterEvaluator()
        self._temperature = TemperatureEvaluator()
        self._humidity = HumidityEvaluator()
        self._light = LightEvaluator()

    def evaluate_plant(
        self, plant_id: str, measurements: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Esegue tutti i valutatori per una pianta e restituisce le raccomandazioni."""
        now = datetime.now(timezone.utc)
        valid_from = now.isoformat()
        valid_until = (now + timedelta(hours=1)).isoformat()

        results: list[dict[str, Any]] = []
        critical_alerts: list[str] = []

        for evaluator, domain_name in [
            (self._water, "water"),
            (self._temperature, "temperature"),
            (self._humidity, "humidity"),
            (self._light, "light"),
        ]:
            recommendation = (
                evaluator.evaluate(measurements, evaluated_at=now)
                if domain_name == "light" else evaluator.evaluate(measurements)
            )
            recommendation.valid_from = valid_from
            recommendation.valid_until = valid_until
            results.append(recommendation.as_dict())

            # Raccogli le condizioni critiche per le notifiche
            if recommendation.urgency in (Urgency.CRITICAL.value, Urgency.HIGH.value):
                if recommendation.action != CareAction.INSUFFICIENT_DATA.value:
                    critical_alerts.append(recommendation.message)

        return {
            "plantID": plant_id,
            "evaluatedAt": valid_from,
            "recommendations": results,
            "criticalAlerts": critical_alerts,
            "hasCriticalConditions": len(critical_alerts) > 0,
        }

    def evaluate_all_plants(
        self, plants_data: dict[str, list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Valuta tutte le piante. plants_data: {plant_id: [misurazioni]}."""
        results = []
        for plant_id, measurements in plants_data.items():
            try:
                result = self.evaluate_plant(plant_id, measurements)
                results.append(result)
            except Exception as error:
                logger.error("Errore nella valutazione della pianta %s: %s", plant_id, error)
                results.append({
                    "plantID": plant_id,
                    "evaluatedAt": datetime.now(timezone.utc).isoformat(),
                    "recommendations": [],
                    "criticalAlerts": [],
                    "hasCriticalConditions": False,
                    "error": str(error),
                })
        return results
