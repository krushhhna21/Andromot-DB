from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .auth import authenticate_device
from .db import get_db_session
from .models import Device, EnvironmentMeasurement, Measurement, ProcessingJob, Reading
from .schemas import ReadingAccepted, ReadingInput

router = APIRouter(prefix="/api/v1", tags=["readings"])


@router.get("/gases", tags=["gases"])
async def list_target_gases() -> dict[str, Any]:
    """Return the catalogue of 8 monitored hazardous gases and their alarm thresholds."""
    return {
        "target_gases": [
            {
                "name": "Carbon Monoxide",
                "key": "carbon_monoxide",
                "formula": "CO",
                "warning_threshold": 35.0,
                "critical_threshold": 100.0,
                "unit": "ppm",
                "hazard": "Toxic chemical asphyxiant",
            },
            {
                "name": "LPG (Liquefied Petroleum Gas)",
                "key": "lpg",
                "formula": "C3H8/C4H10",
                "warning_threshold": 1000.0,
                "critical_threshold": 2000.0,
                "unit": "ppm",
                "hazard": "Explosion / Flammability (10-20% LEL)",
            },
            {
                "name": "Methane",
                "key": "methane",
                "formula": "CH4",
                "warning_threshold": 1000.0,
                "critical_threshold": 2500.0,
                "unit": "ppm",
                "hazard": "Flammable hydrocarbon (10-20% LEL)",
            },
            {
                "name": "Propane",
                "key": "propane",
                "formula": "C3H8",
                "warning_threshold": 1000.0,
                "critical_threshold": 2000.0,
                "unit": "ppm",
                "hazard": "Dense flammable gas / Floor pooling hazard",
            },
            {
                "name": "Butane",
                "key": "butane",
                "formula": "C4H10",
                "warning_threshold": 1000.0,
                "critical_threshold": 1800.0,
                "unit": "ppm",
                "hazard": "Dense flammable gas / Floor pooling hazard",
            },
            {
                "name": "Hydrogen",
                "key": "hydrogen",
                "formula": "H2",
                "warning_threshold": 1000.0,
                "critical_threshold": 4000.0,
                "unit": "ppm",
                "hazard": "Extreme explosion hazard / Fast flame propagation",
            },
            {
                "name": "Alcohol / Ethanol",
                "key": "alcohol",
                "formula": "C2H5OH",
                "warning_threshold": 200.0,
                "critical_threshold": 500.0,
                "unit": "ppm",
                "hazard": "Flammable VOC vapor",
            },
            {
                "name": "Smoke",
                "key": "smoke",
                "formula": None,
                "warning_threshold": 200.0,
                "critical_threshold": 400.0,
                "unit": "ppm",
                "hazard": "Combustion particulates / Fire outbreak",
            },
        ]
    }


@router.get("/readings/latest", tags=["readings"])
async def get_latest_reading(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    device_id: str | None = Query(None, description="Optional device filter"),
) -> dict[str, Any]:
    """Retrieve the most recent gas and environmental telemetry recorded in the platform."""
    stmt = (
        select(Reading)
        .options(
            selectinload(Reading.measurements),
            selectinload(Reading.environment_measurements),
        )
        .order_by(Reading.event_timestamp.desc())
    )
    if device_id:
        dev = await session.scalar(select(Device).where(Device.device_id == device_id))
        if dev is None:
            raise HTTPException(status_code=404, detail=f"device '{device_id}' not found")
        stmt = stmt.where(Reading.device_id == dev.id)

    stmt = stmt.limit(1)
    reading = (await session.execute(stmt)).scalar_one_or_none()
    if reading is None:
        raise HTTPException(status_code=404, detail="no readings found")

    device = await session.get(Device, reading.device_id)
    device_str = device.device_id if device else str(reading.device_id)

    return {
        "id": str(reading.id),
        "device_id": device_str,
        "external_event_id": reading.external_event_id,
        "event_timestamp": reading.event_timestamp.isoformat(),
        "received_at": reading.received_at.isoformat() if reading.received_at else None,
        "measurements": [
            {
                "gas_type": m.gas_type,
                "value": float(m.value),
                "unit": m.unit,
                "calibration_status": m.calibration_status,
                "measurement_status": m.measurement_status,
                "sensor_channel": m.sensor_channel,
            }
            for m in reading.measurements
        ],
        "environment": [
            {
                "measurement_type": em.measurement_type,
                "value": float(em.value),
                "unit": em.unit,
                "measurement_status": em.measurement_status,
            }
            for em in reading.environment_measurements
        ],
        "sensor_health": reading.sensor_health,
        "data_quality": reading.data_quality,
    }


@router.post("/readings", response_model=ReadingAccepted, status_code=status.HTTP_202_ACCEPTED)
async def ingest_reading(
    payload: ReadingInput,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    device: Annotated[Device, Depends(authenticate_device)],
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=256)
    ] = None,
) -> ReadingAccepted:
    device_db_id = device.id
    device_str_id = device.device_id

    if payload.device_id != device_str_id:
        raise HTTPException(status_code=403, detail="payload device_id does not match authenticated device")
    if payload.event_timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="event_timestamp must include timezone")

    eff_key = (
        idempotency_key
        or (f"{device_str_id}:{payload.external_event_id}" if payload.external_event_id else f"{device_str_id}:{payload.event_timestamp.isoformat()}:{uuid4()}")
    )

    existing = await session.scalar(select(Reading).where(Reading.idempotency_key == eff_key))
    if existing is not None:
        if existing.device_id != device_db_id:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another device")
        received_at = existing.received_at or datetime.now(UTC)
        return ReadingAccepted(reading_id=existing.id, status="accepted", duplicate=True, received_at=received_at)

    reading = Reading(
        id=uuid4(),
        device_id=device_db_id,
        external_event_id=payload.external_event_id,
        idempotency_key=eff_key,

        schema_version=payload.schema_version,
        event_timestamp=payload.event_timestamp,
        sensor_health=payload.sensor_health,
        data_quality=payload.data_quality,
        measurements=[
            Measurement(
                id=uuid4(),
                gas_type=item.gas_type,
                value=item.value,
                unit=item.unit,
                calibration_status=item.calibration_status,
                measurement_status=item.measurement_status,
                sensor_channel=item.sensor_channel,
            )
            for item in payload.measurements
        ],
        environment_measurements=[
            EnvironmentMeasurement(
                id=uuid4(),
                measurement_type=item.measurement_type,
                value=item.value,
                unit=item.unit,
                measurement_status=item.measurement_status,
            )
            for item in payload.environment
        ],
        processing_job=ProcessingJob(id=uuid4(), status="pending"),
    )
    session.add(reading)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        existing = await session.scalar(select(Reading).where(Reading.idempotency_key == eff_key))

        if existing is None:
            existing = await session.scalar(
                select(Reading).where(
                    Reading.device_id == device_db_id,
                    Reading.external_event_id == payload.external_event_id,
                )
            )
            if existing is not None:
                raise HTTPException(
                    status_code=409,
                    detail="external_event_id already exists for this device",
                ) from error
            raise HTTPException(status_code=409, detail="reading conflicts with an existing event") from error
        return ReadingAccepted(
            reading_id=existing.id,
            status="accepted",
            duplicate=True,
            received_at=existing.received_at or datetime.now(UTC),
        )
    await session.refresh(reading)
    return ReadingAccepted(
        reading_id=reading.id,
        status="accepted",
        duplicate=False,
        received_at=reading.received_at or datetime.now(UTC),
    )
