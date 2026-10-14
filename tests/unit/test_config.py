from andromot.config import Settings


def test_settings_keep_secrets_out_of_repr() -> None:
    settings = Settings(device_credential_pepper="test-pepper")

    assert "test-pepper" not in repr(settings)
    assert settings.groq_enabled is False
