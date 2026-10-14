"""Telegram Bot notification client for safety alert dispatches."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def format_alert_message(
    device_id: str,
    level: str,
    measurements: list[dict[str, Any]] | list[Any],
    *,
    event_timestamp: datetime | None = None,
    sensor_health: dict[str, Any] | None = None,
    summary: str | None = None,
) -> str:
    """Format structured alert into a clean HTML message for Telegram."""
    level_normalized = level.upper()
    if level_normalized == "CRITICAL":
        header_icon = "🚨"
    elif level_normalized == "WARNING":
        header_icon = "⚠️"
    elif level_normalized == "RESOLVED":
        header_icon = "✅"
    else:
        header_icon = "ℹ️"

    ts_str = (event_timestamp or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"{header_icon} <b>[ANDROMOT ALERT — {level_normalized}]</b>",
        f"<b>Device:</b> <code>{device_id}</code>",
        f"<b>Timestamp:</b> {ts_str}",
        "",
        "<b>Measurements:</b>",
    ]

    for m in measurements:
        if isinstance(m, dict):
            gas = m.get("gas_type", "unknown")
            val = m.get("value", 0)
            unit = m.get("unit", "")
            cal = m.get("calibration_status", "")
        else:
            gas = getattr(m, "gas_type", "unknown")
            val = getattr(m, "value", 0)
            unit = getattr(m, "unit", "")
            cal = getattr(m, "calibration_status", "")

        cal_note = f" ({cal})" if cal else ""
        lines.append(f"• <b>{gas}:</b> {val} {unit}{cal_note}")

    if sensor_health:
        status = sensor_health.get("status", "unknown")
        battery = sensor_health.get("battery_percent")
        batt_str = f" (Battery: {battery}%)" if battery is not None else ""
        lines.extend(["", f"<b>Sensor Health:</b> {status}{batt_str}"])

    if summary:
        lines.extend(["", "<b>Safety Guidance:</b>", f"<i>{summary}</i>"])

    return "\n".join(lines)


async def send_telegram_message(
    text: str,
    *,
    chat_id: str | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """Dispatch an alert message to a Telegram chat. Returns True if sent successfully."""
    settings = get_settings()

    if not settings.telegram_enabled:
        logger.debug("Telegram notifications are disabled (TELEGRAM_ENABLED=false)")
        return False

    token_secret = settings.telegram_bot_token
    if token_secret is None:
        logger.warning("Telegram is enabled but TELEGRAM_BOT_TOKEN is not configured")
        return False

    token = token_secret.get_secret_value().strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN is empty")
        return False

    target_chat_id = chat_id or settings.telegram_chat_id
    if not target_chat_id:
        logger.warning("No Telegram chat_id provided and TELEGRAM_CHAT_ID is not configured")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.telegram_timeout_seconds) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                logger.info("Successfully dispatched Telegram alert to chat %s", target_chat_id)
                return True

            logger.error(
                "Telegram API returned HTTP %d: %s",
                response.status_code,
                response.text,
            )
            return False
    except httpx.TimeoutException:
        logger.error("Telegram API request timed out after %.1fs", settings.telegram_timeout_seconds)
        return False
    except (httpx.HTTPError, OSError) as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


async def get_bot_info() -> dict[str, Any] | None:
    """Fetch current bot profile via getMe to verify token credentials."""
    settings = get_settings()
    token_secret = settings.telegram_bot_token
    if not token_secret:
        return None

    token = token_secret.get_secret_value().strip()
    url = f"{TELEGRAM_API_BASE}/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=settings.telegram_timeout_seconds) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json().get("result", {})
            logger.error("Telegram getMe failed with HTTP %d: %s", resp.status_code, resp.text)
            return None
    except (httpx.HTTPError, OSError) as exc:
        logger.error("Telegram getMe error: %s", exc)
        return None


async def fetch_latest_chat_id() -> tuple[str, str] | None:
    """Check getUpdates for the most recent incoming chat_id."""
    settings = get_settings()
    token_secret = settings.telegram_bot_token
    if not token_secret:
        return None
    token = token_secret.get_secret_value().strip()
    url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates"
    try:
        async with httpx.AsyncClient(timeout=settings.telegram_timeout_seconds) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                results = resp.json().get("result", [])
                for item in reversed(results):
                    msg = item.get("message") or item.get("channel_post")
                    if isinstance(msg, dict):
                        chat = msg.get("chat", {})
                        if "id" in chat:
                            c_id = str(chat["id"])
                            name = chat.get("username") or chat.get("first_name") or chat.get("title") or c_id
                            return c_id, name
            return None
    except (httpx.HTTPError, OSError) as exc:
        logger.error("Telegram getUpdates error: %s", exc)
        return None


def main() -> None:
    """CLI utility to test Telegram bot configuration."""
    parser = argparse.ArgumentParser(description="Andromot Telegram Bot CLI")
    parser.add_argument("--test", action="store_true", help="Send a test message using current .env configuration")
    parser.add_argument("--info", action="store_true", help="Check bot identity via getMe")
    parser.add_argument("--link", action="store_true", help="Auto-detect chat ID from recent messages and test")
    parser.add_argument("--message", type=str, default=None, help="Custom test message content")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = get_settings()
    print("=== Andromot Telegram Configuration ===")
    print(f"TELEGRAM_ENABLED: {settings.telegram_enabled}")
    print(f"TELEGRAM_CHAT_ID: {settings.telegram_chat_id or '[Not Set]'}")
    has_token = bool(settings.telegram_bot_token and settings.telegram_bot_token.get_secret_value())
    print(f"TELEGRAM_BOT_TOKEN: {'[Configured]' if has_token else '[Not Set]'}")

    if args.info or args.test or args.link:
        if not has_token:
            print("\nError: TELEGRAM_BOT_TOKEN is not configured in .env")
            return
        bot = asyncio.run(get_bot_info())
        if bot:
            print(f"\nBot Verified: @{bot.get('username')} ({bot.get('first_name')})")
        else:
            print("\nFailed to connect with the configured bot token.")
            return

    if args.link:
        print("\nChecking for incoming messages from Telegram...")
        detected = asyncio.run(fetch_latest_chat_id())
        if detected:
            chat_id, name = detected
            print(f"Detected incoming message from: {name} (Chat ID: {chat_id})")
            print(f"You can set TELEGRAM_CHAT_ID={chat_id} in your .env")
            print("Sending verification message to detected chat...")
            msg = format_alert_message(
                device_id="device-001",
                level="RESOLVED",
                measurements=[{"gas_type": "carbon_monoxide", "value": 0.0, "unit": "ppm", "calibration_status": "calibrated"}],
                sensor_health={"status": "healthy", "battery_percent": 100},
                summary="Andromot Telegram bot has been successfully linked and verified!",
            )
            success = asyncio.run(send_telegram_message(msg, chat_id=chat_id))
            if success:
                print("Verification message delivered to Telegram!")
            return
        print("No incoming messages detected.")
        print(f"Please open https://t.me/{bot.get('username')} in Telegram, click START or send 'hi', then run this again.")
        return

    if args.test:
        msg = args.message or format_alert_message(
            device_id="device-001",
            level="WARNING",
            measurements=[{"gas_type": "carbon_monoxide", "value": 38.5, "unit": "ppm", "calibration_status": "calibrated"}],
            sensor_health={"status": "healthy", "battery_percent": 88},
            summary="Test notification from Andromot IoT Platform.",
        )
        print("\nSending test message...")
        success = asyncio.run(send_telegram_message(msg))
        if success:
            print("Telegram test message sent successfully!")
        else:
            print("Failed to send test message. Check logs for details.")


if __name__ == "__main__":
    main()
