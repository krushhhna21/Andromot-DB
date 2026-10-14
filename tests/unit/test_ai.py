from unittest.mock import AsyncMock, patch

import httpx
import pytest

from andromot.ai import generate_incident_summary
from andromot.config import get_settings


@pytest.mark.asyncio
async def test_generate_incident_summary_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_ENABLED", "false")
    get_settings.cache_clear()

    res = await generate_incident_summary("device-001", "CRITICAL", [])
    assert res is None


@pytest.mark.asyncio
async def test_generate_incident_summary_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_ENABLED", "true")
    monkeypatch.setenv("GROQ_API_KEY", "fake-key")
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    get_settings.cache_clear()

    mock_resp = httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": "Evacuate immediately. Ventilate the room."}}
            ]
        },
    )
    mock_post = AsyncMock(return_value=mock_resp)

    with patch.object(httpx.AsyncClient, "post", mock_post):
        res = await generate_incident_summary(
            "device-001",
            "CRITICAL",
            [{"gas_type": "carbon_monoxide", "value": 120.0, "unit": "ppm"}],
        )

    assert res == "Evacuate immediately. Ventilate the room."
    assert mock_post.await_count == 1
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["model"] == "qwen/qwen3.8-27b"
