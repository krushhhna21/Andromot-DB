from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    credential_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reading(Base):
    __tablename__ = "readings"
    __table_args__ = (UniqueConstraint("device_id", "external_event_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    external_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    schema_version: Mapped[str] = mapped_column(String(32))
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sensor_health: Mapped[dict] = mapped_column(JSONB, default=dict)
    data_quality: Mapped[dict] = mapped_column(JSONB, default=dict)
    measurements: Mapped[list["Measurement"]] = relationship(cascade="all, delete-orphan")
    environment_measurements: Mapped[list["EnvironmentMeasurement"]] = relationship(cascade="all, delete-orphan")
    processing_job: Mapped["ProcessingJob | None"] = relationship(cascade="all, delete-orphan", uselist=False)


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reading_id: Mapped[UUID] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), index=True)
    gas_type: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8))

    unit: Mapped[str] = mapped_column(String(64))
    calibration_status: Mapped[str] = mapped_column(String(32))
    measurement_status: Mapped[str] = mapped_column(String(32))
    sensor_channel: Mapped[str | None] = mapped_column(String(128), nullable=True)


class EnvironmentMeasurement(Base):
    __tablename__ = "environment_measurements"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reading_id: Mapped[UUID] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), index=True)
    measurement_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    unit: Mapped[str] = mapped_column(String(64))
    measurement_status: Mapped[str] = mapped_column(String(32))


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    reading_id: Mapped[UUID] = mapped_column(ForeignKey("readings.id", ondelete="CASCADE"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceSubscriber(Base):
    __tablename__ = "device_subscribers"
    __table_args__ = (UniqueConstraint("device_id", "telegram_chat_id"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), index=True)
    user_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

