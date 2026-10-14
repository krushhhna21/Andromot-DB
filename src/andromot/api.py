from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import authenticate_device
from .db import get_db_session
from .models import Device, EnvironmentMeasurement, Measurement, ProcessingJob, Reading
from .schemas import ReadingAccepted, ReadingInput

router = APIRouter(prefix="/api/v1", tags=["readings"])


@router.post("/readings", response_model=ReadingAccepted, status_code=status.HTTP_202_ACCEPTED)
async def ingest_reading(
    payload: ReadingInput,
    idempotency_key: Annotated[
        str, Header(..., alias="Idempotency-Key", min_length=1, max_length=256)
    ],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    device: Annotated[Device, Depends(authenticate_device)],
) -> ReadingAccepted:
    device_db_id = device.id
    device_str_id = device.device_id

    if payload.device_id != device_str_id:
        raise HTTPException(status_code=403, detail="payload device_id does not match authenticated device")
    if payload.event_timestamp.tzinfo is None:
        raise HTTPException(status_code=422, detail="event_timestamp must include timezone")

    existing = await session.scalar(select(Reading).where(Reading.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.device_id != device_db_id:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another device")
        received_at = existing.received_at or datetime.now(UTC)
        return ReadingAccepted(reading_id=existing.id, status="accepted", duplicate=True, received_at=received_at)

    reading = Reading(
        id=uuid4(),
        device_id=device_db_id,
        external_event_id=payload.external_event_id,
        idempotency_key=idempotency_key,
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
        existing = await session.scalar(select(Reading).where(Reading.idempotency_key == idempotency_key))
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
