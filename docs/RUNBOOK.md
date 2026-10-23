# Runbook

## Local Python service

Use Python 3.12 and install `requirements-dev.txt` in a virtual environment.
Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python scripts/prepare_model.py --verify
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

macOS/Linux:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python scripts/prepare_model.py --verify
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

The first model preparation requires network access. Keep `API_KEY` empty only
for loopback-local work. Open `http://127.0.0.1:8000/` or follow the
[API contract](API_CONTRACT.md). Sample-photo provenance is in
`app/static/SAMPLES.md`; samples are functional demos, not an accuracy dataset.

## Tests and local container

```sh
python -m pip check
python scripts/check_dependencies.py
python -m pytest tests/ -q
node --test tests/frontend/studio-core.test.mjs
```

Build with `docker build -t ai-model-serving:local .`. On Windows, use
`scripts/test-container.ps1` for the hardened local-container checks. The
runtime image runs as UID 10001 with a read-only root, all capabilities dropped,
one worker, and writable scratch/cache mounts only.

## Kubernetes

Render manifests rather than applying template placeholders:

```sh
python scripts/render_k8s.py --image ai-model-serving:local --pull-secret none \
  --output .validation/rendered-local.yaml
kubectl --context ai-serving-local apply -f k8s/namespace.yaml
kubectl --context ai-serving-local apply -f .validation/rendered-local.yaml
kubectl --context ai-serving-local -n ai-serving rollout status deployment/ai-model-serving
kubectl --context ai-serving-local -n ai-serving get deploy,pods,svc,hpa
```

The cluster needs Metrics Server for CPU HPA decisions. The k6 ConfigMap must
contain both `loadtest/k6-script.js` and `loadtest/sample.jpg`; see the
[load-test guide](../loadtest/README.md). The bounded local runner requires an
idle one-replica deployment and waits for recovery between runs. Analyze its
preserved output with `scripts/analyze_experiments.py`.

## AWS

AWS resources are not part of local setup. Review [budget notes](BUDGET.md) and
the [AWS runbook](AWS_RUNBOOK.md), obtain explicit approval for billable
resources, and use the protected manual workflow. Access is through encrypted
SSM port forwarding; the service has no public endpoint.