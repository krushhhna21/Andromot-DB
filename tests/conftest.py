import pytest

from andromot.config import get_settings


@pytest.fixture(autouse=True)
def configured_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEVICE_CREDENTIAL_PEPPER", "test-pepper")
    monkeypatch.setenv("GROQ_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_ENABLED", "false")
    get_settings.cache_clear()

