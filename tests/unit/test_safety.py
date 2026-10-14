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


def test_evaluate_target_gases_all_eight() -> None:
    """Verify thresholds for all 8 target gases (LPG, methane, propane, butane, hydrogen, alcohol, CO, smoke)."""
    reading = MagicMock(spec=Reading)
    reading.measurements = [
        MagicMock(spec=Measurement, gas_type="lpg", value=Decimal("1500.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-1", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="methane", value=Decimal("2600.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-2", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="propane", value=Decimal("1200.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-3", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="butane", value=Decimal("1900.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-4", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="hydrogen", value=Decimal("4500.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-5", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="alcohol", value=Decimal("600.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-6", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="carbomoxide", value=Decimal("110.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-7", calibration_status="calibrated"),
        MagicMock(spec=Measurement, gas_type="smoke", value=Decimal("250.0"), unit="ppm", measurement_status="valid", sensor_channel="ch-8", calibration_status="calibrated"),
    ]

    level, breaches = evaluate_reading_safety(reading)
    assert level == "CRITICAL"
    # All 8 should have breached their warning or critical limits
    assert len(breaches) == 8
    gas_breach_map = {b["gas_type"]: b["level"] for b in breaches}
    assert gas_breach_map["lpg"] == "WARNING"  # 1500 ppm >= 1000 ppm
    assert gas_breach_map["methane"] == "CRITICAL"  # 2600 ppm >= 2500 ppm
    assert gas_breach_map["propane"] == "WARNING"  # 1200 ppm >= 1000 ppm
    assert gas_breach_map["butane"] == "CRITICAL"  # 1900 ppm >= 1800 ppm
    assert gas_breach_map["hydrogen"] == "CRITICAL"  # 4500 ppm >= 4000 ppm
    assert gas_breach_map["alcohol"] == "CRITICAL"  # 600 ppm >= 500 ppm
    assert gas_breach_map["carbomoxide"] == "CRITICAL"  # 110 ppm >= 100 ppm
    assert gas_breach_map["smoke"] == "WARNING"  # 250 ppm >= 200 ppm

