"""Authentication, secret non-disclosure and byte-level request limit behavior."""
import asyncio
import json

import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import Settings
from app.main import BodyLimitMiddleware, RequestBodyTooLarge, create_app
from tests.conftest import TEST_API_KEY, TestModel


# ---------------------------------------------------------------------------
# Local-only mode (empty API key)
# ---------------------------------------------------------------------------

def test_local_mode_allows_api_and_metrics_without_header(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/api/models").status_code == 200
    assert client.post(
        "/api/inference", json={"model": "sentiment", "input": "hello"}
    ).status_code == 200


def test_whitespace_api_key_stays_local_mode():
    assert Settings(_env_file=None, api_key="   ").auth_enabled is False
    assert Settings(_env_file=None, api_key="").auth_enabled is False


# ---------------------------------------------------------------------------
# Bearer authentication
# ---------------------------------------------------------------------------

def test_missing_token_is_rejected_with_www_authenticate(authed_client):
    response = authed_client.get("/api/models")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Unauthorized"}


@pytest.mark.parametrize("header", [
    "Basic dXNlcjpwYXNz",
    "Bearer",
    "Bearer   ",
    "bearer wrong-key",
    "Bearer " + TEST_API_KEY + "x",
    "Bearer x" + TEST_API_KEY,
    "x" + TEST_API_KEY,
])
def test_bad_credentials_rejected(authed_client, header):
    response = authed_client.get("/api/models", headers={"Authorization": header})
    assert response.status_code == 401


def test_correct_token_accepted_and_scheme_case_insensitive(authed_client):
    headers = {"Authorization": f"Bearer {TEST_API_KEY}"}
    assert authed_client.get("/api/models", headers=headers).status_code == 200
    lower = {"Authorization": f"bEaReR {TEST_API_KEY}"}
    assert authed_client.get("/api/models", headers=lower).status_code == 200


def test_bearer_matches_compares_in_constant_time_helper():
    settings = Settings(_env_file=None, api_key=TEST_API_KEY)
    assert settings.bearer_matches(TEST_API_KEY)
    assert not settings.bearer_matches(TEST_API_KEY + "x")
    assert not settings.bearer_matches("")
    assert not settings.bearer_matches("a" * 1000)


def test_health_probes_stay_public_when_auth_enabled(authed_client):
    assert authed_client.get("/health/live").status_code == 200
    assert authed_client.get("/health/ready").status_code == 200
    assert authed_client.get("/docs").status_code == 200
    assert authed_client.get("/openapi.json").status_code == 200


def test_metrics_requires_token_when_auth_enabled(authed_client, auth_headers):
    assert authed_client.get("/metrics").status_code == 401
    assert authed_client.get("/metrics", headers=auth_headers).status_code == 200


def test_inference_with_token_succeeds(authed_client, auth_headers):
    response = authed_client.post(
        "/api/inference",
        json={"model": "sentiment", "input": "hello"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["prediction"] == "positive"


# ---------------------------------------------------------------------------
# Auth cannot be bypassed by absent or malformed bodies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {},  # no body at all
    {"content": "{broken", "headers": {"Content-Type": "application/json"}},
    {"content": b"x" * 999999},  # oversized raw body, no auth header
    {"json": ["not", "an", "object"]},
])
def test_body_shape_never_bypasses_auth(authed_client, kwargs):
    assert authed_client.post("/api/inference", **kwargs).status_code == 401


# ---------------------------------------------------------------------------
# OpenAPI discoverability of the security scheme
# ---------------------------------------------------------------------------

def test_openapi_documents_bearer_scheme_when_enabled(authed_client):
    schema = authed_client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["bearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    protected = ["/api/inference", "/api/models", "/metrics"]
    for path in protected:
        assert path in schema["paths"], path
    for path, operations in schema["paths"].items():
        if path.startswith("/api/") or path == "/metrics":
            for spec in operations.values():
                assert spec.get("security") == [{"bearerAuth": []}], path
    # Public probes must not require the scheme.
    for spec in schema["paths"]["/health/live"].values():
        assert "security" not in spec


def test_openapi_local_mode_has_no_security_requirement(client):
    schema = client.get("/openapi.json").json()
    assert "securitySchemes" not in schema.get("components", {})


# ---------------------------------------------------------------------------
# Secret non-disclosure
# ---------------------------------------------------------------------------

def test_api_key_hidden_in_settings_repr_and_str():
    settings = Settings(_env_file=None, api_key=TEST_API_KEY)
    assert TEST_API_KEY not in repr(settings)
    assert TEST_API_KEY not in str(settings)


def test_api_key_never_appears_in_responses_or_metrics(authed_client, auth_headers):
    rejected = authed_client.get("/api/models")
    assert TEST_API_KEY not in rejected.text
    accepted = authed_client.get("/metrics", headers=auth_headers)
    assert TEST_API_KEY not in accepted.text
    inference = authed_client.post(
        "/api/inference",
        json={"model": "sentiment", "input": "hello"},
        headers=auth_headers,
    )
    assert TEST_API_KEY not in inference.text


# ---------------------------------------------------------------------------
# Byte-level request limits
# ---------------------------------------------------------------------------

def test_declared_content_length_over_limit_rejected_before_read(authed_client, auth_headers):
    limit = authed_client.app.state.settings.max_request_bytes
    response = authed_client.post(
        "/api/inference",
        content=b"x" * (limit + 1),
        headers={**auth_headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert response.headers["connection"] == "close"


def test_streamed_chunked_body_over_limit_rejected_413(client):
    limit = client.app.state.settings.max_request_bytes

    def chunks():
        chunk = b"x" * 1024
        for _ in range(limit // 1024 + 2):
            yield chunk

    response = client.post(
        "/api/inference",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_body_within_limit_succeeds(client):
    payload = json.dumps({"model": "sentiment", "input": "hello"}).encode()
    assert len(payload) < client.app.state.settings.max_request_bytes
    response = client.post(
        "/api/inference", content=payload, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 200


def test_413_response_does_not_echo_body(client):
    marker = "PRIVATE-OVERSIZE-MARKER" + "z" * 64
    limit = client.app.state.settings.max_request_bytes
    response = client.post(
        "/api/inference",
        content=marker.encode() + b"y" * (limit + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert marker not in response.text


def test_max_request_bytes_validator_couples_to_input_limit():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_input_chars=10000, max_request_bytes=65536)
    Settings(_env_file=None, max_input_chars=10000, max_request_bytes=200000)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_request_bytes=512)


# ---------------------------------------------------------------------------
# Middleware-level streaming checks (raw ASGI, no HTTP client involvement)
# ---------------------------------------------------------------------------

def _run_middleware(max_bytes, scope, receive, send, downstream):
    async def scenario():
        await BodyLimitMiddleware(downstream, max_bytes=max_bytes)(scope, receive, send)

    return asyncio.run(scenario())


def test_lying_content_length_is_caught_by_byte_counting():
    """Header claims 4 bytes, 64 arrive: the cap still applies."""
    app_bodies = []
    sent = []

    async def downstream(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                break
        app_bodies.append(body)
        await JSONResponse({"ok": True})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"y" * 64, "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/inference",
        "headers": [(b"content-length", b"4")],
    }
    _run_middleware(16, scope, receive, send, downstream)
    assert app_bodies == []  # the cap fired before the app could consume anything
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


def test_absent_content_length_streaming_is_capped():
    """Chunked upload without Content-Length cannot exceed the cap."""
    sent = []
    consumed = []

    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.request":
                consumed.append(len(message.get("body", b"")))
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                break
        await JSONResponse({"ok": True})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"z" * 8, "more_body": True}

    scope = {"type": "http", "method": "POST", "path": "/api/inference", "headers": []}

    async def send(message):
        sent.append(message)

    _run_middleware(16, scope, receive, send, downstream)
    # Two 8-byte chunks pass (16 == cap, not above it); the third raises.
    assert sum(consumed) == 16
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


def test_within_limit_passes_through_middleware():
    app_bodies = []
    sent = []

    async def downstream(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
        app_bodies.append(body)
        await JSONResponse({"ok": True})(scope, receive, send)

    async def receive():
        return {"type": "http.request", "body": b"hello", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/inference",
        "headers": [(b"content-length", b"5")],
    }
    _run_middleware(16, scope, receive, send, downstream)
    assert app_bodies == [b"hello"]
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


def test_declared_over_limit_never_calls_receive():
    sent = []

    async def forbidden_receive():
        raise AssertionError("body must not be read when declared length exceeds cap")

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/inference",
        "headers": [(b"content-length", b"999999999")],
    }
    _run_middleware(16, scope, forbidden_receive, send,
                    lambda scope, receive, send: None)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413


def test_request_body_too_large_is_http_exception():
    exc = RequestBodyTooLarge()
    assert exc.status_code == 413
    assert exc.detail == "Request body too large"
    assert exc.headers == {"Connection": "close"}
