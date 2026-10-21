"""Tests for app/studio.py: static UI serving, safe UI config, security headers.

The polished frontend assets (app/static/*) are authored concurrently, so the
serving mechanics are verified against a dedicated fixture asset directory:
these tests never depend on, download, or fail because of the real static
contents. The full create_app integration is exercised through the shared
conftest fixtures with the injected TestModel — no downloads, no provider
imports, no real model behavior.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.config import Settings
from app.extensions import ExtensionSettings, install_extensions
from app.main import create_app
from app.studio import (
    DEFAULT_STATIC_DIR,
    UI_CONTENT_SECURITY_POLICY,
    StudioSecurityHeadersMiddleware,
    install_studio,
    is_studio_path,
)
from tests.conftest import TEST_API_KEY, TestModel

EXPECTED_CONFIG_KEYS = {
    "title",
    "version",
    "auth_required",
    "max_request_bytes",
    "sentiment_max_input_chars",
    "generation_max_input_chars",
    "generation_max_output_tokens",
    "speech_max_input_chars",
    "transcription_max_bytes",
}

FIXTURE_ASSETS = {
    "index.html": b"<!doctype html><html><body>studio fixture index</body></html>",
    "studio.css": b"body { color: teal; }",
    "studio-core.mjs": b"export const core = 'fixture-core';",
    "studio.mjs": b"export const app = 'fixture-app';",
    "favicon.svg": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
}


def _build_studio_app(static_dir: Path, api_key: str = "") -> FastAPI:
    application = FastAPI(title="AI Model Serving Platform", version="0.1.0")
    settings = Settings(_env_file=None, api_key=api_key)
    install_studio(application, settings, static_dir=static_dir)
    return application


@pytest.fixture
def studio_app(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    for name, content in FIXTURE_ASSETS.items():
        (assets / name).write_bytes(content)
    return _build_studio_app(assets)


@pytest.fixture
def studio_client(studio_app):
    # No lifespan needed: install_studio registers routes/middleware only.
    return TestClient(studio_app)


# ---------------------------------------------------------------------------
# Static asset serving (fixture assets; real app/static contents not required)
# ---------------------------------------------------------------------------

def test_root_serves_index_html(studio_client):
    response = studio_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "studio fixture index" in response.text


def test_studio_slash_serves_index_html(studio_client):
    response = studio_client.get("/studio/")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "studio fixture index" in response.text


def test_studio_redirects_to_canonical_slash(studio_client):
    response = studio_client.get("/studio", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/studio/"


@pytest.mark.parametrize("name", sorted(FIXTURE_ASSETS))
def test_assets_served_with_fixed_mime_and_ui_headers(studio_client, name):
    media_type, cache_control = {
        # The /studio/assets/ endpoint is revalidate-only for every file,
        # including index.html; the HTML pages themselves (/ and /studio/)
        # are no-store (covered separately below).
        "index.html": ("text/html; charset=utf-8", "no-cache"),
        "studio.css": ("text/css; charset=utf-8", "no-cache"),
        "studio-core.mjs": ("text/javascript; charset=utf-8", "no-cache"),
        "studio.mjs": ("text/javascript; charset=utf-8", "no-cache"),
        "favicon.svg": ("image/svg+xml", "no-cache"),
    }[name]
    response = studio_client.get(f"/studio/assets/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"] == cache_control
    assert response.headers["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_unknown_asset_is_404_without_disclosure(studio_client):
    response = studio_client.get("/studio/assets/not-in-allowlist.js")
    assert response.status_code == 404
    assert "not-in-allowlist" not in response.text


@pytest.mark.parametrize("target", [
    # Encoded traversal reaches the server decoded; the strict allowlist must
    # reject every variant before the filesystem is read.
    "/studio/assets/..%2Fmain.py",
    "/studio/assets/%2e%2e/main.py",
    "/studio/assets/%2e%2e",
    "/studio/assets/..%5Csettings.py",
    "/studio/assets/",
    "/studio/assets",
    "/studio/assets/studio.py",
    "/studio/assets/app/studio.py",
])
def test_traversal_and_non_allowlisted_paths_are_404(studio_client, target):
    assert studio_client.get(target).status_code == 404


def test_dot_segment_names_never_resolve_server_side(studio_app):
    """httpx resolves literal '..' client-side against the base URL, so the
    decoded segments are asserted against the route handler directly: '..' and
    '.' are not allowlisted and can never resolve to a file."""
    handler = next(
        route.endpoint for route in studio_app.routes
        if getattr(route, "path", None) == "/studio/assets/{asset_name}"
    )
    for name in ("..", ".", "..%2Fsettings.py", "../settings.py"):
        with pytest.raises(HTTPException) as excinfo:
            handler(name)
        assert excinfo.value.status_code == 404


def test_missing_asset_files_return_404_not_500(tmp_path):
    """Assets may not exist yet (authored concurrently): degrade to 404."""
    application = _build_studio_app(tmp_path)  # empty directory
    client = TestClient(application)
    assert client.get("/").status_code == 404
    assert client.get("/studio/").status_code == 404
    assert client.get("/studio/assets/index.html").status_code == 404


# ---------------------------------------------------------------------------
# UI security headers: studio surface only, nothing else touched
# ---------------------------------------------------------------------------

def test_headers_on_html_config_and_redirect(studio_client):
    for response in (
        studio_client.get("/"),
        studio_client.get("/studio/"),
        studio_client.get("/studio", follow_redirects=False),
        studio_client.get("/studio/config"),
    ):
        assert response.headers["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"


def test_headers_absent_from_non_studio_paths():
    raw = _raw_headers("/api/models")
    assert "content-security-policy" not in raw
    assert "x-frame-options" not in raw
    assert "cache-control" not in raw


def test_headers_present_on_studio_path_raw_asgi():
    raw = _raw_headers("/studio/config")
    assert raw["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
    assert raw["x-frame-options"] == "DENY"
    assert raw["cache-control"] == "no-store"
    assert raw["x-content-type-options"] == "nosniff"
    assert raw["referrer-policy"] == "no-referrer"


def _raw_headers(path: str) -> dict[str, str]:
    sent = []

    async def downstream(scope, receive, send):
        await Response(b"ok", media_type="text/plain")(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    asyncio.run(StudioSecurityHeadersMiddleware(downstream)(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in start["headers"]}


# ---------------------------------------------------------------------------
# Safe UI config: fields, no secrets, no paths, no invented values
# ---------------------------------------------------------------------------

def test_config_without_extensions_reports_fallback_defaults(studio_client):
    data = studio_client.get("/studio/config").json()
    assert data == {
        "title": "AI Model Serving Platform",
        "version": "0.1.0",
        "auth_required": False,
        "max_request_bytes": 65536,
        "sentiment_max_input_chars": 2000,
        "generation_max_input_chars": 2000,
        "generation_max_output_tokens": 256,
        "speech_max_input_chars": 1000,
        "transcription_max_bytes": 10_485_760,
    }
    assert set(data) == EXPECTED_CONFIG_KEYS


def test_config_with_auth_reports_flag_never_the_key(tmp_path):
    application = _build_studio_app(tmp_path, api_key=TEST_API_KEY)
    client = TestClient(application)
    response = client.get("/studio/config")
    assert response.json()["auth_required"] is True
    assert TEST_API_KEY not in response.text
    assert set(response.json()) == EXPECTED_CONFIG_KEYS


def test_config_uses_installed_extension_runtime_limits():
    """Values flow from the already-installed extension runtime, not a fresh
    ExtensionSettings built from the environment."""
    application = FastAPI(title="AI Model Serving Platform", version="0.1.0")
    settings = Settings(_env_file=None)
    install_extensions(
        application,
        settings,
        extension_settings=ExtensionSettings(
            _env_file=None,
            generate_max_input_chars=1234,
            generate_max_output_tokens=77,
            speech_max_input_chars=222,
            transcription_max_bytes=4096,
        ),
    )
    install_studio(application, settings)
    data = TestClient(application).get("/studio/config").json()
    assert data["generation_max_input_chars"] == 1234
    assert data["generation_max_output_tokens"] == 77
    assert data["speech_max_input_chars"] == 222
    assert data["transcription_max_bytes"] == 4096
    # Non-extension fields still come from core settings.
    assert data["max_request_bytes"] == 65536
    assert data["sentiment_max_input_chars"] == 2000
    assert data["auth_required"] is False


# ---------------------------------------------------------------------------
# create_app integration: studio installed after extensions, API untouched
# ---------------------------------------------------------------------------

def test_create_app_installs_studio_with_runtime_limits(client):
    data = client.get("/studio/config").json()
    runtime = client.app.state.extensions
    assert data["title"] == "AI Model Serving Platform"
    assert data["version"] == "0.1.0"
    assert data["auth_required"] is False
    assert data["max_request_bytes"] == client.app.state.settings.max_request_bytes
    assert data["sentiment_max_input_chars"] == client.app.state.settings.max_input_chars
    assert data["generation_max_input_chars"] == runtime.settings.generate_max_input_chars
    assert data["generation_max_output_tokens"] == runtime.settings.generate_max_output_tokens
    assert data["speech_max_input_chars"] == runtime.settings.speech_max_input_chars
    assert data["transcription_max_bytes"] == runtime.settings.transcription_max_bytes


def test_install_studio_cannot_be_called_twice_on_created_app(application):
    with pytest.raises(RuntimeError):
        install_studio(application, application.state.settings)


def test_api_requires_auth_while_studio_is_public(authed_client):
    assert authed_client.get("/api/models").status_code == 401
    assert authed_client.get("/metrics").status_code == 401
    config = authed_client.get("/studio/config")
    assert config.status_code == 200
    assert config.json()["auth_required"] is True
    # Frontend routes are public regardless of static asset presence.
    assert authed_client.get("/").status_code in (200, 404)
    assert authed_client.get("/studio/").status_code in (200, 404)
    assert authed_client.get("/studio", follow_redirects=False).status_code in (307, 200)


def test_studio_headers_do_not_leak_into_api_or_docs(client):
    api = client.get("/api/models")
    assert api.status_code == 200
    assert "content-security-policy" not in api.headers
    assert "x-frame-options" not in api.headers
    docs = client.get("/openapi.json")
    assert docs.status_code == 200
    assert "content-security-policy" not in docs.headers
    health = client.get("/health/live")
    assert health.status_code == 200
    assert "x-frame-options" not in health.headers


def test_studio_headers_present_on_created_app(client):
    config = client.get("/studio/config")
    assert config.headers["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
    assert config.headers["cache-control"] == "no-store"
    assert config.headers["x-frame-options"] == "DENY"


def test_studio_routes_are_excluded_from_openapi(client):
    schema = client.get("/openapi.json").json()
    for path in schema["paths"]:
        assert not path.startswith("/studio")
        assert path != "/"


def test_default_extension_capabilities_stay_disabled(client):
    """Default install triggers no provider behavior (no imports, downloads)."""
    capabilities = client.get("/api/capabilities").json()["capabilities"]
    assert capabilities, "extension routes must still be installed"
    assert all(cap["state"] == "disabled" for cap in capabilities.values())


def test_default_static_dir_serves_real_index_when_authored(client):
    """The default directory is app/static; assets are authored concurrently,
    so accept either the served page or the graceful 404 — never a 500."""
    index = DEFAULT_STATIC_DIR / "index.html"
    response = client.get("/")
    assert response.status_code in (200, 404)
    if index.is_file():
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["content-security-policy"] == UI_CONTENT_SECURITY_POLICY
        assert response.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Path classification helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/", True),
    ("/studio", True),
    ("/studio/", True),
    ("/studio/config", True),
    ("/studio/assets/studio.css", True),
    ("", False),
    (None, False),
    ("/metrics", False),
    ("/api/models", False),
    ("/api/capabilities", False),
    ("/docs", False),
    ("/openapi.json", False),
    ("/health/live", False),
    ("/studiofoo", False),
    ("/STUDIO", False),
])
def test_is_studio_path(path, expected):
    assert is_studio_path(path) is expected
