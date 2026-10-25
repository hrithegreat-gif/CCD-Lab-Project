# Runbook

## Fresh Windows local deployment

The tested local Kubernetes path is Windows 11 with Docker Desktop using Linux
containers, Minikube, and kubectl. The following commands use one image tag,
`ai-model-serving:detection`. The application and Python tests are also
available on macOS/Linux, but the end-to-end Kubernetes helper is Windows-only;
no portable macOS/Linux cluster sequence is currently maintained.

### Prerequisites

- Windows 11, PowerShell, and Docker Desktop started in Linux-container mode.
- Python 3.12.
- Node.js 22 for the frontend contract tests only.
- `uv` on `PATH` for `scripts/setup-phase1.ps1`.
- Minikube and kubectl on `PATH`, or install the project-local Minikube binary
  with `scripts/install-local-tools.ps1`. The installer stores its verified
  binary under `.validation/tools/`; `scripts/deploy-local.ps1` uses that path
  automatically. The experiment runner still requires a `kubectl` executable
  on `PATH`.
- Internet access for Python packages, the pinned model asset, and optional
  local-tool installation.

From a fresh checkout, run in PowerShell at the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\setup-phase1.ps1
.\.venv-review\Scripts\python.exe scripts\prepare_model.py --verify
node --test tests/frontend/studio-core.test.mjs
docker info --format '{{.OSType}}'
```

If Minikube is not installed globally, install the checksum-verified,
project-local copy:

```powershell
.\scripts\install-local-tools.ps1
```

Build and exercise the hardened image before deployment. Set a throwaway,
nonempty local API key in the same PowerShell session that runs the helper;
never reuse a production credential:

```powershell
$env:API_KEY = [guid]::NewGuid().ToString('N')
.\scripts\test-container.ps1 -Image ai-model-serving:detection
.\scripts\deploy-local.ps1 -Image ai-model-serving:detection
```

The deployment helper creates the fixed `ai-serving-local` Minikube profile,
imports the exact image tag, enables Metrics Server, creates the `ai-serving`
namespace and `serving-secrets` Secret from `API_KEY` via stdin, renders the
manifests, waits for application and Metrics Server rollouts, verifies HPA
metrics, and checks for startup restarts. It does not inspect or change AWS
contexts. It writes rendered manifests beneath the ignored `.validation/`
directory; do not commit rendered Secret material.

### Verify and access the service

In one PowerShell window, start a local tunnel to the Service:

```powershell
kubectl --context ai-serving-local -n ai-serving port-forward service/ai-model-serving 8000:8000
```

Keep it running. In a second window, set the same local key and run the
real-model smoke acceptance:

```powershell
$env:API_KEY = '<the same throwaway key>'
.\.venv-review\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000/` for the Studio. The smoke test exercises real
detection, authentication, rejection paths, metadata, readiness, and metrics.
To inspect scaling metrics and the initial HPA state:

```powershell
kubectl --context ai-serving-local get nodes
kubectl --context ai-serving-local top nodes
kubectl --context ai-serving-local -n ai-serving get deploy,pods,svc,hpa
kubectl --context ai-serving-local -n ai-serving top pods
kubectl --context ai-serving-local -n ai-serving describe hpa ai-model-serving
```

Run a bounded, single measured experiment only when the runner reports one
ready idle replica and working HPA metrics. It may take up to 27 minutes because
it waits for the scale-down stabilization window and recovery:

```powershell
python scripts\run_experiments.py --kubectl kubectl --context ai-serving-local `
  --prefix detection-YYYYMMDD --runs 1
python scripts\analyze_experiments.py results\raw\detection-YYYYMMDD
```

Use a unique prefix each time. Preserve generated CSV rows, plots, and evidence;
do not hand-edit measurements.

### Common failures and cleanup

- **ImagePullBackOff:** confirm Docker is using Linux containers, rebuild the
  `ai-model-serving:detection` tag, and rerun `scripts/deploy-local.ps1` so the
  helper imports that same tag into Minikube.
- **Missing Secret or 401:** set a nonempty `API_KEY` in the deployment shell
  and rerun the helper; set the identical key in the smoke-test shell.
- **Model not ready / crash loop:** inspect
  `kubectl --context ai-serving-local -n ai-serving describe pod <pod>` and
  `kubectl --context ai-serving-local -n ai-serving logs deployment/ai-model-serving`.
  Rebuild the image if its baked model asset is missing or fails digest checks.
- **HPA shows unknown metrics:** wait for Metrics Server to become ready, then
  inspect `kubectl --context ai-serving-local -n kube-system get pods`,
  `kubectl --context ai-serving-local -n ai-serving describe hpa ai-model-serving`,
  and `kubectl --context ai-serving-local top nodes`.
- **Port-forward fails:** verify the Deployment and Service are ready before
  retrying `kubectl port-forward`.

The namespace cleanup script is preview-only unless explicitly confirmed. From
the repository root:

```powershell
.\scripts\cleanup.ps1 -ExpectedContext ai-serving-local
.\scripts\cleanup.ps1 -ExpectedContext ai-serving-local -Apply -ConfirmNamespace ai-serving
minikube stop -p ai-serving-local
```

The cleanup script deletes only the `ai-serving` namespace in the exact named
context; it does not delete the Minikube profile or touch AWS. The generated
model cache and local tools under `.validation/` may be kept for later runs.

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

Build with `docker build -t ai-model-serving:detection .`. On Windows, use
`scripts/test-container.ps1` for the hardened local-container checks. The
runtime image runs as UID 10001 with a read-only root, all capabilities dropped,
one worker, and writable scratch/cache mounts only.

## Kubernetes

For a new Windows cluster, follow **Fresh Windows local deployment** above.
The manual steps below are for an already configured cluster with the
`ai-serving-local` context and required Secret in place. Render manifests
rather than applying template placeholders:

```sh
python scripts/render_k8s.py --image ai-model-serving:detection --pull-secret none \
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