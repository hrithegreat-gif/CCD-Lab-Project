"""API contract tests for the current detection-only MVP.

The application currently registers only the YOLOv8n object-detection model
in app.model_registry.REGISTRY and exposes /api/detect. Although experimental
classification and segmentation inference helpers exist, those models are not
registered, pinned as model assets, or exposed as API routes yet. Keep those
future endpoints out of the public OpenAPI contract until the full integration
is implemented and tested end-to-end.
"""

import pytest


FUTURE_TASK_ENDPOINTS = ("/api/segment", "/api/classify")


def test_current_api_exposes_detection_route_only_for_image_tasks(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/detect" in paths
    for endpoint in FUTURE_TASK_ENDPOINTS:
        assert endpoint not in paths


@pytest.mark.parametrize("endpoint", FUTURE_TASK_ENDPOINTS)
def test_unintegrated_task_endpoints_are_not_exposed(client, endpoint):
    response = client.post(
        endpoint,
        content=b"not an image",
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 404
