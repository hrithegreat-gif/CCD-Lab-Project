# Development and deployment environments

## Phase 4 refresh on 23 October 2026

The dedicated namespace was deleted and recreated successfully. Real authenticated
Service smoke passed after recovery, with one ready Pod and zero restarts.
The `ai-serving-local` profile was then stopped to release compute. Resume with
`scripts/deploy-local.ps1` and the intended local API key. Earlier descriptions
of a running cluster are historical. Hosted CI and exact commit/run identity
are recorded in [VERIFICATION.md](VERIFICATION.md).

## Phase 2 refresh on 22 October 2026

- Docker Desktop Linux engine: 29.2.1; clean build and hardened smoke executed.
- Project-local Minikube: v1.39.0, checksum verified; profile `ai-serving-local`.
- Kubernetes: v1.37.0; containerd 2.3.4; Metrics Server v0.9.0.
- Docker node-container limits: 4 CPUs and 6 GiB; Kubernetes reports the larger
  WSL host capacity (16 CPUs, 12,087,488 KiB). Record both for experiments.
- Global kubectl v1.34.1 is too old for this cluster. Use Minikube's matching
  client as shown in [PHASE_2.md](PHASE_2.md); deployment helpers do this already.
- Image tag: `ai-model-serving:phase2`; actual digest and checks are in
  [VERIFICATION.md](VERIFICATION.md).
- Local cluster is available for testing. Port forwards and disposable smoke
  containers have been cleaned up; Docker Desktop remains running.

## Phase 1 refresh on 21 October 2026

- Windows platform reported by Python: `Windows-11-10.0.26200-SP0`.
- Python 3.12.14 now runs from `.venv-review`, backed by the project's ignored
  `.validation/python` installation; the original broken `.venv` is preserved.
- uv: 0.12.5. Cache: `.validation/uv-cache`.
- Node: v24.14.0. Browser acceptance used installed Microsoft Edge with
  Playwright 1.58.0 in the local test environment.
- The pinned model cache works offline; model assets total 68,293,328 bytes.
- CPU/RAM CIM queries were denied in the execution environment, so the older
  hardware figures below were not remeasured.
- Run `scripts/setup-phase1.ps1` as documented in [PHASE_1.md](PHASE_1.md).
  [VERIFICATION.md](VERIFICATION.md) records the fresh passing acceptance checks.

The remaining table is the older environment record; versions there are historical.

Recorded 2026-10-13 on the Windows validation host. These are not EC2 or container measurements.

| Field | Observed value |
| --- | --- |
| OS | Windows 11 Home Single Language, 10.0.26200 |
| Shell | PowerShell; Git Bash syntax checks; Ubuntu WSL2 for GNU tar safety tests and final shell syntax checks |
| Python | 3.12.14, managed by uv 0.12.17 |
| CPU | AMD Ryzen 7 8845HS; 8 physical cores / 16 logical processors |
| Reported physical RAM | 33,615,630,336 bytes (about 31.3 GiB) |
| Available C: disk at inspection | About 92.3 GiB; recheck before building |
| Docker client | 29.4.3; Linux daemon unavailable, not started |
| Docker RAM allocation | Not measured; no container execution |
| kubectl client | v1.34.1, windows/amd64; no configured cluster contexts |
| Git / Node | 2.51.0.windows.1 / v25.4.0 |
| Terraform | 1.15.8, windows_amd64; AWS provider pinned to 5.100.0 |
| Core model | Quantized CPU ONNX DistilBERT SST-2; immutable revision and license in [model provenance](../models/README.md) |

## Planned cloud environment — not provisioned

Mumbai (`ap-south-1`), one x86 `m7i-flex.large` (2 vCPU / 8 GiB), encrypted 40 GiB gp3, pinned k3s from [Terraform](../infra/terraform/variables.tf). Metrics Server is enabled. Runtime Pods use a baked offline model, one native inference thread per process, and the limits in the [Deployment](../k8s/deployment.yaml).

The local kubectl v1.34 client is **outside the supported one-minor skew** for the planned k3s v1.37 server. Use the node's bundled `k3s kubectl` through SSM, or install a compatible client before using a local kubeconfig. No local cluster distribution has been selected or tested.

Local sentiment smoke results cannot establish EC2/container throughput, memory fit, HPA behavior, or optional-model performance. Record those separately on the actual target.

## Recheck commands

```powershell
uv --version
uv run --python 3.12 --no-project python --version
git --version
docker version
kubectl version --client
kubectl config get-contexts
terraform version
```

A failing Docker daemon check is a prerequisite failure, not a successful image validation. Do not run Kubernetes apply/cleanup without selecting and reviewing an explicit intended context. Cloud operations require the authorization gates in the [AWS runbook](AWS_RUNBOOK.md).
