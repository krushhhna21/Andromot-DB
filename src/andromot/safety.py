"""Deterministic safety rule and threshold evaluation engine for gas monitoring."""

from decimal import Decimal
from typing import Any

from andromot.models import Reading

# Default safety alarm thresholds: (warning_threshold, critical_threshold)
# Categorized by gas and unit (ppm vs %LEL)
UNIT_AWARE_THRESHOLDS: dict[tuple[str, str], tuple[Decimal, Decimal]] = {
    # Carbon Monoxide (ppm) - OSHA PEL 50 ppm, NIOSH ceiling 200 ppm
    ("carbon_monoxide", "ppm"): (Decimal("35.0"), Decimal("100.0")),
    ("co", "ppm"): (Decimal("35.0"), Decimal("100.0")),
    ("carbomoxide", "ppm"): (Decimal("35.0"), Decimal("100.0")),
    ("carbonmonoxide", "ppm"): (Decimal("35.0"), Decimal("100.0")),
    # LPG (Liquefied Petroleum Gas - Propane/Butane blend)
    ("lpg", "ppm"): (Decimal("1000.0"), Decimal("2000.0")),
    ("lpg", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("liquefied_petroleum_gas", "ppm"): (Decimal("1000.0"), Decimal("2000.0")),
    ("liquefied_petroleum_gas", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Methane (CH4) - LEL is 5.0% (50,000 ppm)
    ("methane", "ppm"): (Decimal("1000.0"), Decimal("2500.0")),
    ("methane", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("ch4", "ppm"): (Decimal("1000.0"), Decimal("2500.0")),
    ("ch4", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Propane (C3H8) - LEL is 2.1% (21,000 ppm)
    ("propane", "ppm"): (Decimal("1000.0"), Decimal("2000.0")),
    ("propane", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("c3h8", "ppm"): (Decimal("1000.0"), Decimal("2000.0")),
    ("c3h8", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Butane (C4H10) - LEL is 1.8% (18,000 ppm)
    ("butane", "ppm"): (Decimal("1000.0"), Decimal("1800.0")),
    ("butane", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("c4h10", "ppm"): (Decimal("1000.0"), Decimal("1800.0")),
    ("c4h10", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Hydrogen (H2) - LEL is 4.0% (40,000 ppm)
    ("hydrogen", "ppm"): (Decimal("1000.0"), Decimal("4000.0")),
    ("hydrogen", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("h2", "ppm"): (Decimal("1000.0"), Decimal("4000.0")),
    ("h2", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Alcohol / Ethanol (VOC vapor)
    ("alcohol", "ppm"): (Decimal("200.0"), Decimal("500.0")),
    ("alcohol", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("ethanol", "ppm"): (Decimal("200.0"), Decimal("500.0")),
    ("ethanol", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    # Smoke (obscuration index / particulate concentration)
    ("smoke", "ppm"): (Decimal("200.0"), Decimal("400.0")),
    ("smoke", "index"): (Decimal("50.0"), Decimal("100.0")),
    # General combustible gas
    ("combustible_gas", "%lel"): (Decimal("10.0"), Decimal("20.0")),
    ("combustible_gas", "ppm"): (Decimal("1000.0"), Decimal("2000.0")),
    # Hydrogen Sulfide (ppm)
    ("hydrogen_sulfide", "ppm"): (Decimal("10.0"), Decimal("20.0")),
    ("h2s", "ppm"): (Decimal("10.0"), Decimal("20.0")),
    # Nitrogen Dioxide (ppm)
    ("nitrogen_dioxide", "ppm"): (Decimal("1.0"), Decimal("5.0")),
    ("no2", "ppm"): (Decimal("1.0"), Decimal("5.0")),
    # Ammonia (ppm)
    ("ammonia", "ppm"): (Decimal("25.0"), Decimal("50.0")),
    ("nh3", "ppm"): (Decimal("25.0"), Decimal("50.0")),
}

# Fallback thresholds when unit is unspecified
DEFAULT_GAS_THRESHOLDS: dict[str, tuple[Decimal, Decimal]] = {
    "carbon_monoxide": (Decimal("35.0"), Decimal("100.0")),
    "co": (Decimal("35.0"), Decimal("100.0")),
    "carbomoxide": (Decimal("35.0"), Decimal("100.0")),
    "carbonmonoxide": (Decimal("35.0"), Decimal("100.0")),
    "lpg": (Decimal("10.0"), Decimal("20.0")),
    "methane": (Decimal("10.0"), Decimal("20.0")),
    "propane": (Decimal("10.0"), Decimal("20.0")),
    "butane": (Decimal("10.0"), Decimal("20.0")),
    "hydrogen": (Decimal("10.0"), Decimal("20.0")),
    "alcohol": (Decimal("200.0"), Decimal("500.0")),
    "ethanol": (Decimal("200.0"), Decimal("500.0")),
    "smoke": (Decimal("200.0"), Decimal("400.0")),
    "combustible_gas": (Decimal("10.0"), Decimal("20.0")),
    "hydrogen_sulfide": (Decimal("10.0"), Decimal("20.0")),
    "h2s": (Decimal("10.0"), Decimal("20.0")),
    "nitrogen_dioxide": (Decimal("1.0"), Decimal("5.0")),
    "no2": (Decimal("1.0"), Decimal("5.0")),
    "ammonia": (Decimal("25.0"), Decimal("50.0")),
    "nh3": (Decimal("25.0"), Decimal("50.0")),
}

GAS_THRESHOLDS = DEFAULT_GAS_THRESHOLDS


def normalize_unit_category(unit: str) -> str:
    """Normalize unit into either %lel, index, or ppm."""
    u = unit.lower().strip()
    if "%" in u or "lel" in u:
        return "%lel"
    if "index" in u:
        return "index"
    return "ppm"


def get_gas_thresholds(gas_key: str, unit: str) -> tuple[Decimal, Decimal] | None:
    """Look up warning and critical thresholds by gas type and measurement unit."""
    cat = normalize_unit_category(unit)
    if (gas_key, cat) in UNIT_AWARE_THRESHOLDS:
        return UNIT_AWARE_THRESHOLDS[(gas_key, cat)]
    return DEFAULT_GAS_THRESHOLDS.get(gas_key)


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
        thresholds = get_gas_thresholds(gas_key, m.unit)
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

