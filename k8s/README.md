# Kubernetes resources

Manifests for the CPU object-detection service. See [the runbook](../docs/RUNBOOK.md)
for local and AWS operations. Never apply files that still contain unreplaced
`__IMAGE__` / `__PULL_SECRET__` values; render with `scripts/render_k8s.py` first.

| File | Resource |
| --- | --- |
| namespace.yaml | Namespace `ai-serving` |
| deployment.yaml | Deployment `ai-model-serving` (1 replica, probes, non-root, per-Pod emptyDir model cache, no ServiceAccount token) |
| service.yaml | NodePort Service `ai-model-serving` (30080 -> 8000) |
| hpa.yaml | autoscaling/v2 CPU HPA (min 1, max 3, target 70%) |
| load-generator.yaml | Opt-in k6 Job (ConfigMap must include the script and `sample.jpg`) |

Render and apply (cloud):

    python3 scripts/render_k8s.py --image <ECR-image:immutable-tag> \
        --pull-secret ecr-pull --output /tmp/ai-serving-rendered.yaml
    kubectl apply -f /tmp/ai-serving-rendered.yaml

Local cluster without a pull secret:

    python scripts/render_k8s.py --image ai-model-serving:detection \
        --pull-secret none --output .validation/rendered-local.yaml
    kubectl apply -f .validation/rendered-local.yaml

For the fresh Windows cluster, Secret setup, image import, Metrics Server,
authenticated smoke test and cleanup sequence, follow the
[runbook](../docs/RUNBOOK.md#fresh-windows-local-deployment).

Verification (requires a reachable cluster with Metrics Server):

    kubectl -n ai-serving rollout status deployment/ai-model-serving
    kubectl -n ai-serving get deploy,pods,svc,hpa
    kubectl top nodes && kubectl top pods -n ai-serving

The workload uses one worker per Pod and two inference slots based on the
recorded local calibration. A third Pod shares node CPU; this is scaling
behavior, not high availability. See [measured results](../results/ANALYSIS.md).
