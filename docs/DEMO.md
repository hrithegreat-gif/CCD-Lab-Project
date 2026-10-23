# Demonstration script

Target: 5?8 minutes after setup. Use the local Minikube profile and the actual
recorded Phase 3 evidence. Do not start a full benchmark during this short demo.

## Prepare before presenting

Complete Phase 1?2 setup. If the profile is stopped, set the disposable local
key and run `scripts/deploy-local.ps1`. In terminal A:

```powershell
& ./.validation/tools/minikube.exe -p ai-serving-local kubectl -- -n ai-serving port-forward service/ai-model-serving 8002:8000
```

In terminal B, verify the live Service before opening the UI:

```powershell
$env:API_KEY = 'local-review-only'
& ./.venv-review/Scripts/python.exe scripts/smoke_test.py --base-url http://127.0.0.1:8002
```

Use your configured key if different. Expect the live HTTP acceptance PASS.
Open `http://127.0.0.1:8002/` and enter the key through Session access. Do not
show keys in screenshots. A Service port-forward selects one Pod; the recorded
in-cluster k6 experiments exercise the multi-replica Service path.

## Timed walkthrough

| Time | Action | What to verify or explain |
| --- | --- | --- |
| 0:00?1:00 | Show architecture in FINAL_REPORT.md | Client ? Service ? FastAPI ? pinned CPU model; one node with fixed 4 CPU/6 GiB limit |
| 1:00?2:30 | Submit ?I love this product. It is absolutely wonderful!? and ?I hate this product. It is terrible and disappointing.? | Actual positive/negative output, scores and timings; no fixed timing promise |
| 2:30?3:30 | Show live Pods, Service, HPA and top output | Ready Pod, numeric CPU/memory; idle HPA does not need to be scaling |
| 3:30?5:00 | Open results/ANALYSIS.md and scaling figures | Explicitly label recorded 22 October experiments; 1 ? 3 ? 1 ready replicas; 104?117 successful rps, 57?59% 429s, missing metric samples |
| 5:00?6:00 | Show the exact delivered commit's Actions CI run | All required jobs green; match SHA, not an older unrelated run |
| 6:00?7:00 | Explain limits and preview cleanup | Shared hardware, no HA, no causal speedup control; cleanup preview must name only ai-serving in ai-serving-local |

Live inspection commands:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- -n ai-serving get pods,svc,hpa
& $mk -p ai-serving-local kubectl -- -n ai-serving top pods
$client = Join-Path $env:USERPROFILE '.minikube/cache/windows/amd64/v1.37.0/kubectl.exe'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/cleanup.ps1 -ExpectedContext ai-serving-local -Kubectl $client
```

Stop port forwarding with Ctrl+C afterward. Use [PHASE_4.md](PHASE_4.md) for
actual namespace deletion, redeployment and profile stop/resume. The deletion
removes local Secrets and Jobs; preserve evidence first. No AWS or paid-provider
step is needed. Optional capabilities remain disabled unless independently
accepted. Confirm team contributions in [FINAL_REPORT.md](FINAL_REPORT.md).
