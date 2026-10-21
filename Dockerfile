# CPU-only sentiment inference image. Single Uvicorn worker, non-root, no GPU.
# Model assets are baked at build time from the pinned revision in
# app/model_registry.py, so the image never depends on a host model cache.
# Build verification: CI docker job is configured but NOT yet executed here
# (the Docker daemon was unavailable on the dev machine); the container smoke
# test runs in CI before any image is pushed.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MODEL_CACHE_DIR=/opt/model-cache \
    TMPDIR=/tmp

WORKDIR /app

# Pinned runtime dependencies only (requirements.txt is exact-pinned).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# The image redistributes the baked model export: keep attribution and the
# Apache-2.0 license text inside the image (root-owned, world-readable).
RUN mkdir -p /opt/model-notices
COPY models/NOTICE.md models/LICENSE-APACHE-2.0.txt /opt/model-notices/

# Bake the allowlisted sentiment assets (config/tokenizer/quantized weights)
# into a root-owned, world-readable store. Uses the same pinned registry as the
# application, so image and code can never drift apart. The step verifies real
# positive and negative predictions; a failed download or wrong label breaks
# the build.
RUN python -c "import json; \
from pathlib import Path; \
from app.config import Settings; \
from app.inference import SentimentModel, download_assets; \
from app.model_registry import SENTIMENT; \
settings = Settings(_env_file=None, model_cache_dir=Path('/opt/model-cache')); \
assets = download_assets(SENTIMENT, settings); \
model = SentimentModel(SENTIMENT, settings); \
model.load(); \
assert all(model.predict(text).label == expected for text, expected in (('I love this product. It is absolutely wonderful!', 'positive'), ('I hate this product. It is terrible and disappointing.', 'negative'))); \
print(json.dumps({'baked': SENTIMENT.source, 'revision': SENTIMENT.revision, 'bytes': {name: path.stat().st_size for name, path in assets.items()}})); \
model.close()" \
 && touch /opt/model-cache/.model-baked

# Run as an unprivileged user. Kubernetes mounts emptyDir volumes over
# /cache and /tmp; locally the baked cache is used read-only and no writes
# to the image filesystem are expected.
RUN groupadd --gid 10001 appgroup \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin appuser

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=90s --retries=4 \
  CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

# One worker: inference concurrency is bounded by the application semaphore,
# and HPA adds Pods (not processes) on the 2 vCPU node.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
