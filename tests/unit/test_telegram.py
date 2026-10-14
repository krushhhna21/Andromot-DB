from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from andromot.config import get_settings
from andromot.telegram import (
    format_alert_message,
    get_bot_info,
    send_telegram_message,
)


def test_format_alert_message_critical() -> None:
    msg = format_alert_message(
        device_id="device-001",
        level="CRITICAL",
        measurements=[
            {"gas_type": "carbon_monoxide", "value": 115.0, "unit": "ppm", "calibration_status": "calibrated"}
        ],
        event_timestamp=datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC),
        sensor_health={"status": "healthy", "battery_percent": 95},
        summary="Evacuate the area immediately.",
    )

    assert "🚨 <b>[ANDROMOT ALERT — CRITICAL]</b>" in msg
    assert "<b>Device:</b> <code>device-001</code>" in msg
    assert "2026-10-14 12:00:00 UTC" in msg
    assert "<b>carbon_monoxide:</b> 115.0 ppm (calibrated)" in msg
    assert "Battery: 95%" in msg
    assert "Evacuate the area immediately." in msg


def test_format_alert_message_warning() -> None:
    msg = format_alert_message(
        device_id="device-002",
        level="WARNING",
        measurements=[
            {"gas_type": "methane", "value": 15.2, "unit": "%LEL", "calibration_status": "calibrated"}
        ],
    )

    assert "⚠️ <b>[ANDROMOT ALERT — WARNING]</b>" in msg
    assert "<code>device-002</code>" in msg
    assert "15.2 %LEL" in msg


def test_format_alert_message_resolved() -> None:
    msg = format_alert_message(
        device_id="device-001",
        level="RESOLVED",
        measurements=[],
    )

    assert "✅ <b>[ANDROMOT ALERT — RESOLVED]</b>" in msg


@pytest.mark.asyncio
async def test_send_telegram_message_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    get_settings.cache_clear()

    result = await send_telegram_message("Hello from test")
    assert result is False


@pytest.mark.asyncio
async def test_send_telegram_message_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    get_settings.cache_clear()

    result = await send_telegram_message("Hello from test")
    assert result is False


@pytest.mark.asyncio
async def test_send_telegram_message_no_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    get_settings.cache_clear()

    result = await send_telegram_message("Hello from test")
    assert result is False


@pytest.mark.asyncio
async def test_send_telegram_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456789")
    get_settings.cache_clear()

    mock_resp = httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})
    mock_post = AsyncMock(return_value=mock_resp)

    with patch.object(httpx.AsyncClient, "post", mock_post):
        result = await send_telegram_message("Emergency test message")

    assert result is True
    assert mock_post.await_count == 1
    args, kwargs = mock_post.call_args
    assert "https://api.telegram.org/bot123456:fake-token/sendMessage" in args[0]
    assert kwargs["json"]["chat_id"] == "-100123456789"
    assert kwargs["json"]["text"] == "Emergency test message"
    assert kwargs["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_send_telegram_message_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456789")
    get_settings.cache_clear()

    mock_resp = httpx.Response(400, json={"ok": False, "description": "Chat not found"})
    mock_post = AsyncMock(return_value=mock_resp)

    with patch.object(httpx.AsyncClient, "post", mock_post):
        result = await send_telegram_message("Test message")

    assert result is False


@pytest.mark.asyncio
async def test_send_telegram_message_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123456789")
    get_settings.cache_clear()

    mock_post = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))

    with patch.object(httpx.AsyncClient, "post", mock_post):
        result = await send_telegram_message("Test message")

    assert result is False


@pytest.mark.asyncio
async def test_get_bot_info_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token")
    get_settings.cache_clear()

    mock_resp = httpx.Response(
        200, json={"ok": True, "result": {"id": 123456, "is_bot": True, "first_name": "AndromotBot", "username": "andromot_bot"}}
    )
    mock_get = AsyncMock(return_value=mock_resp)

    with patch.object(httpx.AsyncClient, "get", mock_get):
        info = await get_bot_info()

    assert info is not None
    assert info["username"] == "andromot_bot"
