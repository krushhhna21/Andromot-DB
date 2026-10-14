from decimal import Decimal
from unittest.mock import MagicMock

from andromot.models import Measurement, Reading
from andromot.safety import evaluate_reading_safety


def test_evaluate_reading_safety_normal() -> None:
    reading = MagicMock(spec=Reading)
    reading.measurements = [
        MagicMock(spec=Measurement, gas_type="carbon_monoxide", value=Decimal("12.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-1", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="methane", value=Decimal("2.5"), unit="%LEL", measurement_status="valid", sensor_channel="ch-2", calibration_status="calibrated"),
    ]

    level, breaches = evaluate_reading_safety(reading)
    assert level == "NORMAL"
    assert len(breaches) == 0


def test_evaluate_reading_safety_warning() -> None:
    reading = MagicMock(spec=Reading)
    reading.measurements = [
        MagicMock(spec=Measurement, gas_type="carbon_monoxide", value=Decimal("45.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-1", calibration_status="calibrated"),
    ]

    level, breaches = evaluate_reading_safety(reading)
    assert level == "WARNING"
    assert len(breaches) == 1
    assert breaches[0]["gas_type"] == "carbon_monoxide"
    assert breaches[0]["level"] == "WARNING"


def test_evaluate_reading_safety_critical() -> None:
    reading = MagicMock(spec=Reading)
    reading.measurements = [
        MagicMock(spec=Measurement, gas_type="carbon_monoxide", value=Decimal("150.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-1", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="methane", value=Decimal("25.0"), unit="%LEL", measurement_status="valid", sensor_channel="ch-2", calibration_status="calibrated"),
    ]

    level, breaches = evaluate_reading_safety(reading)
    assert level == "CRITICAL"
    assert len(breaches) == 2
    assert all(b["level"] == "CRITICAL" for b in breaches)


def test_evaluate_reading_safety_ignores_invalid_measurements() -> None:
    reading = MagicMock(spec=Reading)
    reading.measurements = [
        MagicMock(spec=Measurement, gas_type="carbon_monoxide", value=Decimal("999.0"), unit="ppm", measurement_status="invalid", sensor_channel="ch-1", calibration_status="calibrated"),
    ]

    level, breaches = evaluate_reading_safety(reading)
    assert level == "NORMAL"
    assert len(breaches) == 0
