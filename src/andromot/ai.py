"""Groq LLM safety incident summary generator."""

import logging
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


async def generate_incident_summary(
    device_id: str,
    level: str,
    measurements: list[dict[str, Any]] | list[Any],
) -> str | None:
    """Generate concise emergency protocol and hazard assessment using Groq LLM."""
    settings = get_settings()
    if not settings.groq_enabled or not settings.groq_api_key:
        return None

    api_key = settings.groq_api_key.get_secret_value().strip()
    if not api_key:
        return None

    model = settings.groq_model or "qwen/qwen3.8-27b"

    meas_strs = []
    for m in measurements:
        if isinstance(m, dict):
            meas_strs.append(f"{m.get('gas_type')}: {m.get('value')} {m.get('unit')}")
        else:
            meas_strs.append(f"{getattr(m, 'gas_type', 'gas')}: {getattr(m, 'value', 0)} {getattr(m, 'unit', '')}")

    telemetry_summary = ", ".join(meas_strs) or "No measurements"
    user_prompt = f"Device {device_id} triggered an alarm level {level.upper()} with readings: {telemetry_summary}."

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an industrial IoT gas safety officer for the Andromot monitoring platform. "
                    "Provide a concise, authoritative 2-sentence emergency action protocol and hazard advice "
                    "for personnel based on the provided gas readings. Do not use markdown headers."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 150,
        "temperature": 0.2,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.groq_timeout_seconds) as client:
            resp = await client.post(GROQ_API_URL, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0]["message"]["content"].strip()
            logger.error("Groq API error HTTP %d: %s", resp.status_code, resp.text)
            return None
    except (httpx.HTTPError, OSError) as exc:
        logger.error("Groq request failed: %s", exc)
        return None
