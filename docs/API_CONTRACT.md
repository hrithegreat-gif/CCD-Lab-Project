# REST API contract

FastAPI OpenAPI: `/openapi.json`; Swagger: `/docs`. Sentiment and optional generation/audio schemas are deliberately separate.

## Authentication and byte limits

A nonempty `API_KEY` protects **all `/api/*` routes and `/metrics`** using `Authorization: Bearer <key>`. Missing, malformed and incorrect keys return a generic 401 with `WWW-Authenticate: Bearer`, before request-body parsing. Swagger advertises the Bearer scheme when auth is enabled. An empty key disables auth for local loopback development only; the cloud deploy script requires a nonempty SSM secret.

Health probes remain public. Requests are capped by declared and actual streamed bytes (`MAX_REQUEST_BYTES`, default 65536). Oversized bodies return 413. This is distinct from text-length validation (422). For sentiment JSON, the byte limit must be at least `MAX_INPUT_CHARS * 12 + 1024`, including worst-case JSON Unicode escaping.

## Baseline endpoints

| Method | Path | Success | Errors |
| --- | --- | --- | --- |
| GET | `/health/live` | 200, status alive | Process must be running |
| GET | `/health/ready` | 200, status ready | 503 until successful real model warm-up |
| GET | `/api/models` | 200, array of sentiment metadata | 401 |
| GET | `/api/models/{model_id}` | 200, model details | 401, 404 |
| POST | `/api/inference` | 200, real prediction | 401, 413, 404, 422, 429, 500, 503, 504 |
| GET | `/metrics` | 200, Prometheus text | 401 |

Inference request:

```json
{"model":"sentiment","input":"I love this product."}
```

Response: `status` (`success`), `model`, `prediction` (`positive`/`negative`), actual `confidence`, `processing_time_ms`, `tokens_processed`, `truncated`. Timing covers processing, not network time; no fixed score or latency is promised. Confidence is a softmax class score, not calibrated certainty.

Only `sentiment` is allowlisted in this registry. Clients cannot supply model source/revision. Input must be a nonblank string, at most 2000 characters by default; unexpected keys are rejected. Beyond 256 tokens, input is truncated and disclosed with `truncated=true`. Configured limits are listed in model metadata.

Errors have a `detail` string or a sanitized schema-validation detail array. Input text and private exception messages are omitted. A capacity 429 includes `Retry-After: 1`. The 504 deadline is **native ONNX execution only**, not a complete deadline for downloads, request parsing, tokenization, thread-pool waiting and network transport. Native capacity remains held until prediction actually returns.

Model-load failures do not fabricate inference: readiness and inference stay 503. With `BACKGROUND_MODEL_LOAD=true`, liveness is available during load; the default local blocking-startup mode begins serving only after the startup load attempt finishes. Automatic startup retry is not implemented.

Metrics count real HTTP responses and observe request duration using bounded method/endpoint/status labels. They contain no user input, key, model ID or arbitrary raw request path. A metrics endpoint does not itself install a metrics scraper/dashboard.

## Optional extensions

See [the detailed extension contract](MODEL_EXTENSIONS.md) for enable flags, provider/model allowlists, independent limits and error semantics.

- `GET /api/capabilities`: each adapter reports disabled/loading/unavailable/ready.
- `POST /api/generate`: local Qwen or Groq text generation, separate JSON schema.
- `POST /api/speech`: Kitten Mini WAV response, separate speech schema.
- `POST /api/transcriptions`: bounded **raw audio bytes**, supported audio `Content-Type`, optional language query parameter; not a multipart form or arbitrary URL fetch.

All four routes require the same Bearer key when enabled. Extension readiness does not replace sentiment readiness or add fake entries to `/api/models`. Disabled features perform no optional imports/downloads/provider calls. `/api/jobs` and durable queue/state semantics are not implemented.
