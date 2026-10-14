from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MeasurementInput(BaseModel):
    gas_type: str = Field(min_length=1, max_length=128)
    value: Decimal
    unit: str = Field(default="ppm", min_length=1, max_length=64)
    calibration_status: Literal["calibrated", "uncalibrated", "unknown"] = "calibrated"
    measurement_status: Literal["valid", "suspect", "invalid"] = "valid"
    sensor_channel: str | None = Field(default=None, max_length=128)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("measurement value must be finite")
        return value


class EnvironmentInput(BaseModel):
    measurement_type: str = Field(min_length=1, max_length=64)
    value: Decimal
    unit: str = Field(min_length=1, max_length=64)
    measurement_status: Literal["valid", "suspect", "invalid"] = "valid"


class ReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    device_id: str = Field(min_length=1, max_length=128)
    external_event_id: str | None = Field(default=None, max_length=128)
    event_timestamp: datetime

    measurements: list[MeasurementInput] = Field(min_length=1, max_length=100)
    environment: list[EnvironmentInput] = Field(default_factory=list, max_length=50)
    sensor_health: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)


class ReadingAccepted(BaseModel):
    reading_id: UUID
    status: Literal["accepted"]
    duplicate: bool
    received_at: datetime
