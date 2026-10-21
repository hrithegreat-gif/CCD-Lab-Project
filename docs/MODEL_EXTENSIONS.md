# Optional model extensions

Implemented by `app/extensions.py` and `app/adapters/`. The core app installs
them once with `install_extensions(application, settings)` (see
`app/main.py`); no other core integration exists. All endpoints run under the
core `/api/*` authentication and body-limit middleware.

Verified upstream facts date from 2026-10-13 (Hugging Face model APIs, the
KittenTTS release wheel inspected directly, and Groq docs pages linked below).
Implementation tests use injected test doubles only — they do **not** download
weights, import torch/transformers/kittentts, call the Groq API, or verify real
model behaviour. Real-model verification is a separate, explicit step.

## Endpoints

| Method | Path | Purpose | Success | Notable errors |
| --- | --- | --- | --- | --- |
| GET | `/api/capabilities` | State of every optional capability | 200 | — |
| POST | `/api/generate` | Text generation (local Qwen or Groq) | 200 JSON | 422, 429, 500, 502, 503, 504 |
| POST | `/api/speech` | Text-to-speech WAV (Kitten Mini 0.8) | 200 `audio/wav` | 422, 429, 500, 503, 504 |
| POST | `/api/transcriptions` | Raw audio bytes → Groq Whisper text | 200 JSON | 413, 415, 422, 429, 500, 502, 503, 504 |

### Capability states

`GET /api/capabilities` returns one entry per capability
(`qwen-generation`, `kitten-speech`, `groq-text`, `groq-transcription`):

- `disabled` — flag off. The default. No optional import, download, thread, or
  network call is ever made; the endpoints return 503.
- `loading` — enabled local model is being downloaded/loaded/warmed up in a
  background thread. Requests return 503 with `Retry-After: 5`.
- `unavailable` — enabled but not usable. `detail` carries a safe reason code
  (`missing_dependency:<module>`, `missing_api_key`, `load_timeout`,
  `load_failed`, `espeak_backend_missing`, …). Never exception text or secrets.
- `ready` — requests are served. For the local models this means real warm-up
  inference succeeded. For the Groq capabilities it means **configured**:
  enabled, `GROQ_API_KEY` present, client constructed — the provider was
  deliberately not contacted, so `ready` is not a provider health check.

Readiness of extensions never influences core `/health/ready`; the core
lifespan stays authoritative for core models.

### POST /api/generate

Request (`extra` keys rejected):

```json
{"model": "qwen3.5-0.8b", "input": "Explain KV caching briefly.", "max_output_tokens": 128}
```

`model` is the allowlist `qwen3.5-0.8b` (local Transformers CPU) or
`groq-gpt-oss-120b` (Groq). Response:

```json
{"status": "success", "model": "qwen3.5-0.8b", "provider": "local",
 "output": "...", "input_tokens": 9, "output_tokens": 128,
 "truncated_input": false, "processing_time_ms": 123.4}
```

This is deliberately a different schema from the binary sentiment
`/api/inference` response. Errors: 422 validation/limits, 429 capacity
(`Retry-After`), 502 upstream failure/rejected credentials, 503
disabled/loading/unavailable, 504 deadline. If a native generation outlives the
response deadline, the 504 is returned while the worker keeps its capability
slot held until the native call actually finishes (release happens in the
worker, not on the cancelled await).

### POST /api/speech

Request: `{"input": "Hello world.", "voice": "Bella"}` — `voice` optional,
default `expr-voice-5-m`, must be in the allowlist (canonical
`expr-voice-2…5-m/f` plus aliases Bella/Jasper/Luna/Bruno/Rosie/Hugo/Kiki/Leo
from the pinned model config). Response: real mono 16-bit PCM WAV at 24 kHz
with `X-Sample-Rate` and `X-Processing-Time-Ms` headers.

### POST /api/transcriptions

The request body is **raw audio bytes** with a supported `Content-Type`
(`audio/wav` + aliases, `audio/mpeg`, `audio/flac`, `audio/ogg`,
`audio/webm`, `audio/mp4`/`audio/m4a`, `audio/mpga` — all map to Groq's
documented upload types). Optional `?language=de` query parameter (2–3 letter
code). The bytes are forwarded unmodified to Groq `whisper-large-v3-turbo`;
there is no URL field anywhere — external audio URLs are never accepted.
Oversized bodies are rejected with 413 before/while reading; the bound is
operator-configurable but hard-capped at 10 MiB (default 10 MiB — well below
Groq's 25 MB free-tier file limit).
The effective limit is the smaller of `TRANSCRIPTION_MAX_BYTES` and the core
`MAX_REQUEST_BYTES` (default **65536 bytes**, hard maximum **10485760 bytes**),
plus any smaller proxy limit. Explicitly raise the core limit for larger audio
uploads; changing the extension setting alone does not bypass it.

```json
{"status": "success", "model": "whisper-large-v3-turbo", "text": "...",
 "duration_seconds": null, "processing_time_ms": 210.5}
```

## Configuration

Separate `ExtensionSettings` (in `app/extensions.py`) validated with
pydantic-settings. `env_file` mirrors the core (`PROJECT_ROOT/.env`); tests
pass `_env_file=None`. Defaults — parent-owned `.env.example` should mirror:

| Env | Default | Notes |
| --- | --- | --- |
| `ENABLE_QWEN` | `false` | Local Transformers Qwen3.5-0.8B |
| `ENABLE_KITTEN` | `false` | Local ONNX Kitten Mini 0.8 |
| `ENABLE_GROQ` | `false` | Groq `openai/gpt-oss-120b` text |
| `ENABLE_GROQ_TRANSCRIPTION` | `false` | Groq `whisper-large-v3-turbo` |
| `GROQ_API_KEY` | empty | SecretStr; whitespace-trimmed, blank means missing; required only if a Groq flag is on |
| `GENERATE_MAX_INPUT_CHARS` | `2000` | 1–8000 |
| `GENERATE_MAX_OUTPUT_TOKENS` | `256` | 1–1024; request values are clamped to this |
| `SPEECH_MAX_INPUT_CHARS` | `1000` | 1–5000 |
| `TRANSCRIPTION_MAX_BYTES` | `10485760` | 1024–10485760; hard cap 10 MiB |
| `QWEN_MAX_INPUT_TOKENS` | `512` | 8–4096 truncation bound |
| `QWEN_MAX_CONCURRENCY` | `1` | 1–4 |
| `KITTEN_MAX_CONCURRENCY` | `1` | 1–4 |
| `GROQ_MAX_CONCURRENCY` | `4` | 1–16 |
| `TRANSCRIPTION_MAX_CONCURRENCY` | `2` | 1–16 |
| `LOAD_TIMEOUT_SECONDS` | `300` | Loading past this marks `unavailable` with `detail=load_timeout`; a load that finishes later never revives `ready` and its engine is closed exactly once |
| `GENERATE_TIMEOUT_SECONDS` | `60` | Cooperative native deadline + response deadline |
| `SPEECH_TIMEOUT_SECONDS` | `60` | |
| `TRANSCRIPTION_TIMEOUT_SECONDS` | `60` | |
| `GROQ_TIMEOUT_SECONDS` | `30` | Per-request httpx timeout |

Fixed in code, never env- or client-configurable: Groq base URL
`https://api.groq.com/openai/v1`, Groq model IDs, `Qwen/Qwen3.5-0.8B`
@ revision `2fc06364715b967f1860aea9cf38778875588b17`,
`KittenML/kitten-tts-mini-0.8` @ revision
`c02725660cea441db4c383af69f1f26f5cd00947`. No `trust_remote_code` anywhere.
Extension downloads reuse the core `MODEL_CACHE_DIR` /
`MODEL_LOCAL_FILES_ONLY` settings.

## Dependency profiles

The default deployment installs nothing extra. Each local capability profile
includes the core runtime dependencies, unlike an extras-only file.
On 2026-10-13 the core + non-Torch Qwen graph and the baseline + Kitten graph
were metadata-resolved for **CPython 3.12 / Linux x86_64** using
`pip install --dry-run --ignore-installed --only-binary=:all:` with target
platform tags. **The exact CPU Torch graph was not fully resolver-checked**:
Torch was excluded from the Qwen dry-run and unpinned PyPI Torch was allowed
transitively in the Kitten dry-run. The CPU index independently lists
`torch-2.14.1+cpu-cp312-cp312-manylinux_2_28_x86_64.whl`; that proves availability,
not installation or combined compatibility. No heavy wheels or weights were
installed. Optional version ranges are not a transitive lockfile; resolve and
smoke-test the actual profile before enabling it:

- **`requirements-qwen.txt`** (`ENABLE_QWEN`): duplicates **every** core
  `requirements.txt` pin; the only differences are the two Hugging Face pins
  transformers 5.19.0 makes incompatible with the baseline
  (`huggingface-hub 0.30.2 → 1.33.0`, `tokenizers 0.21.1 → 0.23.3`) plus
  `torch==2.14.1+cpu` (CPU wheel index) and `transformers==5.19.0`.
  A test asserts this inheritance. It must live in a **dedicated virtualenv**:
  every transformers release that supports Qwen3.5 (the qwen3_5 package is
  absent from 4.57.1 and 5.0.0) requires huggingface-hub>=1.x, which is
  fundamentally incompatible with the baseline pins — the dedicated venv IS
  the supported path; never "upgrade the core pins" to run it.
- **`requirements-kitten.txt`** (`ENABLE_KITTEN`): `-r requirements.txt` +
  `-r requirements-models.txt` — full runnable standalone, compatible with
  the baseline. The combined graph (101 packages) resolves for
  cp312/manylinux with the baseline pins intact: `numpy==2.2.5` coexists with
  `spacy==3.8.16`/`thinc==8.3.13` (no NumPy conflict), and the baseline
  `huggingface-hub`/`tokenizers` pins are kept.
- **`requirements-models.txt`**: the optional-only shared file (Kitten extras:
  official `kittentts` wheel, `misaki[en]`, `phonemizer`, `espeakng-loader`,
  `soundfile`, `spacy` + CPU-index `torch==2.14.1+cpu` because `misaki`
  depends on torch). Use it **on top of `requirements-qwen.txt`** for a
  combined Qwen+Kitten environment; never add `-r requirements.txt` into the
  Qwen profile environment (the baseline HF pins would conflict).
- **Groq** (`ENABLE_GROQ` / `ENABLE_GROQ_TRANSCRIPTION`): no extra file — the
  adapters use only `httpx==0.28.1`, already pinned in core `requirements.txt`.
- Do not install the unrelated PyPI `kittentts` package: same import name,
  different code and voices. The official wheel and `misaki` pull
  `num2words` themselves (resolves to 0.5.6); do not add a manual
  `num2words>=0.5.12` pin — its `docopt` dependency is sdist-only.

## Operational notes and honest limitations

- **No real model verification was performed.** Model weights, torch, and the
  optional wheels were not installed or downloaded during implementation (per
  budget constraints). The adapters are written strictly against verified
  official APIs: the transformers auto-mapping was verified against 5.19.0
  with the real pinned config and no weights
  (`AutoModelForCausalLM` maps `qwen3_5` → `Qwen3_5ForCausalLM`, the documented
  text-only class; the adapter asserts this at load and fails loudly into
  `unavailable` if it ever changes), the KittenTTS wheel source was inspected
  directly, and Groq endpoints come from the documented pages. First
  enablement downloads weights (~1.75 GB BF16 Qwen checkpoint → ~3.5 GB RAM
  as float32; ~79 MB Kitten) and must be smoke-tested by the operator.
- **Qwen details**: greedy decoding (`do_sample=False`) instead of the card's
  recommended sampling parameters — deterministic and CPU-friendly; the chat
  template renders non-thinking mode (this model's default). The Qwen3.5
  DeltaNet layers fall back to slower PyTorch ops without the optional
  `causal_conv1d`/`fla` kernels — expected on CPU.
- **Kitten details**: the wheel downloads model files without revision
  pinning, so this adapter pins `config.json`/`kitten_tts_mini_v0_8.onnx`/
  `voices.npz` itself via `hf_hub_download(..., revision=...)` and constructs
  the wheel's ONNX class directly. espeak-ng is wired from `espeakng_loader`
  when present; otherwise a system espeak-ng is required (Debian:
  `apt install espeak-ng`).
- **Groq**: billed service; no call is made without `GROQ_API_KEY` and an
  enabled flag. `gpt-oss-120b` is a reasoning model — reasoning tokens count
  toward `max_completion_tokens`; `temperature=0` and a bounded completion
  budget keep cost predictable. Never echo upstream error bodies (only HTTP
  status + Groq short error code) and never log the API key.
- **Concurrency and lifecycle**: each capability has its own
  `BoundedSemaphore`. A native call that outlives the response deadline keeps
  its slot held until it finishes (no orphaned unbounded work); the 429 path
  never queues. Loader success is accepted only while the capability is still
  `loading` — a load finishing after `LOAD_TIMEOUT_SECONDS` (or after shutdown)
  never revives `ready` and its engine is closed exactly once. Published
  engines/clients are stashed at shutdown and closed exactly once by whichever
  of {bounded shutdown drain, last in-flight worker} observes quiescence, so
  504 orphan workers never have their engine closed underneath them; shutdown
  never joins forever. Deadline criteria are instantiated per generation, so
  `QWEN_MAX_CONCURRENCY > 1` never shares deadline state.
- **Lifecycle**: extension startup runs after the core lifespan startup and
  never aborts it; extension shutdown (loader join ≤ 2 s, engine/client close)
  completes before core models close. Calling `install_extensions` twice
  raises `RuntimeError`.

## Manual verification checklist (operator, not automated here)

1. Create a dedicated venv for the Qwen profile:
   `pip install -r requirements-qwen.txt` (mind the dedicated-venv note above).
2. `ENABLE_QWEN=true` + `curl /api/capabilities` → watch `loading` → `ready`.
3. `curl -X POST /api/generate -d '{"model":"qwen3.5-0.8b","input":"hi"}'`.
4. `ENABLE_KITTEN=true` → `curl -X POST /api/speech -d '{"input":"hi"}' -o out.wav`
   → confirm WAV playback.
5. `ENABLE_GROQ_TRANSCRIPTION=true` + `GROQ_API_KEY=...` →
   `curl -X POST --data-binary @clip.wav -H "Content-Type: audio/wav"
   "/api/transcriptions"`.
