"""Offline validation of Docker/Kubernetes/CI/load-test artifacts.

Self-contained: no application imports, no cluster, no network. Runs with
PyYAML + pytest only (CI uses --noconftest because tests/conftest.py pulls in
FastAPI/onnxruntime). These checks pin the shared contracts from
docs/IMPLEMENTATION_PLAN.md: namespace ai-serving, workload ai-model-serving,
NodePort 30080, HPA bounds 1..3, non-root container, no ServiceAccount token,
no Secret material in git, per-Pod caches (no PVC), immutable tagged images.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = PROJECT_ROOT / "k8s"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
LOADTEST_DIR = PROJECT_ROOT / "loadtest"
WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_all_yaml(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [doc for doc in yaml.safe_load_all(handle) if doc]


def env_by_name(container: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in container["env"]}


def find_container(spec: dict) -> dict:
    return spec["template"]["spec"]["containers"][0]


@pytest.fixture(scope="module")
def deployment() -> dict:
    return load_yaml(K8S_DIR / "deployment.yaml")


@pytest.fixture(scope="module")
def service() -> dict:
    return load_yaml(K8S_DIR / "service.yaml")


@pytest.fixture(scope="module")
def hpa() -> dict:
    return load_yaml(K8S_DIR / "hpa.yaml")


@pytest.fixture(scope="module")
def namespace() -> dict:
    return load_yaml(K8S_DIR / "namespace.yaml")


@pytest.fixture(scope="module")
def load_generator() -> dict:
    return load_yaml(K8S_DIR / "load-generator.yaml")


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    return (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def k6_script_text() -> str:
    return (LOADTEST_DIR / "k6-script.js").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Namespace and workload identity
# ---------------------------------------------------------------------------


def test_namespace_is_ai_serving(namespace):
    assert namespace["apiVersion"] == "v1"
    assert namespace["kind"] == "Namespace"
    assert namespace["metadata"]["name"] == "ai-serving"


def test_deployment_identity_and_placeholders(deployment):
    assert deployment["apiVersion"] == "apps/v1"
    assert deployment["kind"] == "Deployment"
    assert deployment["metadata"]["name"] == "ai-model-serving"
    assert deployment["metadata"]["namespace"] == "ai-serving"
    container = find_container(deployment["spec"])
    assert container["image"] == "__IMAGE__", "image must be rendered, never hard-coded"


def test_deployment_replicas_and_rolling_strategy(deployment):
    spec = deployment["spec"]
    assert spec["replicas"] == 1
    strategy = spec["strategy"]
    assert strategy["type"] == "RollingUpdate"
    assert strategy["rollingUpdate"]["maxSurge"] == 0
    assert strategy["rollingUpdate"]["maxUnavailable"] == 1


def test_service_selector_matches_deployment_labels(deployment, service):
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    match_labels = deployment["spec"]["selector"]["matchLabels"]
    for key, value in match_labels.items():
        assert labels.get(key) == value
    assert service["spec"]["selector"] == match_labels
    assert service["metadata"]["namespace"] == "ai-serving"
    assert service["metadata"]["name"] == "ai-model-serving"


def test_hpa_targets_deployment(hpa):
    assert hpa["apiVersion"] == "autoscaling/v2"
    assert hpa["kind"] == "HorizontalPodAutoscaler"
    assert hpa["metadata"]["name"] == "ai-model-serving"
    assert hpa["metadata"]["namespace"] == "ai-serving"
    assert hpa["spec"]["scaleTargetRef"]["kind"] == "Deployment"
    assert hpa["spec"]["scaleTargetRef"]["name"] == "ai-model-serving"


def test_hpa_bounds_and_cpu_target(hpa):
    spec = hpa["spec"]
    assert spec["minReplicas"] == 1
    assert spec["maxReplicas"] == 3
    resource = spec["metrics"][0]["resource"]
    assert resource["name"] == "cpu"
    assert resource["target"]["type"] == "Utilization"
    assert resource["target"]["averageUtilization"] == 70


def test_hpa_has_stabilized_behavior(hpa):
    behavior = hpa["spec"]["behavior"]
    assert behavior["scaleDown"]["stabilizationWindowSeconds"] >= 300
    policies = behavior["scaleDown"]["policies"] + behavior["scaleUp"]["policies"]
    assert all(policy["value"] == 1 for policy in policies), (
        "single node: scale at most one Pod per period"
    )


# ---------------------------------------------------------------------------
# Container hardening and probes
# ---------------------------------------------------------------------------


def test_container_port_8000(deployment):
    container = find_container(deployment["spec"])
    port = container["ports"][0]
    assert port["containerPort"] == 8000
    assert port["name"] == "http"


def test_security_context_nonroot(deployment):
    pod_security = deployment["spec"]["template"]["spec"]["securityContext"]
    assert pod_security["runAsNonRoot"] is True
    assert pod_security["runAsUser"] == 10001
    container_security = find_container(deployment["spec"])["securityContext"]
    assert container_security["allowPrivilegeEscalation"] is False
    assert container_security["readOnlyRootFilesystem"] is True
    assert "ALL" in container_security["capabilities"]["drop"]


def test_no_serviceaccount_token(deployment, load_generator):
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert load_generator["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_resources_fit_single_node(deployment):
    resources = find_container(deployment["spec"])["resources"]
    assert resources["requests"]["cpu"] == "500m"
    assert resources["requests"]["memory"] == "512Mi"
    assert resources["limits"]["cpu"] == "1"
    assert resources["limits"]["memory"] == "1Gi"


def test_public_health_probes(deployment):
    container = find_container(deployment["spec"])
    startup = container["startupProbe"]["httpGet"]
    readiness = container["readinessProbe"]["httpGet"]
    liveness = container["livenessProbe"]["httpGet"]
    assert startup["path"] == "/health/ready"
    assert readiness["path"] == "/health/ready"
    assert liveness["path"] == "/health/live"
    for probe in (startup, readiness, liveness):
        assert probe["port"] == "http"
        assert "httpHeaders" not in probe, "health endpoints are public; no auth in probes"


def test_env_from_secret_and_limits(deployment):
    env = env_by_name(find_container(deployment["spec"]))
    api_key = env["API_KEY"]["valueFrom"]["secretKeyRef"]
    assert api_key["name"] == "serving-secrets"
    assert api_key["key"] == "api-key"
    assert api_key.get("optional", False) is False
    groq = env["GROQ_API_KEY"]["valueFrom"]["secretKeyRef"]
    assert groq == {"name": "serving-secrets", "key": "groq-api-key", "optional": True}
    assert env["MAX_REQUEST_BYTES"]["value"] == "65536"
    assert env["BACKGROUND_MODEL_LOAD"]["value"] == "true"
    assert env["MODEL_CACHE_DIR"]["value"] == "/cache"
    assert env["MODEL_LOCAL_FILES_ONLY"]["value"] == "true"


def test_volumes_are_per_pod_emptydir_no_pvc(deployment):
    spec_volumes = deployment["spec"]["template"]["spec"]["volumes"]
    assert spec_volumes, "model cache volume required"
    for volume in spec_volumes:
        assert "persistentVolumeClaim" not in volume, (
            "shared RWO caches deadlock rolling updates and HPA scale-out"
        )
        assert "emptyDir" in volume
    mounts = find_container(deployment["spec"])["volumeMounts"]
    mount_paths = {mount["mountPath"] for mount in mounts}
    assert "/cache" in mount_paths
    assert "/tmp" in mount_paths


def test_deployment_uses_image_pull_secret_block_marker(deployment):
    text = (K8S_DIR / "deployment.yaml").read_text(encoding="utf-8")
    assert "# BEGIN imagePullSecrets" in text
    assert "# END imagePullSecrets" in text
    assert "__PULL_SECRET__" in text


def test_nodeport_service(service):
    assert service["apiVersion"] == "v1"
    assert service["kind"] == "Service"
    assert service["spec"]["type"] == "NodePort"
    port = service["spec"]["ports"][0]
    assert port["port"] == 8000
    assert port["targetPort"] == "http"
    assert port["nodePort"] == 30080


# ---------------------------------------------------------------------------
# No secret material in the repository
# ---------------------------------------------------------------------------


def test_no_secret_manifests_in_k8s():
    secrets = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        for doc in load_all_yaml(path):
            if doc.get("kind") == "Secret":
                secrets.append(path.name)
    assert secrets == [], "Secrets are created at deploy time; never commit them"
    for path in sorted(K8S_DIR.glob("*.yaml")):
        assert "stringData" not in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# In-cluster load generator
# ---------------------------------------------------------------------------


def test_load_generator_is_bounded_job(load_generator):
    assert load_generator["apiVersion"] == "batch/v1"
    assert load_generator["kind"] == "Job"
    assert load_generator["metadata"]["namespace"] == "ai-serving"
    spec = load_generator["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 900
    container = find_container(spec)
    assert container["image"].startswith("grafana/k6:")
    assert container["image"] != "grafana/k6:latest"
    assert "/scripts/k6-script.js" in " ".join(container["args"])


def test_load_generator_targets_service_dns_and_secret_auth(load_generator):
    container = find_container(load_generator["spec"])
    env = env_by_name(container)
    assert env["TARGET_URL"]["value"] == (
        "http://ai-model-serving.ai-serving.svc.cluster.local:8000"
    )
    assert env["API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "serving-secrets", "key": "api-key",
    }
    assert env["LOADTEST_DURATION"]["value"] == "180s"


def test_load_generator_pod_can_write_summary(load_generator):
    # grafana/k6 runs as uid 12345; the emptyDir at /home/k6 must be writable
    # by that uid even with readOnlyRootFilesystem.
    pod_security = load_generator["spec"]["template"]["spec"]["securityContext"]
    assert pod_security["runAsNonRoot"] is True
    assert pod_security["runAsUser"] == 12345
    assert pod_security["runAsGroup"] == 12345
    assert pod_security["fsGroup"] == 12345


def test_load_generator_script_volume_is_configmap(load_generator):
    volumes = load_generator["spec"]["template"]["spec"]["volumes"]
    config_maps = [v["configMap"]["name"] for v in volumes if "configMap" in v]
    assert config_maps == ["k6-script"], (
        "create with: kubectl -n ai-serving create configmap k6-script "
        "--from-file=k6-script.js=loadtest/k6-script.js"
    )


# ---------------------------------------------------------------------------
# Load-test script content
# ---------------------------------------------------------------------------


def test_k6_script_targets_inference_with_bearer(k6_script_text):
    assert "k6/http" in k6_script_text
    assert "/api/inference" in k6_script_text
    assert "Bearer ${API_KEY}" in k6_script_text
    assert "expectedStatuses(200, 429)" in k6_script_text
    assert "requests_429" in k6_script_text
    assert "latency_success_${phase}_ms" in k6_script_text
    assert "requests_5xx" in k6_script_text


def test_k6_script_disables_connection_reuse(k6_script_text):
    # Keep-alive pins VUs to the first-accepting Pod; scale-out replicas would
    # receive zero load and the HPA measurement would be meaningless.
    assert "noConnectionReuse: true" in k6_script_text
    assert "noConnectionReuse: false" not in k6_script_text
    assert "re-balance" in k6_script_text, "the tradeoff must be documented"


def test_k6_script_separates_success_from_rejections(k6_script_text):
    # 200 latency/throughput must be measured apart from invalid 200 bodies,
    # 429/4xx/5xx, and other statuses. Per-phase metric names are built from
    # template literals; the Node-stubbed semantic test proves the real names.
    assert "latency_rate_limited_ms" in k6_script_text
    assert "latency_errors_ms" in k6_script_text
    assert "latency_invalid_ms" in k6_script_text
    assert "requests_200_invalid" in k6_script_text
    assert "requests_other" in k6_script_text
    for template in ("`attempts_${phase}`", "`success_200_${phase}`",
                     "`invalid_200_${phase}`", "`rate_limited_${phase}`",
                     "`errors_4xx_${phase}`", "`errors_5xx_${phase}`",
                     "`other_${phase}`", "`latency_success_${phase}_ms`"):
        assert template in k6_script_text
    assert "['warmup', 'baseline', 'sustained']" in k6_script_text


def test_k6_script_counts_attempts_before_the_request(k6_script_text):
    attempt_add = k6_script_text.index("COUNTERS[`attempts_${phase}`].add(1)")
    post_call = k6_script_text.index("const response = http.post")
    assert attempt_add < post_call, (
        "attempts must be counted before http.post so interrupted attempts "
        "remain visible and can never count as successes"
    )
    assert "interrupted: attempts - completed" in k6_script_text


def test_k6_script_bounds_phase_overlap(k6_script_text):
    # Default gracefulStop lets a phase's in-flight iterations bleed into the
    # next phase; every scenario must opt out explicitly.
    assert k6_script_text.count("gracefulStop: '0s'") == 3
    assert "interrupted" in k6_script_text, "interrupted requests must be documented"


def test_k6_script_guards_zero_duration(k6_script_text):
    # LOADTEST_DURATION=0 must fall back to the default, not divide by zero.
    assert "seconds < 1" in k6_script_text


def test_k6_script_labels_attempt_throughput_not_goodput(k6_script_text):
    assert "attempt_throughput_rps" in k6_script_text
    assert "successful_throughput_rps" in k6_script_text
    assert "goodput" not in k6_script_text.lower(), (
        "attempt throughput must not be presented as goodput"
    )


def test_k6_script_validates_real_sentiment_response_shape(k6_script_text):
    assert "isValidSentimentResponse" in k6_script_text
    assert "body.status === 'success'" in k6_script_text
    assert "body.prediction === 'positive'" in k6_script_text
    assert "body.tokens_processed" in k6_script_text
    assert "valid sentiment response" in k6_script_text


def test_k6_script_never_logs_the_api_key(k6_script_text):
    for match in re.finditer(r"console\.(log|warn|error)\(([^)]*)\)", k6_script_text):
        assert "API_KEY" not in match.group(2), "credentials must never be logged"


def test_k6_script_is_bounded(k6_script_text):
    assert "600" in k6_script_text, "duration cap"
    assert "32" in k6_script_text, "VU cap"
    assert "'LOADTEST_DURATION', 180, 600" in k6_script_text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_k6_script_parses(tmp_path):
    target = tmp_path / "k6-script.mjs"
    shutil.copyfile(LOADTEST_DIR / "k6-script.js", target)
    result = subprocess.run(
        ["node", "--check", str(target)], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


# Node stubs for the k6 module API so the real script logic runs in Node
# without k6 installed: semantic regression for outcome classification,
# phase boundaries, and throughput accounting.
K6_STUB_PACKAGE_JSON = (
    '{"name":"k6","version":"0.0.0-stub","type":"module",'
    '"exports":{"./http":"./http.mjs","./metrics":"./metrics.mjs",".":"./index.mjs"}}'
)

K6_STUB_HTTP = r"""
const state = globalThis.__K6_STUB__;
const http = {
  setResponseCallback() {},
  expectedStatuses() { return () => true; },
  post(url, body, params) {
    state.requests.push({ url, body, params });
    return state.respond(url, body, params);
  },
};
export default http;
"""

K6_STUB_METRICS = r"""
const state = globalThis.__K6_STUB__;
export class Counter {
  constructor(name) { this.name = name; state.counters[name] = state.counters[name] || 0; }
  add(value = 1) { state.counters[this.name] = (state.counters[this.name] || 0) + value; }
}
export class Trend {
  constructor(name) { this.name = name; state.trends[name] = state.trends[name] || []; }
  add(value) { if (typeof value === 'number') state.trends[this.name].push(value); }
}
"""

K6_STUB_INDEX = r"""
const state = globalThis.__K6_STUB__;
export function check(response, checks) {
  let all = true;
  for (const [name, fn] of Object.entries(checks || {})) {
    let pass = false;
    try { pass = !!fn(response); } catch { pass = false; }
    state.checks.push({ name, pass });
    all = all && pass;
  }
  return all;
}
export function sleep() {}
"""

K6_SEMANTIC_DRIVER = r"""
function assert(cond, message) { if (!cond) throw new Error("ASSERT: " + message); }
function approx(a, b, eps = 1e-9) { return Math.abs(a - b) <= eps; }

const state = globalThis.__K6_STUB__ = {
  requests: [], counters: {}, trends: {}, checks: [],
  respond: () => ({ status: 0 }),
};
globalThis.__ENV = {};
const mod = await import("./k6-script.mjs");

assert(mod.options.noConnectionReuse === true, "noConnectionReuse true");
for (const phase of ["warmup", "baseline", "sustained"]) {
  assert(mod.options.scenarios[phase].gracefulStop === "0s", `gracefulStop 0s (${phase})`);
}
assert(mod.options.scenarios.warmup.startTime === "0s", "warmup boundary");
assert(mod.options.scenarios.baseline.startTime === "15s", "baseline boundary");
assert(mod.options.scenarios.sustained.startTime === "45s", "sustained boundary");
assert(mod.options.scenarios.sustained.duration === "180s", "sustained default");

function respond(status, body) {
  state.respond = () => ({
    status,
    json() {
      if (body === undefined) throw new Error("no body");
      return body;
    },
  });
}
const VALID = { status: "success", model: "sentiment", prediction: "positive",
  confidence: 0.87, processing_time_ms: 6.5, tokens_processed: 9, truncated: false };

respond(200, VALID);
mod.sustainedRequest();                                   // 1 valid 200
respond(200, { status: "success", prediction: "positive", confidence: 0.5 });
mod.sustainedRequest();                                   // 2 malformed 200
respond(429, { detail: "capacity" });
mod.sustainedRequest();                                   // 3 rate limited
respond(500, { detail: "boom" });
mod.sustainedRequest();                                   // 4 server error
respond(302, {});
mod.sustainedRequest();                                   // 5 unexpected status
state.respond = () => { throw new Error("connection refused"); };
let threw = false;
try { mod.sustainedRequest(); } catch { threw = true; }    // 6 interrupted
assert(threw, "connection error propagates");

assert(state.counters.attempts_sustained === 6, "attempts pre-counted");
assert(state.counters.success_200_sustained === 1, "only body-validated 200 is success");
assert(state.counters.invalid_200_sustained === 1, "malformed 200 is invalid");
assert(state.counters.rate_limited_sustained === 1, "429 counted");
assert(state.counters.errors_5xx_sustained === 1, "500 counted");
assert(state.counters.other_sustained === 1, "other status counted");
assert(state.counters.errors_4xx_sustained === 0, "no 4xx in this run");
assert(state.counters.requests_200 === 1, "aggregate requests_200 valid-only");
assert(state.counters.requests_200_invalid === 1, "aggregate invalid");
assert(state.counters.requests_429 === 1, "aggregate 429");
assert(state.counters.requests_5xx === 1, "aggregate 5xx");
assert(state.counters.requests_other === 1, "aggregate other");
assert(state.trends.latency_success_sustained_ms.length === 1, "success trend valid-only");
assert(state.trends.latency_invalid_ms.length === 1, "invalid trend recorded");
assert(state.trends.latency_rate_limited_ms.length === 1, "429 trend recorded");
assert(state.trends.latency_errors_ms.length === 1, "error trend recorded");
const shapeChecks = state.checks.filter((c) => c.name === "valid sentiment response");
assert(shapeChecks.length === 2 && shapeChecks[0].pass && !shapeChecks[1].pass,
  "response shape check ran on both 200s with correct results");

globalThis.__ENV = { LOADTEST_DURATION: "0" };
const modZero = await import("./k6-script.mjs?zero");
assert(modZero.options.scenarios.sustained.duration === "180s",
  "LOADTEST_DURATION=0 falls back to the default");

const metrics = {};
for (const [name, value] of Object.entries(state.counters)) {
  metrics[name] = { values: { count: value } };
}
for (const [name, samples] of Object.entries(state.trends)) {
  if (!samples.length) continue;
  const sorted = [...samples].sort((a, b) => a - b);
  metrics[name] = { values: {
    avg: samples.reduce((a, b) => a + b, 0) / samples.length,
    "p(95)": sorted[Math.min(sorted.length - 1, Math.floor(0.95 * sorted.length))],
  } };
}
metrics["http_reqs"] = { values: { count: 5, rate: 5 / 225 } };
metrics["http_req_duration"] = { values: { "p(95)": 42 } };
metrics["checks"] = { values: { rate: 5 / 6 } };
metrics["http_req_failed"] = { values: { rate: 0 } };
const summary = JSON.parse(mod.handleSummary(
  { metrics, state: { testRunDurationMs: 225000 } }
).stdout);

assert(summary.scenario.no_connection_reuse === true, "summary records reuse mode");
assert(summary.totals.successful_200 === 1, "totals success valid-only");
assert(approx(summary.totals.successful_throughput_rps, 1 / 225), "totals successful throughput");
assert(summary.totals.attempts_total === 6, "totals attempts");
assert(summary.totals.completed_http_requests === 5, "interrupted attempt is not a completed HTTP request");
assert(approx(summary.totals.attempt_throughput_rps, 6 / 225), "totals attempt throughput");
assert(summary.totals.requests_200_invalid === 1, "totals invalid");
assert(summary.totals.requests_other === 1, "totals other");
const s = summary.phases.sustained;
assert(s.phase_duration_s === 180, "sustained phase duration");
assert(s.attempts === 6 && s.completed === 5 && s.interrupted === 1, "attempt accounting");
assert(s.success_200 === 1 && s.invalid_200 === 1 && s.rate_limited === 1 &&
  s.errors_5xx === 1 && s.other === 1 && s.errors_4xx === 0, "sustained outcome split");
assert(approx(s.attempt_throughput_rps, 6 / 180), "sustained attempt throughput");
assert(approx(s.successful_throughput_rps, 1 / 180), "sustained phase goodput");
assert(s.latency_success_p95_ms !== null, "sustained success latency present");
const w = summary.phases.warmup;
assert(w.attempts === 0 && w.success_200 === 0 && w.successful_throughput_rps === 0,
  "warmup empty: no pollution of measured results");
assert(summary.phases.baseline.attempts === 0, "baseline empty");
assert(summary.latency_ms.attempts_overall_p95 === 42, "overall attempts latency");
assert(!JSON.stringify(summary).toLowerCase().includes("goodput"), "goodput absent from summary");

console.log(JSON.stringify({ ok: true, sustained: s, totals: summary.totals }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_k6_semantics_via_node_stub(tmp_path, k6_script_text):
    """Run the real script logic in Node against stubbed k6 modules."""
    (tmp_path / "k6-script.mjs").write_text(k6_script_text, encoding="utf-8", newline="\n")
    package = tmp_path / "node_modules" / "k6"
    package.mkdir(parents=True)
    (package / "package.json").write_text(K6_STUB_PACKAGE_JSON, encoding="utf-8")
    (package / "http.mjs").write_text(K6_STUB_HTTP, encoding="utf-8", newline="\n")
    (package / "metrics.mjs").write_text(K6_STUB_METRICS, encoding="utf-8", newline="\n")
    (package / "index.mjs").write_text(K6_STUB_INDEX, encoding="utf-8", newline="\n")
    driver = tmp_path / "driver.mjs"
    driver.write_text(K6_SEMANTIC_DRIVER, encoding="utf-8", newline="\n")
    result = subprocess.run(
        ["node", "driver.mjs"], capture_output=True, text=True,
        cwd=tmp_path, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["ok"] is True
    assert report["sustained"]["interrupted"] == 1
    assert report["sustained"]["success_200"] == 1
    assert report["totals"]["successful_200"] == 1


def test_loadtest_readme_documents_measurement_discipline():
    text = (LOADTEST_DIR / "README.md").read_text(encoding="utf-8")
    assert "--duration 660" in text, (
        "recording must span the run plus the 300s scale-in stabilization"
    )
    assert "300s sustained" not in text, (
        "default sustained duration is 180s; stale guidance must not remain"
    )
    assert "does NOT pin one Pod" in text, (
        "an SSM NodePort forward is not pod-pinning; say so explicitly"
    )
    assert "noConnectionReuse" in text
    assert "gracefulStop" in text


# ---------------------------------------------------------------------------
# Dockerfile and build context
# ---------------------------------------------------------------------------


def test_dockerfile_base_and_nonroot(dockerfile_text):
    assert "FROM python:3.12-slim-bookworm" in dockerfile_text
    assert re.search(r"^USER 10001:10001$", dockerfile_text, re.MULTILINE)
    assert "EXPOSE 8000" in dockerfile_text
    assert "--workers" in dockerfile_text and '"1"' in dockerfile_text
    assert "--no-access-log" in dockerfile_text
    assert "entrypoint.sh" in dockerfile_text
    assert ":latest" not in dockerfile_text


def test_dockerfile_bakes_pinned_model_and_notices(dockerfile_text):
    assert "/opt/model-cache" in dockerfile_text
    assert "scripts/prepare_model.py --bake /opt/model-cache --verify" in dockerfile_text
    assert "from app.model_registry import YOLOV8N" in (PROJECT_ROOT / "scripts/prepare_model.py").read_text()
    assert "/opt/model-notices" in dockerfile_text
    assert "models/NOTICE.md" in dockerfile_text
    assert "models/LICENSE-AGPL-3.0.txt" in dockerfile_text


def test_dockerfile_bake_command_compiles():
    text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'RUN python scripts/prepare_model.py --bake /opt/model-cache --verify' in text
    assert 'touch /opt/model-cache/.model-baked' in text
    helper = (PROJECT_ROOT / 'scripts/prepare_model.py').read_text(encoding='utf-8')
    compile(helper, 'prepare_model.py', 'exec')


def test_dockerignore_keeps_host_caches_and_secrets_out():
    text = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", text, re.MULTILINE)
    assert re.search(r"^\.env\.\*$", text, re.MULTILINE)
    assert "models/cache" in text
    assert re.search(r"^\.git$", text, re.MULTILINE)
    assert "results" in text
    assert re.search(r"^!app$", text, re.MULTILINE)
    assert re.search(r"^!requirements\.txt$", text, re.MULTILINE)


def test_dockerfile_bake_reuses_registry_pins():
    registry_text = (PROJECT_ROOT / "app" / "model_registry.py").read_text(
        encoding="utf-8"
    )
    revision = re.search(r"sha256=[\"\']([0-9a-f]{64})[\"\']", registry_text)
    assert revision, "model registry pin not found"
    dockerfile_text = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    # The bake step imports the registry; the pinned revision must not be
    # duplicated (drift risk) inside the Dockerfile.
    assert revision.group(1) not in dockerfile_text
    assert "from app.model_registry import YOLOV8N" in (PROJECT_ROOT / "scripts/prepare_model.py").read_text()


# ---------------------------------------------------------------------------
# render_k8s.py behavior
# ---------------------------------------------------------------------------


def run_render(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "render_k8s.py"), *args],
        capture_output=True, text=True,
    )


def test_render_default_core_manifests():
    result = run_render("--image", "registry.example.com/ccd-lab-serving:abc123")
    assert result.returncode == 0, result.stderr
    docs = [
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc
    ]
    assert [doc["kind"] for doc in docs] == [
        "Namespace", "Deployment", "Service", "HorizontalPodAutoscaler",
    ]
    rendered_deployment = docs[1]
    assert rendered_deployment["spec"]["template"]["spec"]["containers"][0][
        "image"
    ] == "registry.example.com/ccd-lab-serving:abc123"
    secret_name = rendered_deployment["spec"]["template"]["spec"]["imagePullSecrets"][0][
        "name"
    ]
    assert secret_name == "ecr-pull"
    assert "__IMAGE__" not in result.stdout
    assert "__PULL_SECRET__" not in result.stdout


def test_render_without_pull_secret():
    result = run_render("--image", "ai-model-serving:dev", "--pull-secret", "none")
    assert result.returncode == 0, result.stderr
    assert "imagePullSecrets" not in result.stdout


def test_render_can_include_load_generator():
    result = run_render("--image", "ai-model-serving:dev",
                        "--include", "load-generator.yaml")
    assert result.returncode == 0, result.stderr
    kinds = [doc["kind"] for doc in yaml.safe_load_all(result.stdout) if doc]
    assert kinds == ["Namespace", "Deployment", "Service",
                     "HorizontalPodAutoscaler", "Job"]


def test_render_rejects_bad_image():
    result = run_render("--image", "bad image with spaces")
    assert result.returncode != 0
    result = run_render("--image", "__IMAGE__")
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# CI workflows
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow() -> dict:
    return load_yaml(WORKFLOWS_DIR / "ci.yml")


@pytest.fixture(scope="module")
def deploy_workflow() -> dict:
    return load_yaml(WORKFLOWS_DIR / "deploy-aws.yml")


def test_ci_runs_real_tests_and_offline_manifest_checks(ci_workflow):
    jobs = ci_workflow["jobs"]
    assert {"tests", "manifests", "docker", "terraform"} <= set(jobs)
    test_steps = [step.get("run", "") for step in jobs["tests"]["steps"]]
    assert any("pytest tests/" in step for step in test_steps)
    manifest_steps = [step.get("run", "") for step in jobs["manifests"]["steps"]]
    assert any("tests/test_manifests.py" in step for step in manifest_steps)
    assert any("--noconftest" in step for step in manifest_steps)


def test_ci_terraform_never_applies_and_needs_no_credentials(ci_workflow):
    raw = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
    assert "terraform apply" not in raw
    assert "init -backend=false" in raw
    assert "fmt -check" in raw
    assert "terraform validate" in raw
    docker_steps = [step.get("run", "") for step in ci_workflow["jobs"]["docker"]["steps"]]
    assert any("docker build" in step for step in docker_steps)
    assert any("smoke_test.py" in step for step in docker_steps)


def test_container_smoke_runs_hardened_in_both_workflows():
    for name in ("ci.yml", "deploy-aws.yml"):
        raw = (WORKFLOWS_DIR / name).read_text(encoding="utf-8")
        assert "--read-only" in raw
        assert "--cap-drop=ALL" in raw, name
        assert "no-new-privileges" in raw, name
        assert "--memory 1g" in raw, name
        assert "--cpus 1" in raw, name
        # Temp files are user-owned, but cache must reproduce Kubernetes'
        # root-owned, fsGroup-writable emptyDir. A user-owned cache hid a
        # cp -a timestamp-preservation failure during real Pod startup.
        assert "uid=10001,gid=10001" in raw, name
        assert "--tmpfs /cache:rw,noexec,nosuid,size=256m,uid=0,gid=10001,mode=0770" in raw, name


def test_deploy_requires_green_ci_before_credentials(deploy_workflow):
    job = deploy_workflow["jobs"]["build-smoke-push"]
    names = [step.get("name", "") for step in job["steps"]]
    uses = [step.get("uses", "") for step in job["steps"]]
    gate_index = next(i for i, name in enumerate(names) if "successful CI run" in name)
    creds_index = next(
        i for i, use in enumerate(uses) if "configure-aws-credentials" in use
    )
    assert gate_index < creds_index, "the CI gate must run before any AWS credentials"
    raw = (WORKFLOWS_DIR / "deploy-aws.yml").read_text(encoding="utf-8")
    assert "actions/workflows/ci.yml/runs?head_sha=" in raw
    assert deploy_workflow["permissions"]["actions"] == "read"


def test_deploy_logs_in_to_bare_registry_host():
    raw = (WORKFLOWS_DIR / "deploy-aws.yml").read_text(encoding="utf-8")
    assert '--password-stdin "${{ steps.image.outputs.registry_host }}"' in raw
    assert '--password-stdin "${{ steps.image.outputs.image_uri }}"' not in raw, (
        "docker login takes a registry host, not a full image URI"
    )


def test_deploy_ssm_poll_retries_only_eventual_consistency():
    raw = (WORKFLOWS_DIR / "deploy-aws.yml").read_text(encoding="utf-8")
    assert "InvocationDoesNotExist" in raw, (
        "the first GetCommandInvocation poll races send-command registration"
    )


# ---------------------------------------------------------------------------
# deploy-k3s.sh ordering and validation
# ---------------------------------------------------------------------------


def test_deploy_script_applies_namespace_before_secrets():
    text = (SCRIPTS_DIR / "deploy-k3s.sh").read_text(encoding="utf-8")
    namespace_index = text.index('apply -f "$REPO_ROOT/k8s/namespace.yaml"')
    secret_index = text.index("creating/updating secret serving-secrets")
    assert namespace_index < secret_index, (
        "first deploy fails if secrets are created before the namespace exists"
    )


def test_deploy_script_validates_timeout_and_api_key():
    text = (SCRIPTS_DIR / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert "*[!0-9]*" in text, "--wait-timeout must reject non-numeric input"
    assert "-gt 3600" in text and "-lt 30" in text, "--wait-timeout must be bounded"
    assert "s/^[[:space:]]*//;s/[[:space:]]*$//" in text, "SSM values must be trimmed"
    assert "must be nonblank" in text


def test_deploy_script_enforces_ecr_image_ownership():
    text = (SCRIPTS_DIR / "deploy-k3s.sh").read_text(encoding="utf-8")
    assert "aws sts get-caller-identity" in text
    assert ".dkr.ecr." in text
    assert "^[0-9a-f]{40}$" in text, "only immutable 40-hex commit tags deploy"
    assert "ECR_REPOSITORY" in text


def test_deploy_workflow_is_manual_oidc_and_bounded(deploy_workflow):
    raw = (WORKFLOWS_DIR / "deploy-aws.yml").read_text(encoding="utf-8")
    # YAML 1.1 parses the `on:` key as boolean True.
    triggers = deploy_workflow.get(True, deploy_workflow.get("on"))
    assert triggers and "workflow_dispatch" in triggers
    assert deploy_workflow["permissions"]["id-token"] == "write"
    for job in ("build-smoke-push", "upload-bundle", "deploy"):
        assert deploy_workflow["jobs"][job]["environment"] == "aws-demo"
    assert "aws ssm send-command" in raw
    assert "ObjectKey=" in raw and "BundleSha256=" in raw and "ImageUri=" in raw
    assert "get-command-invocation" in raw
    assert "terraform apply" not in raw
    assert "start-instances" not in raw
    assert "GITHUB_SHA" in raw
    assert "ap-south-1" in raw


# ---------------------------------------------------------------------------
# Guard: measured results stay structurally sound
# ---------------------------------------------------------------------------

PERFORMANCE_CSV_NUMERIC_COLUMNS = (
    "concurrency", "duration_s", "total_requests", "successful_requests",
    "throughput_rps", "mean_latency_ms", "p95_latency_ms", "error_rate",
    "ready_replicas_min", "ready_replicas_max",
)


def test_performance_csv_schema_allows_future_measurements():
    """Empty until measured; once rows exist they must be well-formed."""
    text = (PROJECT_ROOT / "results" / "performance.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return  # untouched until a real measurement exists
    header = rows[0]
    assert header[0] == "run_id", "header schema changed unexpectedly"
    for row in rows[1:]:
        values = row
        assert len(values) == len(header), "data row must match header width"
        for column, value in zip(header, values):
            if column in PERFORMANCE_CSV_NUMERIC_COLUMNS and value.strip():
                float(value)  # reject malformed numeric fields
