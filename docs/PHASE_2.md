# Phase 2 container and local Kubernetes verification

This phase proves the existing sentiment application in a hardened Docker
container and then a local Kubernetes cluster. It also verifies Metrics Server
and usable HPA CPU metrics. Load-driven scale-out/scale-in and performance
benchmarks belong to Phase 3.

Run the commands from the repository root in **Windows PowerShell**. Phase 1's
`.venv-review` must already work. Docker Desktop must be running **Linux
containers**. Initial builds and cluster creation download images and model
assets; allow time and disk space. These commands create only local resources.

## 1. Install the local cluster tool

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install-local-tools.ps1
& ./.validation/tools/minikube.exe version
docker info --format '{{.OSType}} {{.ServerVersion}}'
```

The installer downloads Minikube v1.39.0 into ignored `.validation/tools`, checks
its published SHA-256 before activating it, and does not modify PATH. Expected:
Minikube prints its version and Docker reports `linux` and a server version.
The process-only execution policy does not change system/user policy.

Tool references: [official installation](https://minikube.sigs.k8s.io/docs/start/),
[image commands](https://minikube.sigs.k8s.io/docs/commands/image/), and
[matching kubectl](https://minikube.sigs.k8s.io/docs/commands/kubectl/).

## 2. Build and verify the hardened container

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-container.ps1 -NoCache
```

This builds `ai-model-serving:phase2` without cache, starts a uniquely named
temporary container on loopback port 8001, and tests:

- Baked model assets used offline in a writable cache, with no host cache mount.
- Read-only root filesystem, dropped capabilities, 1 CPU and 1 GiB RAM limit.
- UID 10001, health/readiness, real positive and negative sentiment.
- A root-owned, group-writable cache mount, reproducing Kubernetes `emptyDir`
  ownership rather than masking copy failures with an application-owned mount.
- Authenticated model access, unauthenticated rejection, 404/422 errors, metrics.
- Installed dependency consistency inside Linux.

The script waits up to 180 seconds for readiness, exits on failure, and removes
only its own temporary container in `finally`. Its random test key is passed via
the environment and restored afterward. Successful completion prints:

```text
PASS: hardened container, real inference, authentication, dependencies and UID.
```

To repeat acceptance without rebuilding:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-container.ps1 -SkipBuild
```

Use `-Port 8005` if port 8001 is occupied. Resource snapshots printed by the script
are observations, not load-test results. Inspect logs printed on failure before
continuing. The image remains available for Kubernetes import.

## 3. Deploy the local cluster

```powershell
$env:API_KEY = 'local-review-only'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-local.ps1
```

This disposable key is for a local demo only. The script creates/reuses the fixed
`ai-serving-local` Minikube profile with Kubernetes v1.37.0, 4 CPUs and 6144 MiB RAM, imports the
tested image, enables Metrics Server, creates the namespace and Secret, renders
the manifests, rolls out the application and waits for usable HPA metrics.
Every Kubernetes command names the dedicated context explicitly. It uses
Minikube's version-matched kubectl instead of an older global client.

The secret is sent on stdin, not saved in a repository file. Rerunning restarts
the Deployment so a changed environment key takes effect. Reuse the same key
for subsequent smoke/UI tests. A same-tag image update also requires rebuilding
and rerunning deployment; the script imports the image again.

Expected final message:

```text
PASS: local rollout and metrics. Complete Service smoke checks in docs/PHASE_2.md.
```

Inspect the actual resources:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- get nodes
& $mk -p ai-serving-local kubectl -- -n ai-serving get pods,svc,hpa
& $mk -p ai-serving-local kubectl -- top nodes
& $mk -p ai-serving-local kubectl -- -n ai-serving top pods
& $mk -p ai-serving-local kubectl -- -n ai-serving describe hpa ai-model-serving
```

Expected: node Ready, application Pod `1/1 Running`, Service port 8000/NodePort
30080, HPA bounds 1–3, CPU target 70%, numeric resource readings and
`ScalingActive=True`. A brief `<unknown>` during initial sampling is normal;
persistent unknown metrics fail the phase. Idle CPU does not need to reach 70%.
New application Pods should have zero restarts. The helper rejects startup
restarts even if a subsequent retry becomes ready; inspect `logs --previous`.

In this Docker/WSL setup Kubernetes reports the host's 16 CPUs and approximately
11.5 GiB RAM, while Docker limits the node container to 4 CPUs/6 GiB. Interpret
capacity and load-test results using the enforced container limits, not only
the node percentages. The exact observations are in the verification ledger.

## 4. Verify real inference through the Service

In terminal A, keep this command running:

```powershell
& ./.validation/tools/minikube.exe -p ai-serving-local kubectl -- -n ai-serving port-forward service/ai-model-serving 8002:8000
```

In terminal B:

```powershell
$env:API_KEY = 'local-review-only'
& ./.venv-review/Scripts/python.exe scripts/smoke_test.py --base-url http://127.0.0.1:8002
```

Expected: all smoke checks pass, including real predictions. Open
`http://127.0.0.1:8002/` and enter the same key in Session access to try the UI.
Health remains public. A Service port-forward selects a Pod and is a functional
check, not evidence of balancing traffic across replicas. Phase 3's in-cluster
load generator tests the multi-replica Service path.

To verify cluster DNS and Service routing independently of port forwarding:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
@'
import urllib.request
print(urllib.request.urlopen('http://ai-model-serving.ai-serving.svc.cluster.local:8000/health/ready').read().decode())
'@ | & $mk -p ai-serving-local kubectl -- -n ai-serving exec -i deployment/ai-model-serving -- python -
```

Expected: a JSON response with `status` equal to `ready`.

## 5. Verify rollout recovery

Keep terminal A's port-forward stopped during this check:

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- -n ai-serving rollout restart deployment/ai-model-serving
& $mk -p ai-serving-local kubectl -- -n ai-serving rollout status deployment/ai-model-serving --timeout=240s
& $mk -p ai-serving-local kubectl -- -n ai-serving get pods
```

The replacement Pod must become ready using the baked offline model. Restart
port forwarding and repeat the smoke. Single-replica updates may briefly
interrupt service because the manifest intentionally uses `maxSurge: 0` and
`maxUnavailable: 1`; this is not a high-availability deployment.

## Troubleshooting

The first live deployment exposed an entrypoint defect: archive-mode copying
tried to preserve timestamps on a root-owned cache mount, exited once, and left
a completion marker that allowed the retry to skip copying. The entrypoint now
copies entries without archive metadata and writes its marker only after success.
The Docker/CI smoke mounts reproduce this ownership to catch regressions.

```powershell
$mk = Join-Path (Get-Location) '.validation/tools/minikube.exe'
& $mk -p ai-serving-local kubectl -- -n ai-serving describe pods
& $mk -p ai-serving-local kubectl -- -n ai-serving logs deployment/ai-model-serving --tail=100
& $mk -p ai-serving-local kubectl -- -n ai-serving get endpointslices
& $mk -p ai-serving-local kubectl -- -n ai-serving get events --sort-by=.metadata.creationTimestamp
& $mk -p ai-serving-local kubectl -- -n kube-system logs deployment/metrics-server --tail=100
```

| Symptom | Check/fix |
| --- | --- |
| Docker pipe unavailable | Start Docker Desktop and wait for `docker info` to succeed in Linux mode |
| Minikube download/checksum failure | Check connectivity, rerun installer; never execute a mismatched download |
| Cluster allocation fails | Check Docker/WSL capacity; adjust `-Cpus`/`-MemoryMb` to fit and record the allocation |
| `ImagePullBackOff` | Verify image tag and successful `minikube image load`; host Docker images are not automatically cluster images |
| `Pending` | Inspect scheduler events and requests against allocatable CPU/RAM |
| Readiness or cache errors | Inspect application logs and security/volume configuration; do not disable probes to make status green |
| 401 from smoke | Set the client key to the same key used by deployment; redeploy to refresh a changed Secret |
| HPA unknown CPU | Check Metrics Server readiness/logs, `top pods`, CPU requests and HPA events |
| Global kubectl version warning | Use the Minikube kubectl commands above; the installed global client may be too old |

## Stop and resume

Stop port forwarding with Ctrl+C, then release local compute without deleting
the profile or deployment:

```powershell
& ./.validation/tools/minikube.exe stop -p ai-serving-local
```

To resume, rerun the deployment command with the intended local key. To remove
only this project's cluster permanently, use `minikube delete -p ai-serving-local`
after saving evidence. Neither command manages AWS resources.

## Completion checklist

- Clean image build and hardened real-inference smoke succeed.
- Local image import, namespace/secret ordering and rollout succeed.
- Ready Pods and Service routing produce real predictions.
- Metrics Server returns actual CPU/memory and HPA receives metrics.
- Replacement Pod readiness and inference recover after rollout restart.
- Evidence is recorded in [VERIFICATION.md](VERIFICATION.md); no benchmark or
  autoscaling result is inferred from these functional checks.
