# Kubernetes resources

Real manifests for the CPU sentiment workload. Full runbook: docs/KUBERNETES.md.
Never apply files that still contain unreplaced `__IMAGE__` / `__PULL_SECRET__`
values; render with scripts/render_k8s.py first.

| File | Resource |
| --- | --- |
| namespace.yaml | Namespace `ai-serving` |
| deployment.yaml | Deployment `ai-model-serving` (1 replica, probes, non-root, per-Pod emptyDir model cache, no ServiceAccount token) |
| service.yaml | NodePort Service `ai-model-serving` (30080 -> 8000) |
| hpa.yaml | autoscaling/v2 CPU HPA (min 1, max 3, target 70%) |
| load-generator.yaml | Opt-in k6 Job (requires ConfigMap `k6-script`, see file) |

Render and apply (cloud):

    python3 scripts/render_k8s.py --image <ECR-image:immutable-tag> \
        --pull-secret ecr-pull --output /tmp/ai-serving-rendered.yaml
    kubectl apply -f /tmp/ai-serving-rendered.yaml

Local cluster without a pull secret:

    python scripts/render_k8s.py --image ai-model-serving:dev \
        --pull-secret none --output .validation/rendered-local.yaml
    kubectl apply -f .validation/rendered-local.yaml

Verification (requires a reachable cluster with Metrics Server):

    kubectl -n ai-serving rollout status deployment/ai-model-serving
    kubectl -n ai-serving get deploy,pods,svc,hpa
    kubectl top nodes && kubectl top pods -n ai-serving
