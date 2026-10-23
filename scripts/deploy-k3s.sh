#!/usr/bin/env bash
# Deploy the ai-serving workload to the local k3s node (EC2 instance).
#
# Usage:
#   scripts/deploy-k3s.sh --image <ECR-image:immutable-tag> [--wait-timeout 600]
#
# Runs as root on the instance with the cwd set to the extracted bundle root
# (e.g. /opt/ccd-lab/deploy-bundles/<sha>). Required environment from the
# infra bootstrap: AWS_REGION, SSM_PARAMETER_PREFIX (default /ccd-lab) and,
# for registry-form images, ECR_REPOSITORY (ownership validation).
#
# What it does, in order:
#   1. preflight kubectl (k3s shim or k3s kubectl), python3, aws, curl/python;
#      for registry-form images, enforce ownership: the image must be
#      <own-account>.dkr.ecr.ap-south-1.amazonaws.com/<ECR_REPOSITORY>:<40hex>
#      (own account read via aws sts get-caller-identity)
#   2. apply the namespace BEFORE any Secret is created in it
#   3. fetch the required NONBLANK SSM SecureString $SSM_PARAMETER_PREFIX/api-key
#      (trimmed); secret values are never printed
#   4. create/update Secret serving-secrets from those values via stdin
#   5. refresh the ecr-pull imagePullSecret from an ECR token via stdin
#   6. render k8s manifests with the given image and apply them
#   7. wait for the rollout and check the public health endpoints through the
#      NodePort on localhost:30080
#   8. print a short status summary; also `mkdir -p ./results` so the SSM
#      results sync never fails even when no measurements exist
set -euo pipefail

NAMESPACE="ai-serving"
DEPLOYMENT="ai-model-serving"
NODEPORT=30080
SSM_PARAMETER_PREFIX="${SSM_PARAMETER_PREFIX:-/ccd-lab}"
WAIT_TIMEOUT=600
IMAGE=""

die() { printf 'deploy-k3s: FAIL: %s\n' "$1" >&2; exit 1; }
info() { printf 'deploy-k3s: %s\n' "$1"; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --image)
            [ "$#" -ge 2 ] || die "--image requires a value"
            IMAGE="$2"; shift 2 ;;
        --wait-timeout)
            [ "$#" -ge 2 ] || die "--wait-timeout requires a value"
            WAIT_TIMEOUT="$2"; shift 2 ;;
        --help|-h)
            sed -n '2,29p' "$0"; exit 0 ;;
        *)
            die "unknown argument: $1 (see --help)" ;;
    esac
done

case "$IMAGE" in
    ""|" "*|*" "*|__IMAGE__*)
        die "--image <ECR-image:immutable-tag> is required (got: '$IMAGE')" ;;
esac
case "$WAIT_TIMEOUT" in
    ""|*[!0-9]*)
        die "--wait-timeout must be a positive integer (seconds)" ;;
esac
if [ "$WAIT_TIMEOUT" -lt 30 ] || [ "$WAIT_TIMEOUT" -gt 3600 ]; then
    die "--wait-timeout must be between 30 and 3600 seconds"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# The SSM document syncs ./results/ after deployment; make sure it exists
# even when no load measurements have been produced.
mkdir -p ./results "$REPO_ROOT/results"

# --- preflight --------------------------------------------------------------
if command -v kubectl >/dev/null 2>&1 && kubectl get namespace >/dev/null 2>&1; then
    KUBECTL=(kubectl)
elif command -v k3s >/dev/null 2>&1; then
    KUBECTL=(k3s kubectl)
else
    die "kubectl not reachable and k3s not installed (bootstrap incomplete?)"
fi
command -v python3 >/dev/null 2>&1 || die "python3 is required on the instance"
command -v aws >/dev/null 2>&1 || die "aws CLI is required on the instance"
if ! command -v curl >/dev/null 2>&1; then
    HTTP_PROBE="python3"
else
    HTTP_PROBE="curl"
fi
[ -f "$REPO_ROOT/scripts/render_k8s.py" ] || die "scripts/render_k8s.py not found next to this script"
[ -d "$REPO_ROOT/k8s" ] || die "k8s/ directory not found next to this script"

ECR_REGION="${AWS_REGION:-}"
[ -n "$ECR_REGION" ] || die "AWS_REGION must be exported by the infra bootstrap"

if [[ "$IMAGE" == *"/"* ]]; then
    # Registry-form image: enforce this lab's ownership contract. The image
    # must be <own-account>.dkr.ecr.ap-south-1.amazonaws.com/<ECR_REPOSITORY>:
    # <40-hex commit sha> — never a generic registry or moving tag.
    ECR_HOST="${IMAGE%%/*}"
    [[ "$ECR_HOST" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid registry host in image: $ECR_HOST"
    [[ "$ECR_HOST" == *.dkr.ecr.*.amazonaws.com ]] || die "image registry is not ECR: $ECR_HOST"
    ECR_REPO_PATH="${IMAGE#*/}"
    ECR_REPOSITORY_EXPORTED="${ECR_REPOSITORY:-}"
    [ -n "$ECR_REPOSITORY_EXPORTED" ] || die "ECR_REPOSITORY must be exported by the infra bootstrap"
    [[ "$ECR_REPO_PATH" == *":"* ]] || die "image must carry an immutable tag: $IMAGE"
    ECR_REPO_NAME="${ECR_REPO_PATH%:*}"
    ECR_TAG="${ECR_REPO_PATH##*:}"
    [ "$ECR_REPO_NAME" = "$ECR_REPOSITORY_EXPORTED" ] || \
        die "image repository '$ECR_REPO_NAME' does not match ECR_REPOSITORY '$ECR_REPOSITORY_EXPORTED'"
    [[ "$ECR_TAG" =~ ^[0-9a-f]{40}$ ]] || \
        die "image tag '$ECR_TAG' is not a 40-hex commit sha (moving tags are not deployable)"
    ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
        || die "could not read own account identity (instance role permission?)"
    [ "$ECR_HOST" = "${ACCOUNT_ID}.dkr.ecr.${ECR_REGION}.amazonaws.com" ] || \
        die "image registry '$ECR_HOST' does not match this account/region (${ACCOUNT_ID}.dkr.ecr.${ECR_REGION}.amazonaws.com)"
    unset ACCOUNT_ID
else
    # Local tag without a registry (e.g. ai-model-serving:dev): skip the
    # ECR validation and pull secret; the image must already exist on the node.
    ECR_HOST=""
fi

# --- namespace must exist before any Secret can be created in it ------------
info "applying namespace"
"${KUBECTL[@]}" apply -f "$REPO_ROOT/k8s/namespace.yaml"

# --- secrets (values never printed) -----------------------------------------
info "fetching api-key from SSM prefix $SSM_PARAMETER_PREFIX"
API_KEY="$(aws ssm get-parameter \
    --name "$SSM_PARAMETER_PREFIX/api-key" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null)" \
    || die "SSM parameter $SSM_PARAMETER_PREFIX/api-key is missing or unreadable"
# Trim surrounding whitespace; a whitespace-only key would look enabled but
# never authenticate (the runtime strips it the same way).
API_KEY="$(printf '%s' "$API_KEY" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
[ -n "$API_KEY" ] || die "SSM $SSM_PARAMETER_PREFIX/api-key must be nonblank in cloud deployments"

info "creating/updating secret serving-secrets (contents not printed)"
DEPLOY_SECRET_NAMESPACE="$NAMESPACE" \
DEPLOY_API_KEY="$API_KEY" \
python3 - <<'PY' | "${KUBECTL[@]}" apply -f -
import json, os
secret = {
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": "serving-secrets",
                 "namespace": os.environ["DEPLOY_SECRET_NAMESPACE"]},
    "stringData": {"api-key": os.environ["DEPLOY_API_KEY"]},
}
print(json.dumps(secret))
PY

if [ -n "$ECR_HOST" ]; then
    info "refreshing ECR imagePullSecret ecr-pull (contents not printed)"
    ECR_TOKEN="$(aws ecr get-login-password --region "$ECR_REGION")" \
        || die "could not obtain ECR login password (instance role permission?)"
    [ -n "$ECR_TOKEN" ] || die "empty ECR login password"
    DEPLOY_SECRET_NAMESPACE="$NAMESPACE" \
    DEPLOY_ECR_HOST="$ECR_HOST" \
    DEPLOY_ECR_TOKEN="$ECR_TOKEN" \
    python3 - <<'PY' | "${KUBECTL[@]}" apply -f -
import base64, json, os
host, token = os.environ["DEPLOY_ECR_HOST"], os.environ["DEPLOY_ECR_TOKEN"]
auth = base64.b64encode(f"AWS:{token}".encode()).decode()
config = {"auths": {host: {"username": "AWS", "password": token, "auth": auth}}}
secret = {
    "apiVersion": "v1", "kind": "Secret",
    "type": "kubernetes.io/dockerconfigjson",
    "metadata": {"name": "ecr-pull",
                 "namespace": os.environ["DEPLOY_SECRET_NAMESPACE"]},
    "data": {".dockerconfigjson": base64.b64encode(
        json.dumps(config).encode()).decode()},
}
print(json.dumps(secret))
PY
    unset ECR_TOKEN
fi
unset API_KEY

# --- rendered manifests, rollout --------------------------------------------
RENDERED="$(mktemp /tmp/ai-serving-rendered.XXXXXX.yaml)"
trap 'rm -f "$RENDERED"' EXIT
python3 "$REPO_ROOT/scripts/render_k8s.py" --image "$IMAGE" \
    $([ -n "$ECR_HOST" ] && echo --pull-secret ecr-pull || echo --pull-secret none) \
    --output "$RENDERED"
info "applying rendered manifests (image: $IMAGE)"
"${KUBECTL[@]}" apply -f "$RENDERED"

DEPLOYED_IMAGE="$("${KUBECTL[@]}" -n "$NAMESPACE" get deployment "$DEPLOYMENT" \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
[ "$DEPLOYED_IMAGE" = "$IMAGE" ] || die "deployed image '$DEPLOYED_IMAGE' != requested '$IMAGE'"

info "waiting for rollout (timeout ${WAIT_TIMEOUT}s)"
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" \
    --timeout="${WAIT_TIMEOUT}s" || die "rollout did not complete in time"

# --- health through the NodePort on the node itself --------------------------
health() {
    if [ "$HTTP_PROBE" = "curl" ]; then
        curl -fsS --max-time 5 "http://127.0.0.1:${NODEPORT}$1"
    else
        NODEPORT="$NODEPORT" python3 - "$1" <<'PY'
import os, sys, urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{os.environ['NODEPORT']}{sys.argv[1]}", timeout=5) as r:
    print(r.read().decode())
PY
    fi
}

READY=""
for _ in $(seq 1 $((WAIT_TIMEOUT / 5))); do
    if health /health/ready >/dev/null 2>&1; then READY="1"; break; fi
    sleep 5
done
[ -n "$READY" ] || die "/health/ready did not become 200 within ${WAIT_TIMEOUT}s"
info "health/ready OK"
health /health/live >/dev/null 2>&1 || die "/health/live is not responding"
info "health/live OK"

echo "--- status ---"
"${KUBECTL[@]}" -n "$NAMESPACE" get deployment,pods,service,hpa
info "deploy OK: $DEPLOYMENT serving $IMAGE"
