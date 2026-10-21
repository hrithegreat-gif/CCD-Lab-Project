import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.inference import InferenceTimeout, Prediction
from app.main import create_app
from tests.conftest import TestModel


@pytest.mark.parametrize("body", [
    {}, {"model": "sentiment"}, {"input": "hello"},
    {"model": "sentiment", "input": ""},
    {"model": "sentiment", "input": "  \n\t"},
    {"model": "sentiment", "input": "x" * 2001},
    {"model": "sentiment", "input": 123},
    {"model": "sentiment", "input": None},
    {"model": 1, "input": "hello"},
    {"model": "sentiment", "input": "hello", "unexpected": True},
])
def test_bad_input_returns_422(client, body):
    assert client.post("/api/inference", json=body).status_code == 422


def test_malformed_json_and_non_object(client):
    assert client.post("/api/inference", content="{broken",
                       headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/inference", json=["sentiment", "hello"]).status_code == 422


def test_configurable_character_limit():
    app = create_app(Settings(_env_file=None, max_input_chars=10), model_factory=TestModel)
    with TestClient(app) as client:
        assert client.post("/api/inference", json={
            "model": "sentiment", "input": "x" * 10
        }).status_code == 200
        assert client.post("/api/inference", json={
            "model": "sentiment", "input": "x" * 11
        }).status_code == 422


def test_capacity_limit_and_slot_recovery(application, client):
    slots = application.state.slots
    assert slots.acquire(blocking=False)
    assert slots.acquire(blocking=False)
    response = client.post("/api/inference", json={"model": "sentiment", "input": "hello"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    slots.release()
    slots.release()
    assert client.post("/api/inference", json={
        "model": "sentiment", "input": "hello"
    }).status_code == 200


@pytest.mark.parametrize("failure,status,detail", [
    (RuntimeError("private text must stay private"), 500, "Inference failed"),
    (InferenceTimeout("private deadline info"), 504, "Inference timed out"),
])
def test_safe_failures_release_slot(client, application, monkeypatch, failure, status, detail):
    model = application.state.models["sentiment"]
    original = model.predict
    def fail(_text):
        raise failure
    monkeypatch.setattr(model, "predict", fail)
    response = client.post("/api/inference", json={"model": "sentiment", "input": "hello"})
    assert response.status_code == status
    assert response.json() == {"detail": detail}
    monkeypatch.setattr(model, "predict", original)
    assert client.post("/api/inference", json={
        "model": "sentiment", "input": "hello"
    }).status_code == 200


def test_validation_does_not_echo_input(client):
    private = "PRIVATE INPUT " * 200
    response = client.post("/api/inference", json={"model": "sentiment", "input": private})
    assert response.status_code == 422
    assert private not in response.text


def test_inference_slot_held_for_the_whole_native_prediction(monkeypatch):
    """Capacity must stay acquired while the (native) predict call runs."""
    application = create_app(
        Settings(_env_file=None, max_concurrent_inferences=1), model_factory=TestModel
    )
    with TestClient(application) as client:
        model = application.state.models["sentiment"]
        observed = {}

        def spy(_text):
            # While the native call runs, no other slot may be available.
            probe = application.state.slots.acquire(blocking=False)
            observed["held"] = not probe
            if probe:
                application.state.slots.release()
            return Prediction("positive", 0.8, 8, False)

        monkeypatch.setattr(model, "predict", spy)
        response = client.post(
            "/api/inference", json={"model": "sentiment", "input": "hello"}
        )
        assert response.status_code == 200
        assert observed["held"] is True
        # After the request completes the slot is released again.
        assert application.state.slots.acquire(blocking=False)
        application.state.slots.release()
