# Kubernetes deployment

The primary local path is [Phase 2: Minikube on Docker](PHASE_2.md). It includes
checksum-verified tool installation, hardened container acceptance, image import,
namespace/secret setup, real Service inference, and Metrics Server verification.
Use Minikube's version-matched kubectl; an older globally installed client may
be outside the supported version skew. The EC2/k3s instructions below describe
the separate optional cloud target, not evidence of an executed cloud deployment.

## Optional self-managed k3s on EC2

CPU-only serving lab: one x86 Linux EC2 instance (`m7i-flex.large`, 2 vCPU /
8 GiB), self-managed k3s (bootstrap pins `v1.37.1+k3s1`, Metrics Server 0.9.0,
traefik + servicelb disabled), reached only through an SSM port-forwarding
session. There is no EKS, ALB, public ingress, TLS terminator or LoadBalancer:
the security group has no inbound access and the NodePort is tunneled.

## Resources (k8s/)

| File | Resource | Notes |
| --- | --- | --- |
| namespace.yaml | Namespace `ai-serving` | fixed by contract |
| deployment.yaml | Deployment `ai-model-serving` | 1 replica, non-root, probes, per-Pod cache |
| service.yaml | NodePort Service `ai-model-serving` | 30080 -> 8000, inside the node network only |
| hpa.yaml | HPA `ai-model-serving` | autoscaling/v2, CPU 70%, min 1 / max 3 |
| load-generator.yaml | k6 Job `load-generator` | opt-in, needs the `k6-script` ConfigMap |

## Image

- `python:3.12-slim-bookworm`, one Uvicorn worker, `--no-access-log` (structured
  app logs only; route/query inputs are never logged).
- Non-root: uid/gid 10001, read-only root filesystem, all capabilities dropped,
  no ServiceAccount token, `seccompProfile: RuntimeDefault`.
- Model assets are baked at build time from the pinned revision in
  app/model_registry.py (verified in the build with real positive/negative
  predictions). The image also carries models/NOTICE.md and the Apache-2.0
  license text under /opt/model-notices because it redistributes the export.
- The startup entrypoint copies the baked cache into the per-Pod writable
  emptyDir mounted at `/cache` (`MODEL_CACHE_DIR=/cache`,
  `MODEL_LOCAL_FILES_ONLY=true`): no runtime model downloads, no host-cache
  dependency, and a fresh writable cache per Pod.

## Secret

`serving-secrets` (keys `api-key` required, `groq-api-key` optional) is created
at deploy time by scripts/deploy-k3s.sh from SSM SecureString parameters
`$SSM_PARAMETER_PREFIX/api-key` and `$SSM_PARAMETER_PREFIX/groq-api-key` and is
never printed. Local secret creation may use an empty `api-key` (authentication
disabled); cloud deployments require a nonempty key:

    kubectl -n ai-serving create secret generic serving-secrets \
        --from-literal=api-key=''

Never commit Secret manifests; tests/test_manifests.py asserts none exist.

## Local image loading (no registry)

PowerShell, after the tool installation in [PHASE_2.md](PHASE_2.md):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-container.ps1
$env:API_KEY = 'local-review-only'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-local.ps1
```

`imagePullSecrets` is only injected when render runs with `--pull-secret`;
local clusters load the image into the node instead. The deployment helper imports
the built image explicitly into the dedicated Minikube profile. See
[PHASE_2.md](PHASE_2.md) for the complete local image-import and verification
procedure. The EC2 k3s deployment below is a prepared optional path, not a claim
of verified AWS deployment. See [VERIFICATION.md](VERIFICATION.md) for evidence.

## Deployment on the instance (scripts/deploy-k3s.sh)

Runs as root with cwd = extracted bundle root
(`/opt/ccd-lab/deploy-bundles/<sha>`); requires the bootstrap environment
`AWS_REGION` and `SSM_PARAMETER_PREFIX` (default `/ccd-lab`):

    scripts/deploy-k3s.sh --image <ECR-image:immutable-tag>

For registry-form images the script enforces ownership before touching
anything: the image must be
`<own-account>.dkr.ecr.ap-south-1.amazonaws.com/<ECR_REPOSITORY>:<40-hex sha>`
(own account read via `aws sts get-caller-identity`; moving tags and other
registries are rejected).

Order: preflight (kubectl via the k3s shim or `k3s kubectl`, python3, aws) ->
apply the namespace FIRST (secrets cannot be created in a namespace that does
not exist) -> fetch a nonblank, trimmed `api-key` (+ optional `groq-api-key`)
from SSM -> create/update `serving-secrets` -> refresh the `ecr-pull`
imagePullSecret from an ECR token (both through stdin; values never printed) ->
apply the rendered manifests -> wait for rollout -> probe
`http://127.0.0.1:30080/health/{ready,live}` on the node -> print status. It
also runs `mkdir -p ./results` so the SSM results sync never fails when no
measurements exist. kubeconfig: `/etc/rancher/k3s/k3s.yaml`.

## SSM access (default; private by design)

    aws ssm start-session --target <instance-id> \
        --document-name <SSM-port-forward-document> \
        --parameters '{"portNumber":["30080"],"localPortNumber":["30080"]}'
    curl http://127.0.0.1:30080/health/ready
    # authenticated call:
    curl -s -H "Authorization: Bearer $API_KEY" \
        -H 'Content-Type: application/json' \
        -d '{"model":"sentiment","input":"I love this product."}' \
        http://127.0.0.1:30080/api/inference

`/api/*` requires Bearer auth (`API_KEY`); `/health/live` and `/health/ready`
are public for probes; `/metrics` is protected with the same key.

## Probes and sizing

- startupProbe `/health/ready` every 5s (up to ~2 min: cache copy + load),
  readiness `/health/ready` 10s, liveness `/health/live` 15s. With
  `BACKGROUND_MODEL_LOAD=true` liveness stays available during warm-up.
- requests 500m/512Mi, limits 1/1Gi per Pod. HPA max 3 fits the node on
  requests (3 x 512Mi + k3s system overhead < 8 GiB; 3 x 500m + system
  requests < 2 vCPU allocatable). Limits over-commit CPU via time slicing and
  add no compute; additional replicas never make the node faster.
- Rolling update `maxSurge: 0, maxUnavailable: 1`: pod count stays within HPA
  maxReplicas on one node (a surge Pod would sit Pending); a single replica is
  briefly unavailable during rollouts — this lab has no HA.
- HPA scale-up 1 Pod/60s; scale-down 1 Pod/60s with a 300s stabilization
  window (matches the documented scale-in observation wait).

## Metrics Server verification (do not disable TLS flags)

k3s ships Metrics Server; verify real metrics before trusting the HPA:

    kubectl top nodes
    kubectl top pods -n ai-serving
    kubectl describe hpa ai-model-serving -n ai-serving   # look at Metrics/Events

Do not add `--kubelet-insecure-tls` or similar insecure flags by default; the
bootstrap's Metrics Server (0.9.0) works with node TLS. If `kubectl top`
returns nothing right after deploy, wait for scrape warm-up before diagnosing.

## HPA/scaling experiments

    kubectl -n ai-serving create configmap k6-script \
        --from-file=k6-script.js=loadtest/k6-script.js
    kubectl -n ai-serving apply -f k8s/load-generator.yaml
    python scripts/record_scaling.py --duration 660 --interval 5   # second terminal
    kubectl -n ai-serving get hpa -w

Recording must span warmup (15s) + baseline (30s) + sustained (180s default)
PLUS the 300s scale-in stabilization window and margin — hence 660s; scale it
up proportionally if LOADTEST_DURATION is raised. Expect scale-out under
sustained load, then scale-in after the 300s stabilization window. More Pods
do not add node CPU — one replica is the performance baseline. The k6 script
disables connection reuse (keep-alive would pin VUs to the first Pod and
starve scale-out replicas). See loadtest/README.md.

## Verification status (honest record)

- Verified offline on the dev machine: YAML/manifest validation
  (tests/test_manifests.py), render checks, `bash -n` of the shell scripts.
- NOT yet executed anywhere: docker build (daemon unavailable), kubectl apply
  against a real cluster, HPA scaling runs. The CI docker job and the EC2
  deploy are the execution gates; update this list when they run.
- results/performance.csv stays header-only until a real measured run.
