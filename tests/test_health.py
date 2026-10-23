from tests.conftest import image_bytes
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from tests.conftest import TestModel


def test_health_and_openapi(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json() == {"status": "ready"}
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/detect" in schema["paths"]
    assert "/api/jobs" not in schema["paths"]


def test_failed_load_keeps_liveness_but_blocks_inference():
    class BrokenModel(TestModel):
        def load(self):
            raise RuntimeError("private startup detail")
    app = create_app(Settings(_env_file=None), model_factory=BrokenModel)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/models/yolov8n").json()["ready"] is False
        response = client.post("/api/detect", content=image_bytes(), headers={"Content-Type":"image/jpeg"})
        assert response.status_code == 503
        assert "private startup detail" not in response.text


def test_model_loaded_once_and_released_on_shutdown(application):
    with TestClient(application) as client:
        model = application.state.models["yolov8n"]
        for _ in range(3):
            assert client.post("/api/detect", content=image_bytes(), headers={"Content-Type":"image/jpeg"}).status_code == 200
        assert model.loads == 1
    assert not model.ready
    assert application.state.models == {}
