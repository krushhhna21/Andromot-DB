CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id VARCHAR(128) NOT NULL UNIQUE,
    credential_hash VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled', 'revoked')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS readings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id UUID NOT NULL REFERENCES devices(id),
    external_event_id VARCHAR(128),
    idempotency_key VARCHAR(256) NOT NULL UNIQUE,
    schema_version VARCHAR(32) NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sensor_health JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_quality JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (device_id, external_event_id)
);

CREATE INDEX IF NOT EXISTS ix_readings_device_event
    ON readings (device_id, event_timestamp DESC);

CREATE TABLE IF NOT EXISTS measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reading_id UUID NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
    gas_type VARCHAR(128) NOT NULL,
    value NUMERIC(24, 8) NOT NULL,
    unit VARCHAR(64) NOT NULL,
    calibration_status VARCHAR(32) NOT NULL
        CHECK (calibration_status IN ('calibrated', 'uncalibrated', 'unknown')),
    measurement_status VARCHAR(32) NOT NULL
        CHECK (measurement_status IN ('valid', 'suspect', 'invalid')),
    sensor_channel VARCHAR(128)
);

CREATE TABLE IF NOT EXISTS environment_measurements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reading_id UUID NOT NULL REFERENCES readings(id) ON DELETE CASCADE,
    measurement_type VARCHAR(64) NOT NULL,
    value NUMERIC(24, 8) NOT NULL,
    unit VARCHAR(64) NOT NULL,
    measurement_status VARCHAR(32) NOT NULL
        CHECK (measurement_status IN ('valid', 'suspect', 'invalid'))
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reading_id UUID NOT NULL UNIQUE REFERENCES readings(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS ix_processing_jobs_pending
    ON processing_jobs (status, available_at);

-- Create a device after hashing its credential with the configured pepper.
-- The API expects: Authorization: Bearer <device_id>.<secret>
-- Store only HMAC-SHA256(DEVICE_CREDENTIAL_PEPPER, full bearer credential).
-- Example:
-- INSERT INTO devices (device_id, credential_hash) VALUES ('device-001', '<64-char-hash>');
