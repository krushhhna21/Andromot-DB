from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from andromot.auth import hash_device_credential
from andromot.db import get_db_session
from andromot.main import app
from andromot.models import Device, Reading


@pytest.fixture
def test_device() -> Device:
    device = Device(
        id=uuid4(),
        device_id="device-001",
        credential_hash=hash_device_credential("device-001.test-secret"),
        status="active",
    )
    return device


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer device-001.test-secret",
        "Idempotency-Key": f"test-key-{uuid4()}",
    }


@pytest.fixture
def valid_payload() -> dict:
    return {
        "schema_version": "1.0",
        "device_id": "device-001",
        "external_event_id": f"evt-{uuid4()}",
        "event_timestamp": datetime.now(UTC).isoformat(),
        "measurements": [
            {
                "gas_type": "carbon_monoxide",
                "value": 12.4,
                "unit": "ppm",
                "calibration_status": "calibrated",
                "measurement_status": "valid",
                "sensor_channel": "gas-1",
            }
        ],
        "environment": [
            {
                "measurement_type": "temperature",
                "value": 24.5,
                "unit": "celsius",
                "measurement_status": "valid",
            }
        ],
        "sensor_health": {
            "status": "healthy",
            "battery_percent": 90,
        },
        "data_quality": {
            "overall": "good",
            "flags": [],
        },
    }


class FakeAsyncSession:
    """In-memory AsyncSession test double for deterministic testing."""

    def __init__(self, devices: list[Device] | None = None, readings: list[Reading] | None = None):
        self.devices = {d.device_id: d for d in (devices or [])}
        self.readings_by_key = {r.idempotency_key: r for r in (readings or [])}
        self.readings_by_device_event = {
            (r.device_id, r.external_event_id): r
            for r in (readings or [])
            if r.external_event_id is not None
        }
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.simulated_integrity_error = None

    async def scalar(self, statement):
        params = statement.compile().params
        # Query Reading by (device_id, external_event_id)
        if any("external_event_id" in k for k in params):
            dev_id = next(v for k, v in params.items() if "device_id" in k)
            ext_id = next(v for k, v in params.items() if "external_event_id" in k)
            return self.readings_by_device_event.get((dev_id, ext_id))
        # Query Reading by idempotency_key
        if any("idempotency_key" in k for k in params):
            key = next(v for k, v in params.items() if "idempotency_key" in k)
            return self.readings_by_key.get(key)
        # Query Device by device_id
        if any("device_id" in k for k in params):
            dev_str = next(v for k, v in params.items() if "device_id" in k)
            return self.devices.get(dev_str)
        return None

    def add(self, instance):
        self.added.append(instance)
        if isinstance(instance, Reading):
            instance.received_at = instance.received_at or datetime.now(UTC)

    def add_all(self, instances):
        self.added.extend(instances)

    async def commit(self):
        if self.simulated_integrity_error:
            raise self.simulated_integrity_error
        for item in self.added:
            if isinstance(item, Reading):
                if item.idempotency_key in self.readings_by_key:
                    raise IntegrityError("duplicate idempotency_key", params={}, orig=Exception("unique constraint"))
                if item.external_event_id and (item.device_id, item.external_event_id) in self.readings_by_device_event:
                    raise IntegrityError("duplicate external_event_id", params={}, orig=Exception("unique constraint"))
                self.readings_by_key[item.idempotency_key] = item
                if item.external_event_id:
                    self.readings_by_device_event[(item.device_id, item.external_event_id)] = item
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, instance):
        if isinstance(instance, Reading) and instance.received_at is None:
            instance.received_at = datetime.now(UTC)


def test_new_reading_success(test_device: Device, valid_payload: dict, auth_headers: dict) -> None:
    session = FakeAsyncSession(devices=[test_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=auth_headers)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["duplicate"] is False
        assert "reading_id" in data
        assert "received_at" in data
        assert session.committed is True

        # Verify reading and children were added
        added_readings = [item for item in session.added if isinstance(item, Reading)]
        assert len(added_readings) == 1
        reading = added_readings[0]
        assert reading.device_id == test_device.id
        assert reading.external_event_id == valid_payload["external_event_id"]
        assert len(reading.measurements) == 1
        assert reading.measurements[0].gas_type == "carbon_monoxide"
        assert reading.measurements[0].value == Decimal("12.4")
        assert len(reading.environment_measurements) == 1
        assert reading.processing_job is not None
        assert reading.processing_job.status == "pending"
    finally:
        app.dependency_overrides.clear()


def test_duplicate_idempotency_key_returns_accepted(test_device: Device, valid_payload: dict) -> None:
    existing_id = uuid4()
    existing_reading = Reading(
        id=existing_id,
        device_id=test_device.id,
        external_event_id="existing-event",
        idempotency_key="idempotent-key-1",
        schema_version="1.0",
        event_timestamp=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    session = FakeAsyncSession(devices=[test_device], readings=[existing_reading])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        headers = {
            "Authorization": "Bearer device-001.test-secret",
            "Idempotency-Key": "idempotent-key-1",
        }
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=headers)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["duplicate"] is True
        assert data["reading_id"] == str(existing_id)
    finally:
        app.dependency_overrides.clear()


def test_duplicate_idempotency_key_different_device_conflict(test_device: Device, valid_payload: dict) -> None:
    other_device_id = uuid4()
    existing_reading = Reading(
        id=uuid4(),
        device_id=other_device_id,
        external_event_id="other-event",
        idempotency_key="shared-key",
        schema_version="1.0",
        event_timestamp=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    session = FakeAsyncSession(devices=[test_device], readings=[existing_reading])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        headers = {
            "Authorization": "Bearer device-001.test-secret",
            "Idempotency-Key": "shared-key",
        }
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=headers)

        assert response.status_code == 409
        assert response.json()["detail"] == "idempotency key belongs to another device"
    finally:
        app.dependency_overrides.clear()


def test_duplicate_external_event_conflict(test_device: Device, valid_payload: dict) -> None:
    existing_reading = Reading(
        id=uuid4(),
        device_id=test_device.id,
        external_event_id="event-dup",
        idempotency_key="key-1",
        schema_version="1.0",
        event_timestamp=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )
    session = FakeAsyncSession(devices=[test_device], readings=[existing_reading])
    session.simulated_integrity_error = IntegrityError(
        "duplicate key value violates unique constraint readings_device_id_external_event_id_key",
        params={},
        orig=Exception("unique constraint"),
    )

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        payload = dict(valid_payload)
        payload["external_event_id"] = "event-dup"
        headers = {
            "Authorization": "Bearer device-001.test-secret",
            "Idempotency-Key": "key-2",
        }
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=payload, headers=headers)

        assert response.status_code == 409
        assert response.json()["detail"] == "external_event_id already exists for this device"
        assert session.rolled_back is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("auth_header", "expected_status", "expected_detail"),
    [
        (None, 401, "device authentication required"),
        ("Basic dXNlcjpwYXNz", 401, "device authentication required"),
        ("Bearer unknown-device.secret", 401, "invalid device credentials"),
        ("Bearer device-001.wrong-secret", 401, "invalid device credentials"),
    ],
)
def test_invalid_device_credentials(
    test_device: Device,
    valid_payload: dict,
    auth_header: str | None,
    expected_status: int,
    expected_detail: str,
) -> None:
    session = FakeAsyncSession(devices=[test_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        headers = {"Idempotency-Key": "key-123"}
        if auth_header is not None:
            headers["Authorization"] = auth_header

        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=headers)

        assert response.status_code == expected_status
        assert response.json()["detail"] == expected_detail
    finally:
        app.dependency_overrides.clear()


def test_inactive_device_rejected(valid_payload: dict, auth_headers: dict) -> None:
    disabled_device = Device(
        id=uuid4(),
        device_id="device-001",
        credential_hash=hash_device_credential("device-001.test-secret"),
        status="disabled",
    )
    session = FakeAsyncSession(devices=[disabled_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=auth_headers)

        assert response.status_code == 401
        assert response.json()["detail"] == "invalid device credentials"
    finally:
        app.dependency_overrides.clear()


def test_device_id_mismatch_payload_forbidden(test_device: Device, valid_payload: dict, auth_headers: dict) -> None:
    session = FakeAsyncSession(devices=[test_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        payload = dict(valid_payload)
        payload["device_id"] = "device-999"  # Mismatched device id

        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=payload, headers=auth_headers)

        assert response.status_code == 403
        assert response.json()["detail"] == "payload device_id does not match authenticated device"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "payload_modifier",
    [
        lambda p: p.update({"schema_version": "2.0"}),
        lambda p: p.update({"event_timestamp": "2026-10-14T07:59:00"}),  # missing timezone
        lambda p: p.update({"measurements": []}),  # empty measurements
        lambda p: p["measurements"][0].update({"value": "NaN"}),  # non-finite value
        lambda p: p["measurements"][0].update({"calibration_status": "invalid_status"}),
        lambda p: p["measurements"][0].update({"measurement_status": "unknown_status"}),
        lambda p: p.update({"unrecognized_extra_field": "disallowed"}),
    ],
)
def test_invalid_payload(test_device: Device, valid_payload: dict, auth_headers: dict, payload_modifier) -> None:
    session = FakeAsyncSession(devices=[test_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        payload = dict(valid_payload)
        payload_modifier(payload)

        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=payload, headers=auth_headers)

        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_database_rollback_on_commit_failure(test_device: Device, valid_payload: dict, auth_headers: dict) -> None:
    session = FakeAsyncSession(devices=[test_device])
    session.simulated_integrity_error = IntegrityError(
        "simulated commit integrity error",
        params={},
        orig=Exception("database failure"),
    )

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=valid_payload, headers=auth_headers)

        assert response.status_code == 409
        assert response.json()["detail"] == "reading conflicts with an existing event"
        assert session.rolled_back is True
    finally:
        app.dependency_overrides.clear()


def test_list_target_gases() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/gases")

    assert response.status_code == 200
    data = response.json()
    assert "target_gases" in data
    assert len(data["target_gases"]) == 8
    keys = [g["key"] for g in data["target_gases"]]
    assert "lpg" in keys
    assert "methane" in keys
    assert "propane" in keys
    assert "butane" in keys
    assert "hydrogen" in keys
    assert "alcohol" in keys
    assert "carbon_monoxide" in keys
    assert "smoke" in keys


def test_ingest_all_eight_target_gases_minimal(test_device: Device) -> None:
    session = FakeAsyncSession(devices=[test_device])

    async def override_get_db_session():
        yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        payload = {
            "device_id": "device-001",
            "event_timestamp": datetime.now(UTC).isoformat(),
            "measurements": [
                {"gas_type": "lpg", "value": 450.0},
                {"gas_type": "methane", "value": 350.0},
                {"gas_type": "propane", "value": 280.0},
                {"gas_type": "butane", "value": 210.0},
                {"gas_type": "hydrogen", "value": 150.0},
                {"gas_type": "alcohol", "value": 45.0},
                {"gas_type": "carbon_monoxide", "value": 8.5},
                {"gas_type": "smoke", "value": 60.0},
            ],
        }
        headers = {"Authorization": "Bearer device-001.test-secret"}
        with TestClient(app) as client:
            response = client.post("/api/v1/readings", json=payload, headers=headers)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["duplicate"] is False
        assert len(session.added) == 1
        r = session.added[0]
        assert len(r.measurements) == 8
        assert all(m.calibration_status == "calibrated" for m in r.measurements)
        assert all(m.measurement_status == "valid" for m in r.measurements)
        assert all(m.unit == "ppm" for m in r.measurements)
    finally:
        app.dependency_overrides.clear()

