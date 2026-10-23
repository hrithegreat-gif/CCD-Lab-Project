# CCD Lab: CPU object detection and serving

FastAPI serves a pinned YOLOv8n ONNX model on CPU. The repository includes an
image-upload Studio, bounded inference and request limits, health and metrics
endpoints, a hardened container, Kubernetes/HPA manifests, load-test tooling,
and prepared Terraform/GitHub Actions deployment infrastructure.

[Source code](https://github.com/hrithegreat-gif/CCD-Lab-Project) ·
[API contract](docs/API_CONTRACT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Runbook](docs/RUNBOOK.md) · [Model provenance and license](models/README.md)

## Quick start

Requires Python 3.12 and internet access for the pinned model release asset.
From the repository root, create a virtual environment and install development
dependencies.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

macOS/Linux:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Then, on either platform:

```sh
python scripts/prepare_model.py --verify
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Open `http://127.0.0.1:8000/` for the Studio. The sample photos are documented
in [app/static/SAMPLES.md](app/static/SAMPLES.md). To call the API directly:

```sh
curl --data-binary @app/static/sample-cat.jpg \
	-H 'Content-Type: image/jpeg' \
	'http://127.0.0.1:8000/api/detect?conf=0.25&iou=0.45'
```

The example `.env` leaves `API_KEY` empty for local development. A nonempty key
protects `/api/*` and `/metrics` with Bearer authentication; never use an empty
key for a network-accessible deployment.

## API and runtime

`POST /api/detect` accepts raw JPEG, PNG, or WebP bytes. It verifies content
type and image signature, limits compressed bytes and decoded pixels, applies
EXIF orientation, and returns detections in original-image pixel coordinates.
See the [API contract](docs/API_CONTRACT.md) for response fields and errors.

The service uses one Uvicorn worker per Pod and a bounded inference semaphore.
Kubernetes scales Pods, not worker processes. Three Pods on a single node share
that node's CPU and are not high availability. The current local Minikube
measurements and their sampling limits are in [results/ANALYSIS.md](results/ANALYSIS.md).

For a local container, run `docker build -t ai-model-serving:local .` and use
the hardened smoke helper `scripts/test-container.ps1` on Windows. The image
bakes the pinned model at build time and runs as UID 10001. It does not require
PyTorch or Ultralytics at serving time.

## Deployment and evidence

Kubernetes resources are in [k8s/](k8s/); operational commands are in the
[runbook](docs/RUNBOOK.md). AWS deployment is an owner-authorized, manually
dispatched path using OIDC, SSM, private ECR/S3, and an encrypted SSM tunnel.
No AWS deployment or cloud HPA acceptance is claimed yet. See the step-by-step
[AWS setup guide](docs/AWS_SETUP.md), [AWS runbook](docs/AWS_RUNBOOK.md),
[CI/CD](docs/CI_CD.md), and [budget notes](docs/BUDGET.md).

The model and upstream YOLOv8 code are distributed under AGPL-3.0. Review the
license obligations for your use before shipping; this project notice is not
legal advice. See [models/NOTICE.md](models/NOTICE.md). The source repository
is linked above. No AWS resources are created by local validation.
