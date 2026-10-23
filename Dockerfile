# CPU-only object detection image. Single Uvicorn worker, non-root, no GPU.
# Model assets are baked at build time from the pinned revision in
# app/model_registry.py, so the image never depends on a host model cache.
# Local clean build and hardened smoke: scripts/test-container.ps1.
# Executed evidence is recorded in docs/EVIDENCE.md; hosted CI is a separate gate.
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
# AGPL-3.0 license text inside the image (root-owned, world-readable).
RUN mkdir -p /opt/model-notices
COPY models/NOTICE.md models/LICENSE-AGPL-3.0.txt /opt/model-notices/

# Verify the same pinned weights and real sample detections used by local setup.
COPY scripts/prepare_model.py ./scripts/prepare_model.py
RUN python scripts/prepare_model.py --bake /opt/model-cache --verify \
 && chmod -R a+rX /opt/model-cache \
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
