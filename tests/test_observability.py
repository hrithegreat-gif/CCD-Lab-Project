"""Prometheus exposition, label cardinality bounds and metrics privacy."""
import pytest
from prometheus_client import REGISTRY

INFERENCE_LABELS = {"method": "POST", "endpoint": "/api/inference"}


def _sample(name: str, labels: dict):
    return REGISTRY.get_sample_value(name, labels)


# ---------------------------------------------------------------------------
# Exposition
# ---------------------------------------------------------------------------

def test_metrics_endpoint_serves_prometheus_exposition(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    # The exact exposition version is the client library's choice; the media
    # type must stay Prometheus text with a version parameter.
    assert response.headers["content-type"].startswith("text/plain; version=")
    body = response.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    # Real client library process metrics come along.
    assert "python_gc_objects_collected_total" in body
    assert "python_info" in body


def test_request_counter_increments_once_per_request(client):
    labels = {**INFERENCE_LABELS, "status_code": "200"}
    before = _sample("http_requests_total", labels) or 0
    response = client.post("/api/inference", json={"model": "sentiment", "input": "hello"})
    assert response.status_code == 200
    after = _sample("http_requests_total", labels)
    assert after == before + 1


def test_request_counter_records_error_statuses(client):
    for body, status in (
        ({"model": "sentiment", "input": ""}, "422"),
        ({"model": "unknown", "input": "hello"}, "404"),
    ):
        labels = {**INFERENCE_LABELS, "status_code": status}
        before = _sample("http_requests_total", labels) or 0
        response = client.post("/api/inference", json=body)
        assert response.status_code == int(status)
        assert _sample("http_requests_total", labels) == before + 1


def test_request_counter_records_auth_rejections(authed_client):
    labels = {"method": "GET", "endpoint": "/api/models", "status_code": "401"}
    before = _sample("http_requests_total", labels) or 0
    assert authed_client.get("/api/models").status_code == 401
    assert _sample("http_requests_total", labels) == before + 1


def test_request_counter_records_body_limit_rejections(client):
    limit = client.app.state.settings.max_request_bytes
    labels = {**INFERENCE_LABELS, "status_code": "413"}
    before = _sample("http_requests_total", labels) or 0
    response = client.post("/api/inference", content=b"x" * (limit + 1))
    assert response.status_code == 413
    assert _sample("http_requests_total", labels) == before + 1


def test_latency_histogram_observed_for_inference(client):
    count_labels = dict(INFERENCE_LABELS)
    before = _sample("http_request_duration_seconds_count", count_labels) or 0
    response = client.post("/api/inference", json={"model": "sentiment", "input": "hello"})
    assert response.status_code == 200
    after = _sample("http_request_duration_seconds_count", count_labels)
    assert after == before + 1
    sum_value = _sample("http_request_duration_seconds_sum", count_labels)
    assert sum_value is not None and sum_value > 0
    buckets = _sample("http_request_duration_seconds_bucket",
                      {**count_labels, "le": "+Inf"})
    assert buckets == after


# ---------------------------------------------------------------------------
# Bounded, privacy-safe labels
# ---------------------------------------------------------------------------

def test_unknown_path_collapses_to_unmatched_label(client):
    marker = f"/not-a-route-{id(object())}"
    response = client.get(marker)
    assert response.status_code == 404
    exposition = client.get("/metrics").text
    assert marker not in exposition
    labels = {"method": "GET", "endpoint": "unmatched", "status_code": "404"}
    assert _sample("http_requests_total", labels) is not None


def test_model_details_path_uses_route_template_label(client):
    marker = f"untrusted-model-{id(object())}"
    response = client.get(f"/api/models/{marker}")
    assert response.status_code == 404
    exposition = client.get("/metrics").text
    assert marker not in exposition
    labels = {"method": "GET", "endpoint": "/api/models/{model_id}", "status_code": "404"}
    assert _sample("http_requests_total", labels) is not None


def test_unknown_http_method_labeled_other(client):
    response = client.request("BREW", "/health/live")
    assert response.status_code == 405
    exposition = client.get("/metrics").text
    assert "BREW" not in exposition
    labels = {"method": "OTHER", "endpoint": "/health/live", "status_code": "405"}
    assert _sample("http_requests_total", labels) is not None


def test_inference_input_never_labeled(client):
    marker = "OBSERVABILITY-INPUT-MARKER-" + "z" * 48
    response = client.post("/api/inference", json={"model": "sentiment", "input": marker})
    assert response.status_code == 200
    assert marker not in client.get("/metrics").text
    # Model IDs are allowlisted but still must not appear as metric labels.
    exposition = client.get("/metrics").text
    assert 'model="sentiment"' not in exposition


def test_metrics_never_contain_api_key(authed_client, auth_headers):
    secret = auth_headers["Authorization"].split(" ", 1)[1]
    exposition = authed_client.get("/metrics", headers=auth_headers).text
    assert secret not in exposition
    unauth = authed_client.get("/metrics")
    assert secret not in unauth.text


def test_normalize_endpoint_helper_bounds_cardinality():
    from app.observability import normalize_endpoint

    assert normalize_endpoint({"path": "/api/models/sentiment"}) == "/api/models/{model_id}"
    assert normalize_endpoint({"path": "/api/models/"}) == "unmatched"
    assert normalize_endpoint({"path": "/api/models/a/b"}) == "unmatched"
    assert normalize_endpoint({"path": "/api/models"}) == "/api/models"
    assert normalize_endpoint({"path": "/api/inference"}) == "/api/inference"
    assert normalize_endpoint({"path": "/health/live"}) == "/health/live"
    assert normalize_endpoint({"path": "/metrics"}) == "/metrics"
    assert normalize_endpoint({"path": "/api/generate"}) == "unmatched"
    arbitrary = f"/{id(object())}/{id(object())}"
    assert normalize_endpoint({"path": arbitrary}) == "unmatched"
    assert normalize_endpoint({}) == "unmatched"


def test_normalize_method_bounds_to_known_methods():
    from app.observability import normalize_method

    assert normalize_method("GET") == "GET"
    assert normalize_method("POST") == "POST"
    assert normalize_method("brew") == "OTHER"
    assert normalize_method("GET\r\nX-Evil: 1") == "OTHER"
    assert normalize_method(None) == "OTHER"
    assert normalize_method(["GET"]) == "OTHER"
