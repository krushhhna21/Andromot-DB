"""Deterministic safety rule and threshold evaluation engine for gas monitoring."""

from decimal import Decimal
from typing import Any

from andromot.models import Reading

# Default safety alarm thresholds: (warning_threshold, critical_threshold)
GAS_THRESHOLDS: dict[str, tuple[Decimal, Decimal]] = {
    # Carbon Monoxide (ppm) - OSHA PEL is 50 ppm, NIOSH ceiling is 200 ppm
    "carbon_monoxide": (Decimal("35.0"), Decimal("100.0")),
    "co": (Decimal("35.0"), Decimal("100.0")),
    # Methane / Flammable gases (% LEL - Lower Explosive Limit)
    "methane": (Decimal("10.0"), Decimal("20.0")),
    "lpg": (Decimal("10.0"), Decimal("20.0")),
    "combustible_gas": (Decimal("10.0"), Decimal("20.0")),
    # Hydrogen Sulfide (ppm)
    "hydrogen_sulfide": (Decimal("10.0"), Decimal("20.0")),
    "h2s": (Decimal("10.0"), Decimal("20.0")),
    # Nitrogen Dioxide (ppm)
    "nitrogen_dioxide": (Decimal("1.0"), Decimal("5.0")),
    "no2": (Decimal("1.0"), Decimal("5.0")),
    # Ammonia (ppm)
    "ammonia": (Decimal("25.0"), Decimal("50.0")),
    "nh3": (Decimal("25.0"), Decimal("50.0")),
}


def evaluate_reading_safety(reading: Reading) -> tuple[str, list[dict[str, Any]]]:
    """Evaluate all measurements against safety rules.

    Returns:
        tuple of (overall_level, list_of_breached_measurements)
        where overall_level is 'NORMAL', 'WARNING', or 'CRITICAL'.
    """
    highest_level = "NORMAL"
    breaches: list[dict[str, Any]] = []

    for m in reading.measurements:
        if m.measurement_status == "invalid":
            continue

        gas_key = m.gas_type.lower().strip().replace("-", "_").replace(" ", "_")
        thresholds = GAS_THRESHOLDS.get(gas_key)
        if thresholds is None:
            continue

        warning_thresh, critical_thresh = thresholds
        val = Decimal(str(m.value))

        if val >= critical_thresh:
            breach_level = "CRITICAL"
        elif val >= warning_thresh:
            breach_level = "WARNING"
        else:
            continue

        if breach_level == "CRITICAL" or highest_level == "NORMAL":
            highest_level = breach_level

        breaches.append(
            {
                "gas_type": m.gas_type,
                "value": float(val),
                "unit": m.unit,
                "level": breach_level,
                "sensor_channel": m.sensor_channel,
                "calibration_status": m.calibration_status,
            }
        )

    return highest_level, breaches
