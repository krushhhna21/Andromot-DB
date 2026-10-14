from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from andromot.models import ProcessingJob, Reading
from andromot.worker import (
    claim_pending_job,
    complete_job,
    default_processor,
    fail_job,
    process_one_job,
)


@pytest.fixture
def sample_job() -> ProcessingJob:
    return ProcessingJob(
        id=uuid4(),
        reading_id=uuid4(),
        status="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
        last_error=None,
    )


@pytest.mark.asyncio
async def test_claim_pending_job_found(sample_job: ProcessingJob) -> None:
    session = AsyncMock()
    session.get_bind = MagicMock(return_value=MagicMock(dialect=MagicMock(name="postgresql")))
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = sample_job
    session.execute.return_value = mock_result

    claimed = await claim_pending_job(session)

    assert claimed is sample_job
    assert claimed.status == "processing"
    assert claimed.attempt_count == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_pending_job_none_available() -> None:
    session = AsyncMock()
    session.get_bind = MagicMock(return_value=MagicMock(dialect=MagicMock(name="postgresql")))
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    claimed = await claim_pending_job(session)

    assert claimed is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_job(sample_job: ProcessingJob) -> None:
    sample_job.status = "processing"
    sample_job.last_error = "some previous error"
    session = AsyncMock()
    session.get.return_value = sample_job

    await complete_job(session, sample_job.id)

    assert sample_job.status == "completed"
    assert sample_job.last_error is None
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_job_retry_backoff(sample_job: ProcessingJob) -> None:
    sample_job.status = "processing"
    sample_job.attempt_count = 2  # attempt 2 of 5 -> delay = 2^(2-1) = 2 seconds
    session = AsyncMock()
    session.get.return_value = sample_job

    error = ValueError("sensor failure")
    await fail_job(session, sample_job.id, error, max_attempts=5)

    assert sample_job.status == "pending"
    assert sample_job.last_error == "sensor failure"
    assert sample_job.available_at > datetime.now(UTC)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_job_max_attempts_terminal(sample_job: ProcessingJob) -> None:
    sample_job.status = "processing"
    sample_job.attempt_count = 5  # reached max_attempts 5
    session = AsyncMock()
    session.get.return_value = sample_job

    error = RuntimeError("permanent fatal error")
    await fail_job(session, sample_job.id, error, max_attempts=5)

    assert sample_job.status == "failed"
    assert "permanent fatal error" in sample_job.last_error
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_one_job_success(sample_job: ProcessingJob) -> None:
    session = AsyncMock()
    session_factory = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    session_ctx.__aexit__.return_value = None
    session_factory.return_value = session_ctx

    processor = AsyncMock()

    with (
        patch("andromot.worker.get_session_factory", return_value=session_factory),
        patch("andromot.worker.claim_pending_job", return_value=sample_job),
        patch("andromot.worker.complete_job") as mock_complete,
    ):
        result = await process_one_job(processor)

        assert result is True
        processor.assert_awaited_once_with(sample_job.reading_id)
        mock_complete.assert_awaited_once_with(session, sample_job.id)


@pytest.mark.asyncio
async def test_process_one_job_failure(sample_job: ProcessingJob) -> None:
    session = AsyncMock()
    session_factory = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    session_ctx.__aexit__.return_value = None
    session_factory.return_value = session_ctx

    test_error = RuntimeError("processing exploded")
    processor = AsyncMock(side_effect=test_error)

    with (
        patch("andromot.worker.get_session_factory", return_value=session_factory),
        patch("andromot.worker.claim_pending_job", return_value=sample_job),
        patch("andromot.worker.fail_job") as mock_fail,
    ):
        result = await process_one_job(processor, max_attempts=5)

        assert result is True
        processor.assert_awaited_once_with(sample_job.reading_id)
        session.rollback.assert_awaited_once()
        mock_fail.assert_awaited_once_with(session, sample_job.id, test_error, max_attempts=5)


@pytest.mark.asyncio
async def test_default_processor_reading_found() -> None:
    reading_id = uuid4()
    mock_reading = MagicMock(spec=Reading, id=reading_id, device_id=uuid4(), measurements=[])

    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = mock_reading
    session.execute.return_value = mock_res

    session_factory = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    session_ctx.__aexit__.return_value = None
    session_factory.return_value = session_ctx

    with patch("andromot.worker.get_session_factory", return_value=session_factory):
        await default_processor(reading_id)

    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_processor_reading_not_found_raises() -> None:
    reading_id = uuid4()

    session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_res

    session_factory = MagicMock()
    session_ctx = AsyncMock()
    session_ctx.__aenter__.return_value = session
    session_ctx.__aexit__.return_value = None
    session_factory.return_value = session_ctx

    with (
        patch("andromot.worker.get_session_factory", return_value=session_factory),
        pytest.raises(ValueError, match=f"Reading {reading_id} not found"),
    ):
        await default_processor(reading_id)
