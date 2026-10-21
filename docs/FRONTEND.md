# Inference Studio demo

A responsive, same-origin frontend served by the existing FastAPI application.
There is no npm install, frontend build, external font, analytics or CDN runtime.
The existing application image includes the assets through its `COPY app ./app`.

## Open the demo

Start the server with the [local instructions](../README.md#start-locally-python-312),
then open **http://127.0.0.1:8000/** (also available at `/studio/`).
Swagger remains available at `/docs`.

1. Choose **Positive review**, **Negative review**, or write your own English text.
2. Click **Analyze sentiment**, or press Ctrl/Cmd + Enter.
3. Inspect the prediction, predicted-class softmax score, server processing time,
   processed tokens and measured browser round trip. Switch to **JSON** to see
   the actual response; copy or download it.
4. Open **Models** for live adapter availability and **Runs** for this tab's
   last 20 successful experiments. Clear history to remove runs and the current
   result. No run history survives a reload.

This is binary sentiment classification: **positive or negative, not neutral**.
The softmax score is **not calibrated correctness**. The truncation notice means
only the processed portion of the input contributed to the result. Single-run
latencies and this tab's average are not performance benchmarks or cloud/HPA
telemetry.

## Authentication and safety

- With a configured server `API_KEY`, use **Session access** to enter that same
  key. It stays in JavaScript memory, never local/session storage, cookies, a URL
  or an analytics service. The password field is cleared after submission/close.
  Reload, **Clear key**, or leaving the page forgets it.
- Only allowlisted same-origin endpoints receive requests; the key goes only to
  `/api/*`. There are no provider SDKs, cross-origin requests or automatic
  inference retries. Inference inputs are not submitted until you click Run.
- Readiness and authenticated metadata govern whether Run is available. Failed
  inference never adds a success to history or synthesizes a model response.
- Optional hosted requests require explicit consent: inputs leave the server for
  Groq, whose usage may incur separate charges. The UI never enables adapters or
  changes server configuration. Model switches reset consent.
- Cancelling stops **browser waiting**, not necessarily ongoing server/native
  work. Client timeouts do not silently retry a potentially billable request.
- Text outputs are rendered as text, not HTML. Audio URLs are revoked when
  replaced/cleared. HTML/config use no-store and static assets revalidate. Studio
  responses carry restrictive CSP, no-referrer, nosniff and frame-denial headers.
  Existing API auth, body limits, health probes and metrics are unchanged.
- Empty-key access is for **loopback-only local development**. Use the existing
  secure SSM forwarding runbook for cloud demos; do not expose an unauthenticated
  public service or add plaintext inbound ports for the frontend.

The HTML shell and safe public `/studio/config` reveal title/version, numeric
limits and whether auth is required, never a key or secret/provider environment.
The five assets are served through an exact allowlist, not a generic directory
mount or SPA fallback.

## Optional capabilities

| Task | Model / API | Browser behavior |
| --- | --- | --- |
| Sentiment | DistilBERT / `/api/inference` | English text, class score, tokens and truncation |
| Generation | Qwen 3.5 or Groq GPT-OSS / `/api/generate` | Prompt, bounded output tokens, generated text |
| Speech | Kitten Mini / `/api/speech` | Script, advertised voice, WAV controls/download |
| Transcription | Groq Whisper Turbo / `/api/transcriptions` | Raw audio upload, transcription text |

Optional adapters remain **disabled by default**. Enabling them is a separate
operator decision described in [model extensions](MODEL_EXTENSIONS.md). No
optional weights or provider calls are needed to demonstrate baseline sentiment.
An enabled adapter must report ready before it can be used. The effective audio
limit is the **smaller of `MAX_REQUEST_BYTES` and `TRANSCRIPTION_MAX_BYTES`**:
with the current defaults, it is 64 KiB, not the nominal 10 MiB adapter limit.
Only WAV, MP3/MPGA, MP4/M4A, OGG, WebM and FLAC content types are accepted.
Audio for older speech runs is not retained; download the current WAV before
running another request.

## Repeat validation

Offline contract tests use native Node 22+ and no npm dependencies:

```powershell
node --check app/static/studio-core.mjs
node --check app/static/studio.mjs
node --test tests/frontend/studio-core.test.mjs
uv run --no-project --python 3.12 --with-requirements requirements-dev.txt python -m pytest tests/ -q
```

The CI frontend job executes the offline JS tests and contributes to the existing
CI gate. Python tests check serving, route containment, safe config and unchanged
API auth. A source workflow is not evidence that GitHub Actions has run.

Browser acceptance requires a running loopback server with the real cached
sentiment model ready, empty-key local access and optional adapters disabled.
It launches a fresh headless browser profile, not your logged-in browser.
With Edge installed on Windows:

```powershell
uv run --no-project --python 3.12 --with playwright==1.58.0 python scripts/test_studio_browser.py --base-url http://127.0.0.1:8000 --browser-channel msedge
```

Use `--browser-channel chrome` for installed Chrome. For bundled Chromium, first
run `uv run --no-project --python 3.12 --with playwright==1.58.0 python -m playwright install chromium`,
then omit the channel flag. Browser binaries are validation tools, not application
runtime dependencies.

The browser script checks **real** positive/negative, truncation and mobile
sentiment inference. A separate context intercepts HTTP to test auth,
generation, speech, transcription, XSS-safe output, consent, byte limits and
429/503/504/cancel handling. Those cases are **explicit fixtures, not evidence of
real optional-model inference or Groq execution**. No paid calls or AWS resources
are created. Screenshots and a scope-labelled report go to `results/frontend/`.
See the [verification ledger](VERIFICATION.md) for actual completed checks.
