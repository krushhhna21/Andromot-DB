"""Transaction-safe processing-job worker primitives."""

import argparse
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .ai import generate_incident_summary
from .config import get_settings
from .db import get_session_factory
from .models import Device, DeviceSubscriber, ProcessingJob, Reading
from .safety import evaluate_reading_safety
from .telegram import format_alert_message, send_telegram_message

logger = logging.getLogger(__name__)

JobProcessor = Callable[[UUID], Awaitable[None]]


async def default_processor(reading_id: UUID) -> None:
    """Default processor: evaluates safety rules, generates AI summary on alarm, dispatches Telegram alert."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(Reading).options(selectinload(Reading.measurements)).where(Reading.id == reading_id)
        )
        reading = result.scalar_one_or_none()
        if reading is None:
            raise ValueError(f"Reading {reading_id} not found")

        level, breaches = evaluate_reading_safety(reading)
        logger.info(
            "Evaluated reading %s (device %s): safety level=%s, breaches=%d",
            reading.id,
            reading.device_id,
            level,
            len(breaches),
        )

        if level in ("WARNING", "CRITICAL"):
            device = await session.get(Device, reading.device_id)
            device_str_id = device.device_id if device else str(reading.device_id)

            ai_summary = await generate_incident_summary(
                device_id=device_str_id,
                level=level,
                measurements=breaches or reading.measurements,
            )
            alert_text = format_alert_message(
                device_id=device_str_id,
                level=level,
                measurements=reading.measurements,
                event_timestamp=reading.event_timestamp,
                sensor_health=reading.sensor_health,
                summary=ai_summary,
            )

            # Query all dynamic subscribers for this device
            subscribers_stmt = select(DeviceSubscriber.telegram_chat_id).where(
                DeviceSubscriber.device_id == device_str_id
            )
            subs = (await session.execute(subscribers_stmt)).scalars().all()

            recipients = {s.strip() for s in subs if s and s.strip()}
            settings = get_settings()
            if settings.telegram_chat_id:
                recipients.add(str(settings.telegram_chat_id).strip())

            if not recipients:
                logger.warning("No Telegram recipients found for device %s alert", device_str_id)
            else:
                for target_chat in recipients:
                    try:
                        await send_telegram_message(alert_text, chat_id=target_chat)
                    except Exception as exc:
                        logger.error("Failed to send alert to Telegram chat %s: %s", target_chat, exc)



async def claim_pending_job(session: AsyncSession) -> ProcessingJob | None:
    """Claim one available job without allowing concurrent workers to duplicate it."""
    query = (
        select(ProcessingJob)
        .where(ProcessingJob.status == "pending", ProcessingJob.available_at <= func.now())
        .order_by(ProcessingJob.available_at, ProcessingJob.id)
        .limit(1)
    )
    bind = getattr(session, "bind", None)
    if bind is None and hasattr(session, "get_bind"):
        bind_callable = session.get_bind
        if not asyncio.iscoroutinefunction(bind_callable):
            bind = bind_callable()
    if bind is not None and hasattr(bind, "dialect") and getattr(bind.dialect, "name", None) == "postgresql":
        query = query.with_for_update(skip_locked=True)
    result = await session.execute(query)
    job = result.scalar_one_or_none()
    if job is None:
        return None

    job.status = "processing"
    job.attempt_count += 1
    await session.commit()
    logger.info("Claimed processing job %s for reading %s (attempt %d)", job.id, job.reading_id, job.attempt_count)
    return job


async def complete_job(session: AsyncSession, job_id: UUID) -> None:
    """Mark a job as successfully completed."""
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        return
    job.status = "completed"
    job.last_error = None
    await session.commit()
    logger.info("Completed processing job %s", job_id)


async def fail_job(
    session: AsyncSession,
    job_id: UUID,
    error: Exception,
    *,
    max_attempts: int = 5,
) -> None:
    """Retry with exponential backoff, or move the job to a terminal failed state."""
    job = await session.get(ProcessingJob, job_id)
    if job is None:
        return

    job.last_error = (str(error) or repr(error))[:4000]
    if job.attempt_count >= max_attempts:
        job.status = "failed"
        logger.error(
            "Job %s reached maximum attempts (%d/%d); marked failed: %s",
            job_id,
            job.attempt_count,
            max_attempts,
            job.last_error,
        )
    else:
        job.status = "pending"
        delay_seconds = min(300, 2 ** max(job.attempt_count - 1, 0))
        job.available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
        logger.warning(
            "Job %s failed attempt %d/%d; retrying in %ds: %s",
            job_id,
            job.attempt_count,
            max_attempts,
            delay_seconds,
            job.last_error,
        )
    await session.commit()


async def process_one_job(
    processor: JobProcessor = default_processor,
    *,
    max_attempts: int = 5,
) -> bool:
    """Claim and process a single pending job. Returns True if a job was claimed and processed."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        job = await claim_pending_job(session)
        if job is None:
            return False
        try:
            await processor(job.reading_id)
        except Exception as error:
            logger.exception("Processor error on job %s", job.id)
            await session.rollback()
            await fail_job(session, job.id, error, max_attempts=max_attempts)
        else:
            await complete_job(session, job.id)
        return True


async def process_pending_jobs(
    processor: JobProcessor = default_processor,
    *,
    max_attempts: int = 5,
    idle_delay_seconds: float = 1.0,
    once: bool = False,
) -> None:
    """Run the queue until cancelled, or process available jobs once when ``once`` is true."""
    while True:
        processed = await process_one_job(processor, max_attempts=max_attempts)
        if once:
            if not processed:
                return
            continue
        if not processed:
            await asyncio.sleep(idle_delay_seconds)


def run_worker() -> None:
    """CLI entrypoint for background worker."""
    parser = argparse.ArgumentParser(description="Andromot background job worker")
    parser.add_argument("--once", action="store_true", help="Drain pending jobs once and exit")
    parser.add_argument("--max-attempts", type=int, default=5, help="Maximum attempts before permanent failure")
    parser.add_argument("--idle-delay", type=float, default=1.0, help="Seconds to sleep when no jobs available")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("Starting background worker (once=%s, max_attempts=%d)", args.once, args.max_attempts)
    try:
        asyncio.run(
            process_pending_jobs(
                default_processor,
                max_attempts=args.max_attempts,
                idle_delay_seconds=args.idle_delay,
                once=args.once,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Worker stopped by user/signal")


if __name__ == "__main__":
    run_worker()