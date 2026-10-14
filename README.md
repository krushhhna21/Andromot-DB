# Andromot

Backend-first IoT gas monitoring platform.

The project is being implemented in stages. The current foundation provides:

- validated environment settings;
- a minimal FastAPI application;
- liveness and readiness endpoints;
- secret-safe request logging.
- `POST /api/v1/readings` for authenticated device ingestion.
- PostgreSQL schema in `sql/001_initial_schema.sql`.

Database persistence, device authentication, reading ingestion, deterministic
safety processing, Telegram notifications, and optional Groq summaries will be
added in later stages.

## Local development

1. Create a Python 3.11+ environment.
2. Install development dependencies:

   ```powershell
   pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env` and configure local values.
4. Start the API:

   ```powershell
   uvicorn andromot.main:app --reload
   ```

## PostgreSQL setup

Run the schema script against the target database:

```powershell
psql "$env:DATABASE_URL" -f sql/001_initial_schema.sql
```

Set `DATABASE_URL` in `.env` using the SQLAlchemy async format:

```text
postgresql+asyncpg://andromot:change-me@localhost:5432/andromot
```

Create a device credential outside PostgreSQL. The API expects the bearer
credential in the form `device-id.secret`. Store only the HMAC-SHA256 hash of
the complete credential, using `DEVICE_CREDENTIAL_PEPPER` as the HMAC key:

```python
import hashlib
import hmac

pepper = b"the-value-of-DEVICE_CREDENTIAL_PEPPER"
credential = b"device-001.replace-with-a-random-secret"
print(hmac.new(pepper, credential, hashlib.sha256).hexdigest())
```

Insert the resulting hash:

```sql
INSERT INTO devices (device_id, credential_hash)
VALUES ('device-001', '<64-character-hmac-sha256-hash>');
```

## Reading ingestion

Send a request using the full credential and a unique idempotency key:

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/readings `
  -H "Authorization: Bearer device-001.replace-with-a-random-secret" `
  -H "Idempotency-Key: event-device-001-0001" `
  -H "Content-Type: application/json" `
  --data-binary "@reading.json"
```

The API persists the reading, its measurements, optional environmental
measurements, and a pending processing job in one transaction. Reusing the
same idempotency key returns the original reading ID without inserting a
duplicate.

## Initial endpoints

- `GET /health/live` — process liveness.
- `GET /health/ready` — foundation readiness. Database connectivity will be
  included when the database layer is implemented.
