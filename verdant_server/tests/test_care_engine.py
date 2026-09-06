"""Test per il Care Engine deterministico."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from care_engine import (
    CareAction, CareEngine, CareDomain, Urgency,
    WaterEvaluator, TemperatureEvaluator, HumidityEvaluator, LightEvaluator,
)


# ── Utilità per creare misurazioni di test ─────────────────────

def make_measurement(kind: str, value: float, hours_ago: int = 0) -> dict:
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "id": 1,
        "entityID": f"sensor.test_{kind}",
        "plantID": "pianta-test",
        "kind": kind,
        "value": value,
        "unit": "%" if kind in ("moisture", "humidity") else "°C" if kind == "temperature" else "lux",
        "measuredAt": t.isoformat(),
        "source": "test",
        "reliability": 0.9,
        "createdAt": t.isoformat(),
    }


def make_moisture_series(values: list[float], interval_hours: int = 1) -> list[dict]:
    """Crea una serie temporale di moisture (ordinata per measuredAt DESC)."""
    return [make_measurement("moisture", v, i * interval_hours) for i, v in enumerate(values)]


# ── Test WaterEvaluator ───────────────────────────────────────

class TestWaterEvaluator:
    def setup_method(self):
        self.evaluator = WaterEvaluator()

    def test_nessun_dato(self):
        result = self.evaluator.evaluate([])
        assert result.action == CareAction.INSUFFICIENT_DATA.value
        assert result.confidence == 0.0

    def test_substrato_critico_secco(self):
        measurements = [make_measurement("moisture", 10.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.WATER_NOW.value
        assert result.urgency == Urgency.CRITICAL.value

    def test_substrato_asciutto(self):
        measurements = [make_measurement("moisture", 22.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.CHECK_SUBSTRATE.value
        assert result.urgency == Urgency.HIGH.value

    def test_substrato_troppo_umido(self):
        measurements = [make_measurement("moisture", 85.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.WAIT_TO_WATER.value
        assert result.urgency == Urgency.HIGH.value

    def test_substrato_umido(self):
        measurements = [make_measurement("moisture", 72.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.WAIT_TO_WATER.value
        assert result.urgency == Urgency.MODERATE.value

    def test_substrato_nella_norma(self):
        measurements = [make_measurement("moisture", 45.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.OBSERVE.value
        assert result.urgency == Urgency.LOW.value

    def test_trend_dry_down(self):
        # Serie: 50%, 48%, 46%, 44% (ogni ora) → trend negativo
        measurements = make_moisture_series([44.0, 46.0, 48.0, 50.0])
        result = self.evaluator.evaluate(measurements)
        # Deve avere il fattore trend
        trend_factors = [f for f in result.factors if f["code"] == "moisture_trend"]
        assert len(trend_factors) == 1
        assert trend_factors[0]["value"] < 0  # trend negativo (asciugatura)


# ── Test TemperatureEvaluator ─────────────────────────────────

class TestTemperatureEvaluator:
    def setup_method(self):
        self.evaluator = TemperatureEvaluator()

    def test_nessun_dato(self):
        result = self.evaluator.evaluate([])
        assert result.action == CareAction.INSUFFICIENT_DATA.value

    def test_rischio_gelata(self):
        measurements = [make_measurement("temperature", 3.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.PROTECT_FROM_COLD.value
        assert result.urgency == Urgency.CRITICAL.value

    def test_freddo(self):
        measurements = [make_measurement("temperature", 8.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.PROTECT_FROM_COLD.value
        assert result.urgency == Urgency.HIGH.value

    def test_calore_pericoloso(self):
        measurements = [make_measurement("temperature", 42.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.PROTECT_FROM_HEAT.value
        assert result.urgency == Urgency.CRITICAL.value

    def test_caldo(self):
        measurements = [make_measurement("temperature", 36.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.PROTECT_FROM_HEAT.value
        assert result.urgency == Urgency.MODERATE.value

    def test_temperatura_normale(self):
        measurements = [make_measurement("temperature", 22.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.OBSERVE.value
        assert result.urgency == Urgency.LOW.value


# ── Test HumidityEvaluator ────────────────────────────────────

class TestHumidityEvaluator:
    def setup_method(self):
        self.evaluator = HumidityEvaluator()

    def test_aria_molto_secca(self):
        measurements = [make_measurement("humidity", 25.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.INCREASE_HUMIDITY.value
        assert result.urgency == Urgency.HIGH.value

    def test_aria_secca(self):
        measurements = [make_measurement("humidity", 35.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.INCREASE_HUMIDITY.value
        assert result.urgency == Urgency.MODERATE.value

    def test_aria_umida(self):
        measurements = [make_measurement("humidity", 85.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.REDUCE_HUMIDITY.value

    def test_umidita_normale(self):
        measurements = [make_measurement("humidity", 55.0)]
        result = self.evaluator.evaluate(measurements)
        assert result.action == CareAction.OBSERVE.value


# ── Test LightEvaluator ──────────────────────────────────────

class TestLightEvaluator:
    def setup_method(self):
        self.evaluator = LightEvaluator()

    def test_luce_molto_bassa(self):
        measurements = [make_measurement("illuminance", 200.0)]
        from datetime import datetime, timezone
        result = self.evaluator.evaluate(measurements, datetime(2026, 8, 13, 11, tzinfo=timezone.utc))
        assert result.action == CareAction.IMPROVE_LIGHT.value

    def test_sole_diretto(self):
        measurements = [make_measurement("illuminance", 55000.0)]
        from datetime import datetime, timezone
        result = self.evaluator.evaluate(measurements, datetime(2026, 8, 13, 11, tzinfo=timezone.utc))
        assert result.action == CareAction.MONITOR_DIRECT_SUN.value

    def test_luce_buona(self):
        measurements = [make_measurement("illuminance", 10000.0)]
        from datetime import datetime, timezone
        result = self.evaluator.evaluate(measurements, datetime(2026, 8, 13, 11, tzinfo=timezone.utc))
        assert result.action == CareAction.MAINTAIN_LIGHT.value

    def test_luce_bassa_serale_non_generer_suggerimento(self):
        from datetime import datetime, timezone
        result = self.evaluator.evaluate(
            [make_measurement("illuminance", 20.0)],
            datetime(2026, 8, 13, 19, tzinfo=timezone.utc),  # 21:00 Europe/Rome
        )
        assert result.action == CareAction.OBSERVE.value


# ── Test CareEngine (orchestratore) ──────────────────────────

class TestCareEngine:
    def setup_method(self):
        self.engine = CareEngine()

    def test_valutazione_completa(self):
        measurements = [
            make_measurement("moisture", 20.0),
            make_measurement("temperature", 22.0),
            make_measurement("humidity", 55.0),
            make_measurement("illuminance", 5000.0),
        ]
        result = self.engine.evaluate_plant("pianta-1", measurements)
        assert result["plantID"] == "pianta-1"
        assert len(result["recommendations"]) == 4
        domains = [r["domain"] for r in result["recommendations"]]
        assert "water" in domains
        assert "temperature" in domains
        assert "humidity" in domains
        assert "light" in domains

    def test_condizione_critica_rilevata(self):
        measurements = [
            make_measurement("moisture", 5.0),  # Critico!
            make_measurement("temperature", 22.0),
        ]
        result = self.engine.evaluate_plant("pianta-1", measurements)
        assert result["hasCriticalConditions"] is True
        assert len(result["criticalAlerts"]) > 0

    def test_nessun_dato(self):
        result = self.engine.evaluate_plant("pianta-1", [])
        assert result["plantID"] == "pianta-1"
        assert len(result["recommendations"]) == 4
        # Tutti con insufficient_data
        for rec in result["recommendations"]:
            assert rec["action"] == CareAction.INSUFFICIENT_DATA.value

    def test_valutazione_multipla(self):
        plants_data = {
            "pianta-1": [make_measurement("moisture", 45.0)],
            "pianta-2": [make_measurement("temperature", 5.0)],
        }
        results = self.engine.evaluate_all_plants(plants_data)
        assert len(results) == 2

    def test_contratto_raccomandazioni_client(self):
        """Il payload pubblico mantiene il DTO consumato dal client Swift."""
        result = self.engine.evaluate_plant(
            "6E57D806-5D23-4E91-B8DA-319A68AAB01F",
            [make_measurement("moisture", 12.0)],
        )
        assert set(result) >= {
            "plantID", "evaluatedAt", "recommendations",
            "criticalAlerts", "hasCriticalConditions",
        }
        recommendation = result["recommendations"][0]
        assert set(recommendation) == {
            "domain", "action", "urgency", "confidence", "factors",
            "message", "validFrom", "validUntil",
        }
        assert recommendation["action"] == "water_now"
        assert recommendation["urgency"] == "critical"
        assert recommendation["validFrom"]
        assert recommendation["validUntil"]
