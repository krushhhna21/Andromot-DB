"""Telegram Bot notification client for safety alert dispatches."""

import argparse
import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import httpx
from uuid import uuid4
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from .ai import answer_safety_query
from .config import get_settings
from .db import get_session_factory
from .models import Device, DeviceSubscriber, ProcessingJob, Reading


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


async def _get_status_text() -> str:
    settings = get_settings()
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            dev_cnt = (await session.execute(select(func.count(Device.id)))).scalar_one()
            readings_cnt = (await session.execute(select(func.count(Reading.id)))).scalar_one()
            pending_jobs = (
                await session.execute(
                    select(func.count(ProcessingJob.id)).where(ProcessingJob.status == "pending")
                )
            ).scalar_one()

        ai_model = settings.groq_model if settings.groq_enabled else "Disabled"
        return (
            "🟢 <b>Andromot Platform: Operational</b>\n\n"
            "• <b>Database:</b> Connected (Neon PostgreSQL)\n"
            f"• <b>Active Devices:</b> {dev_cnt}\n"
            f"• <b>Total Readings:</b> {readings_cnt}\n"
            f"• <b>Pending Ingestion Queue:</b> {pending_jobs}\n"
            f"• <b>AI Safety Engine:</b> {ai_model}\n"
            "• <b>Worker Status:</b> Active"
        )
    except Exception as exc:
        logger.exception("Failed to query platform status: %s", exc)
        return "⚠️ <b>Platform Status: Degraded</b>\n\nDatabase query failed. Please check backend logs."


async def _get_latest_telemetry_text() -> str:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            stmt = (
                select(Reading)
                .options(
                    selectinload(Reading.measurements),
                    selectinload(Reading.environment_measurements),
                )
                .order_by(Reading.event_timestamp.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            latest = result.scalar_one_or_none()
            if latest is None:
                return (
                    "ℹ️ <b>No Telemetry Logged Yet</b>\n\n"
                    "Once an IoT device transmits readings to <code>/api/v1/readings</code>, "
                    "the latest gas levels will be displayed here."
                )

            device = await session.get(Device, latest.device_id)
            dev_name = device.device_id if device else str(latest.device_id)

            ts_str = latest.event_timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
            lines = [
                "📊 <b>Latest Gas Telemetry</b>",
                f"<b>Device:</b> <code>{dev_name}</code>",
                f"<b>Timestamp:</b> {ts_str}",
                "",
                "<b>Gas Measurements:</b>",
            ]
            if latest.measurements:
                for m in latest.measurements:
                    cal = f" ({m.calibration_status})" if m.calibration_status else ""
                    val_clean = f"{float(m.value):g}"
                    lines.append(f"• <b>{m.gas_type}:</b> {val_clean} {m.unit}{cal}")
            else:
                lines.append("• <i>No gas measurements recorded</i>")

            if latest.environment_measurements:
                lines.extend(["", "<b>Environment:</b>"])
                for em in latest.environment_measurements:
                    em_clean = f"{float(em.value):g}"
                    lines.append(f"• <b>{em.measurement_type}:</b> {em_clean} {em.unit}")

            health = latest.sensor_health or {}
            if health:
                status = health.get("status", "healthy")
                battery = health.get("battery_percent")
                batt_str = f" (Battery: {battery}%)" if battery is not None else ""
                lines.extend(["", f"<b>Sensor Health:</b> {status}{batt_str}"])

            return "\n".join(lines)
    except Exception as exc:
        logger.exception("Failed to query latest telemetry: %s", exc)
        return "⚠️ Could not retrieve latest telemetry. Database may be busy."


async def _get_devices_text() -> str:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            stmt = select(Device).order_by(Device.created_at.asc())
            devices = (await session.execute(stmt)).scalars().all()
            if not devices:
                return "ℹ️ No devices registered yet."
            lines = ["📟 <b>Registered IoT Devices:</b>", ""]
            for d in devices:
                created = d.created_at.strftime("%Y-%m-%d") if d.created_at else "N/A"
                lines.append(f"• <code>{d.device_id}</code> | Status: <b>{d.status}</b> | Registered: {created}")
            return "\n".join(lines)
    except Exception as exc:
        logger.exception("Failed to query devices: %s", exc)
        return "⚠️ Could not retrieve devices list."


async def _subscribe_device(chat_id: str, user_name: str | None, device_id: str | None) -> str:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            # If no device_id specified, find registered devices
            if not device_id:
                devices = (await session.execute(select(Device).where(Device.status == "active"))).scalars().all()
                if not devices:
                    return "ℹ️ No active devices registered to subscribe to."
                if len(devices) == 1:
                    target_device = devices[0].device_id
                else:
                    dev_list = ", ".join(f"<code>{d.device_id}</code>" for d in devices)
                    return f"Please specify which device to subscribe to:\nUsage: <code>/subscribe &lt;device_id&gt;</code>\n\nAvailable devices: {dev_list}"
            else:
                target_device = device_id.strip()
                dev = await session.scalar(select(Device).where(Device.device_id == target_device))
                if dev is None:
                    return f"⚠️ Device <code>{target_device}</code> not found in platform registry. Send <code>/devices</code> to see valid devices."

            # Check existing subscription
            existing = await session.scalar(
                select(DeviceSubscriber).where(
                    DeviceSubscriber.device_id == target_device,
                    DeviceSubscriber.telegram_chat_id == chat_id,
                )
            )
            if existing is not None:
                return f"ℹ️ You are already subscribed to alerts for <code>{target_device}</code>!"

            sub = DeviceSubscriber(
                id=uuid4(),
                device_id=target_device,
                telegram_chat_id=chat_id,
                user_name=user_name,
            )
            session.add(sub)
            await session.commit()
            return (
                f"✅ <b>Successfully Subscribed!</b>\n\n"
                f"This chat (<code>{chat_id}</code>) is now registered to receive real-time emergency & warning alerts for device <code>{target_device}</code>.\n\n"
                "Type <code>/subscriptions</code> to manage your alert feeds."
            )
    except Exception as exc:
        logger.exception("Subscription error: %s", exc)
        return "⚠️ Failed to save subscription. Please try again later."


async def _unsubscribe_device(chat_id: str, device_id: str | None) -> str:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            stmt = delete(DeviceSubscriber).where(DeviceSubscriber.telegram_chat_id == chat_id)
            if device_id:
                stmt = stmt.where(DeviceSubscriber.device_id == device_id.strip())
            result = await session.execute(stmt)
            await session.commit()
            if result.rowcount > 0:
                target_str = f"<code>{device_id.strip()}</code>" if device_id else "all devices"
                return f"🗑️ <b>Unsubscribed!</b> You will no longer receive alerts for {target_str}."
            return "ℹ️ You had no active subscriptions to remove."
    except Exception as exc:
        logger.exception("Unsubscribe error: %s", exc)
        return "⚠️ Failed to unsubscribe. Please try again."


async def _get_subscriptions_text(chat_id: str) -> str:
    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            subs = (
                await session.execute(
                    select(DeviceSubscriber).where(DeviceSubscriber.telegram_chat_id == chat_id)
                )
            ).scalars().all()
            if not subs:
                return "ℹ️ You have no active device subscriptions in this chat.\n\nType <code>/subscribe</code> to link to sensor alerts."
            lines = ["🔔 <b>Your Active Device Subscriptions:</b>", ""]
            for s in subs:
                lines.append(f"• <code>{s.device_id}</code> (Alerts active)")
            lines.extend(["", "To stop alerts, send: <code>/unsubscribe &lt;device_id&gt;</code>"])
            return "\n".join(lines)
    except Exception as exc:
        logger.exception("Get subscriptions error: %s", exc)
        return "⚠️ Could not retrieve subscriptions."


async def handle_incoming_telegram_message(message: dict[str, Any]) -> None:
    """Process an incoming interactive message from Telegram and send an appropriate response."""
    chat = message.get("chat", {})
    chat_id = str(chat.get("id"))
    text = (message.get("text") or message.get("caption") or "").strip()
    if not text:
        await send_telegram_message(
            "I received your media! Send <code>/help</code> to view available commands.",
            chat_id=chat_id,
        )
        return

    first_name = chat.get("first_name", "there")
    norm_text = text.lower().strip()
    cmd = norm_text.split()[0] if norm_text.startswith("/") else norm_text

    # 1. Welcome / Help / Greeting
    if cmd in ("/start", "/help", "help", "hi", "hello", "ii", "hiii", "hey"):
        reply = (
            f"👋 Hello <b>{first_name}</b>!\n\n"
            "I am the <b>Andromot IoT Safety Bot</b>. I monitor 8 hazardous gas channels in real-time "
            "(LPG, Methane, Propane, Butane, Hydrogen, Alcohol, Carbon Monoxide, Smoke) "
            "and alert you immediately if emergency thresholds are breached.\n\n"
            "<b>Available Commands:</b>\n"
            "• <code>/status</code> — System & database health\n"
            "• <code>/latest</code> — Latest sensor telemetry & gas readings\n"
            "• <code>/gases</code> — Safety limits & thresholds for all 8 gases\n"
            "• <code>/devices</code> — Registered sensor hardware\n"
            "• <code>/subscribe &lt;device_id&gt;</code> — Link this chat to receive device alerts\n"
            "• <code>/unsubscribe</code> — Stop receiving alerts in this chat\n"
            "• <code>/subscriptions</code> — View your active alert feeds\n"
            "• <code>/help</code> — Show this menu\n\n"
            "💡 <i>You can also ask me any safety question (e.g. 'What are the dangers of hydrogen leak?') and our Groq AI will assist!</i>"
        )
        await send_telegram_message(reply, chat_id=chat_id)
        return


    # 2. Latest Telemetry (supports /latest or natural English inquiries)
    if cmd == "/latest" or any(p in norm_text for p in (
        "latest reading", "latest readings", "current reading", "current readings",
        "show reading", "show readings", "get reading", "get readings",
        "what are the readings", "what is the reading", "what are the latest readings",
        "sensor data", "gas levels", "current level", "current levels"
    )):
        telemetry_text = await _get_latest_telemetry_text()
        await send_telegram_message(telemetry_text, chat_id=chat_id)
        return

    # 3. Gases / Thresholds
    if cmd in ("/gases", "/thresholds") or any(p in norm_text for p in (
        "what gases", "gas list", "show gases", "thresholds", "alarm limits", "danger values", "trigger values"
    )):
        gases_text = (
            "🧪 <b>Target Gases & Alarm Thresholds:</b>\n\n"
            "• <b>Carbon Monoxide (CO):</b>\n"
            "  ⚠️ Warning: ≥ 35 ppm | 🚨 Critical: ≥ 100 ppm\n\n"
            "• <b>LPG (Propane/Butane):</b>\n"
            "  ⚠️ Warning: ≥ 10% LEL (1000 ppm) | 🚨 Critical: ≥ 20% LEL (2000 ppm)\n\n"
            "• <b>Methane (CH4):</b>\n"
            "  ⚠️ Warning: ≥ 10% LEL (1000 ppm) | 🚨 Critical: ≥ 20% LEL (2500 ppm)\n\n"
            "• <b>Propane (C3H8):</b>\n"
            "  ⚠️ Warning: ≥ 10% LEL (1000 ppm) | 🚨 Critical: ≥ 20% LEL (2000 ppm)\n\n"
            "• <b>Butane (C4H10):</b>\n"
            "  ⚠️ Warning: ≥ 10% LEL (1000 ppm) | 🚨 Critical: ≥ 20% LEL (1800 ppm)\n\n"
            "• <b>Hydrogen (H2):</b>\n"
            "  ⚠️ Warning: ≥ 10% LEL (1000 ppm) | 🚨 Critical: ≥ 20% LEL (4000 ppm)\n\n"
            "• <b>Alcohol / Ethanol:</b>\n"
            "  ⚠️ Warning: ≥ 200 ppm | 🚨 Critical: ≥ 500 ppm\n\n"
            "• <b>Smoke:</b>\n"
            "  ⚠️ Warning: ≥ 200 ppm | 🚨 Critical: ≥ 400 ppm\n\n"
            "<i>Deterministic rules evaluate each measurement immediately upon ingestion.</i>"
        )
        await send_telegram_message(gases_text, chat_id=chat_id)
        return

    # 4. Status
    if cmd == "/status" or any(p in norm_text for p in ("system status", "health", "how is system", "platform status")):
        status_text = await _get_status_text()
        await send_telegram_message(status_text, chat_id=chat_id)
        return

    # 5. Devices
    if cmd == "/devices" or any(p in norm_text for p in ("show devices", "which devices", "list devices")):
        devices_text = await _get_devices_text()
        await send_telegram_message(devices_text, chat_id=chat_id)
        return

    # 6. Subscribe to alerts
    if cmd == "/subscribe" or norm_text.startswith("/subscribe") or (cmd == "subscribe" and len(norm_text.split()) <= 2):
        parts = text.split()
        req_dev = parts[1] if len(parts) > 1 else None
        res = await _subscribe_device(chat_id, first_name, req_dev)
        await send_telegram_message(res, chat_id=chat_id)
        return

    # 7. Unsubscribe from alerts
    if cmd == "/unsubscribe" or norm_text.startswith("/unsubscribe") or (cmd == "unsubscribe" and len(norm_text.split()) <= 2):
        parts = text.split()
        req_dev = parts[1] if len(parts) > 1 else None
        res = await _unsubscribe_device(chat_id, req_dev)
        await send_telegram_message(res, chat_id=chat_id)
        return

    # 8. View Subscriptions
    if cmd in ("/subscriptions", "/mysubscriptions") or any(p in norm_text for p in ("my subscriptions", "show subscriptions", "list subscriptions")):
        res = await _get_subscriptions_text(chat_id)
        await send_telegram_message(res, chat_id=chat_id)
        return

    # 9. Freeform question or text: invoke Groq AI with live telemetry context
    try:
        telemetry_context = await _get_latest_telemetry_text()
    except Exception:
        telemetry_context = None

    ai_answer = await answer_safety_query(text, telemetry_context=telemetry_context)
    if ai_answer:
        await send_telegram_message(ai_answer, chat_id=chat_id)
    else:
        await send_telegram_message(
            f"Received: \"{text}\"\n\nType <code>/help</code> for commands or <code>/latest</code> to see sensor readings.",
            chat_id=chat_id,
        )



async def poll_telegram_updates(stop_event: asyncio.Event | None = None) -> None:
    """Long-polling worker that listens for incoming Telegram messages and dispatches responses."""
    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_token:
        logger.info("Telegram polling skipped (disabled or no token configured)")
        return

    token = settings.telegram_bot_token.get_secret_value().strip()
    if not token:
        logger.info("Telegram polling skipped (empty token)")
        return

    # Delete any existing webhook to prevent polling conflicts
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{TELEGRAM_API_BASE}/bot{token}/deleteWebhook")
    except Exception as exc:
        logger.debug("deleteWebhook error (non-fatal): %s", exc)

    offset = 0
    logger.info("Telegram bot polling started")

    while stop_event is None or not stop_event.is_set():
        try:
            url = f"{TELEGRAM_API_BASE}/bot{token}/getUpdates"
            params = {"offset": offset, "timeout": 20}
            async with httpx.AsyncClient(timeout=35.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    updates = data.get("result", [])
                    for update in updates:
                        offset = max(offset, update.get("update_id", 0) + 1)
                        msg = update.get("message")
                        if msg:
                            try:
                                await handle_incoming_telegram_message(msg)
                            except Exception as exc:
                                logger.exception("Error handling Telegram message: %s", exc)
                elif resp.status_code == 409:
                    logger.warning("Telegram polling conflict (409). Backing off 5s...")
                    await asyncio.sleep(5)
                else:
                    logger.warning("Telegram getUpdates HTTP %d: %s", resp.status_code, resp.text)
                    await asyncio.sleep(3)
        except asyncio.CancelledError:
            logger.info("Telegram bot polling stopped (cancelled)")
            break
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("Telegram polling network glitch: %s", exc)
            await asyncio.sleep(2)
        except Exception as exc:
            logger.exception("Unexpected error in Telegram polling loop: %s", exc)
            await asyncio.sleep(3)



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
