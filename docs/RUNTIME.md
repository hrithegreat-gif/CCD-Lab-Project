# Secure runtime and observability

Implemented in app/config.py, app/main.py and app/observability.py. All settings
can be set in .env or the process environment; defaults keep the Phase 1
local-lab behavior.

## Authentication (optional API key)

`API_KEY` is read as a pydantic `SecretStr`. Empty or whitespace-only means
local-only mode: every request is served without authentication. A nonempty key
requires `Authorization: Bearer <key>` on `/api/*` (including the optional
extension routes) and `GET /metrics`. `/health/live`, `/health/ready`, `/docs`
and `/openapi.json` stay public so probes and Swagger still work.

- Comparison is constant time: both sides are HMAC-normalized to fixed-length
  digests before `secrets.compare_digest`.
- Authentication runs before any body byte is read and before routing, so
  absent, malformed or oversized bodies cannot bypass it. Missing, malformed
  and wrong tokens all return the same `401 {"detail": "Unauthorized"}` with
  `WWW-Authenticate: Bearer`; the key is never reflected anywhere.
- When a key is configured, the generated OpenAPI marks `/api/*` and `/metrics`
  with a `bearerAuth` HTTP security scheme so Swagger UI offers the key field.
  In local-only mode no security requirement appears.

## Byte-level request limit

`MAX_REQUEST_BYTES` (default 65536, range 1024..10485760) caps every request
body before it can be buffered:

- A declared `Content-Length` above the limit is rejected with
  `413 {"detail": "Request body too large"}` and `Connection: close` before any
  byte is read.
- The actual received bytes are counted regardless of the header, so absent,
  chunked or lying `Content-Length` values are capped at the same limit. No
  more than the cap is ever buffered.
- A validator refuses configurations where `MAX_REQUEST_BYTES` is smaller than
  `max_input_chars * 12 + 1024`, so valid JSON bodies can never be rejected as
  too large by misconfiguration (worst case: non-BMP characters escape to
  12 bytes per character).
- Early rejection closes the connection, so a client that is still streaming an
  oversized body may see a connection reset instead of the 413 (the same
  trade-off nginx makes to shed load). Requests at or under the limit are
  unaffected.
- The cap applies to all `/api/*` routes including optional extensions. The
  extension's own per-capability audio limit only matters after the operator
  raises this core cap.

## Prometheus metrics

`GET /metrics` exposes real `prometheus_client` metrics:

- `http_requests_total{method, endpoint, status_code}` counter
- `http_request_duration_seconds{method, endpoint}` histogram
- default Python process/gc metrics from the client library

Labels are bounded by construction: `method` is normalized to known HTTP
methods or `OTHER`; `endpoint` is the matched route template (for example
`/api/models/{model_id}`) or a fixed allowlisted path, and any unmatched client
path collapses to `unmatched`. Input text, API keys, model IDs and raw request
paths never become labels, so there is no high-cardinality or privacy exposure
through metrics.

## Startup, readiness and shutdown

Models load before startup completes by default (blocking), which is what the
existing test suite and single-process deployment rely on. With
`BACKGROUND_MODEL_LOAD=true` (set for the Kubernetes deployment), startup
finishes immediately, `/health/live` answers while the real model loads in a
worker thread, and `/health/ready` returns 503 until the model finished a real
warm-up prediction. Readiness is never fabricated:

- Failed initialization keeps liveness up and readiness at 503 forever (error
  details are logged by type only).
- Shutdown drains an in-flight load (5 s grace), then cancels the load task.
  Native loads cannot be force-killed, so the worker thread itself closes the
  model if shutdown was requested, before or after the load completes; a
  cancelled task never closes or registers anything, and a load that fails
  after cancellation is still closed by its worker. Models are registered only
  when shutdown has not started.
- Inference capacity stays acquired for the whole native prediction and is
  released afterwards.

## Smoke test

`scripts/smoke_test.py` discovers the configured input limit from
`/api/models` metadata instead of hardcoding one, and verifies real
predictions, validation errors, authentication and the metrics exposition:

    python scripts/smoke_test.py --base-url http://127.0.0.1:8000
    python scripts/smoke_test.py --api-key <key>          # or set API_KEY

The key is read from `--api-key` or the `API_KEY` environment variable and is
never printed. When a key is supplied, the test also asserts that
unauthenticated `/api/models` access is rejected.

## Verified behavior

`uv run --python 3.12 --no-project --with-requirements requirements-dev.txt
python -m pytest tests/` passes with the real model (failures in other agents'
files are tracked by their owners). The smoke test was run against a live
uvicorn server in local-only and API-key modes, including real positive and
negative predictions, 401 rejection, the metrics exposition and 413 handling
for streamed oversized bodies.
