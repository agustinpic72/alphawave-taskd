import pytest
from pydantic import ValidationError

from app.core.config import Settings, validate_deployment_security
from app import main as app_main


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_local_mode_allows_auth_disabled() -> None:
    config = make_settings(deployment_mode="local", auth_enabled=False)

    assert config.docs_enabled is True
    assert config.docs_url == "/docs"


@pytest.mark.parametrize("mode", ["private", "public"])
def test_non_local_modes_require_auth(mode: str) -> None:
    with pytest.raises(ValidationError, match="authentication must be enabled"):
        make_settings(deployment_mode=mode, auth_enabled=False, auth_cookie_secure=True)


@pytest.mark.parametrize("mode", ["private", "public"])
def test_non_local_modes_reject_dev_endpoints(mode: str) -> None:
    with pytest.raises(ValidationError, match="development endpoints must be disabled"):
        make_settings(
            deployment_mode=mode,
            auth_enabled=True,
            auth_cookie_secure=True,
            app_dev_endpoints_enabled=True,
        )


def test_public_mode_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="secure authentication cookies are required"):
        make_settings(deployment_mode="public", auth_enabled=True, auth_cookie_secure=False)


def test_private_mode_allows_non_secure_cookie_for_private_transport() -> None:
    config = make_settings(deployment_mode="private", auth_enabled=True, auth_cookie_secure=False)

    assert config.docs_enabled is False
    assert config.openapi_url is None


def test_documented_alphawave_environment_names_are_loaded(monkeypatch) -> None:
    monkeypatch.setenv("ALPHAWAVE_DEPLOYMENT_MODE", "public")
    monkeypatch.setenv("ALPHAWAVE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ALPHAWAVE_AUTH_COOKIE_SECURE", "true")

    config = Settings(_env_file=None)

    assert config.deployment_mode == "public"
    assert config.auth_enabled is True
    assert config.auth_cookie_secure is True


def test_startup_validation_catches_runtime_mutation() -> None:
    config = make_settings(deployment_mode="local", auth_enabled=False)
    config.deployment_mode = "private"

    with pytest.raises(ValueError, match="authentication must be enabled"):
        validate_deployment_security(config)


def test_process_entrypoint_fails_before_starting_server(monkeypatch) -> None:
    monkeypatch.setattr(app_main.settings, "deployment_mode", "public")
    monkeypatch.setattr(app_main.settings, "auth_enabled", False)

    with pytest.raises(ValueError, match="authentication must be enabled"):
        app_main.main()
