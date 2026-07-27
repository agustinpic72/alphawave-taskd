import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_local_mode_exposes_configured_openapi_routes() -> None:
    app = create_app(make_settings(deployment_mode="local", auth_enabled=False))
    client = TestClient(app)

    docs = client.get("/docs")
    redoc = client.get("/redoc")
    schema = client.get("/openapi.json")

    assert docs.status_code == 200
    assert redoc.status_code == 200
    assert schema.status_code == 200
    assert schema.json()["info"] == {"title": "alphawave-taskd", "version": "0.1.0"}


@pytest.mark.parametrize(
    "config",
    [
        make_settings(deployment_mode="private", auth_enabled=True, auth_cookie_secure=False),
        make_settings(deployment_mode="public", auth_enabled=True, auth_cookie_secure=True),
    ],
    ids=["private", "public"],
)
def test_non_local_modes_do_not_expose_docs_or_an_alternate_schema(config: Settings) -> None:
    app = create_app(config)
    client = TestClient(app)

    for path in (
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/openapi",
        "/swagger.json",
        "/api/docs",
        "/api/openapi.json",
    ):
        assert client.get(path).status_code == 404, path
