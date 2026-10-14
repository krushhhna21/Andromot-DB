import logging
from collections.abc import Mapping
from typing import Any

from .config import Settings

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "database_url",
        "device_credential_pepper",
        "groq_api_key",
        "password",
        "telegram_bot_token",
        "token",
    }
)


def _redact(value: Any, key: str | None = None) -> Any:
    if key and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class SecretSafeFilter(logging.Filter):
    """Redact sensitive structured fields before they reach a log handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(record.msg)
        record.args = _redact(record.args)
        return True


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.addFilter(SecretSafeFilter())
