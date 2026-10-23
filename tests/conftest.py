"""Fast fault-injection tests plus a separate, mandatory real-model integration suite."""
import io
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.inference import DetectionResult, YoloDetector
from app.main import create_app

TEST_API_KEY = "test-api-key-0123456789"


class TestModel:
    """Test double used only to isolate lifecycle and API errors."""
    __test__ = False

    def __init__(self, _spec, _settings):
        self.ready = False
        self.loads = 0

    def load(self):
        self.loads += 1
        self.ready = True

    def predict(self, image, conf, iou, max_det):
        detections=[] if conf>.8 else [dict(class_id=15,label='cat',confidence=.8,
            box=dict(x1=0.,y1=0.,x2=float(image.width),y2=float(image.height)))]
        return DetectionResult(detections,1.,.1)

    def close(self):
        self.ready = False


@pytest.fixture
def application():
    return create_app(Settings(_env_file=None), model_factory=TestModel)


@pytest.fixture
def client(application):
    with TestClient(application) as active:
        yield active


@pytest.fixture
def authed_application():
    return create_app(
        Settings(_env_file=None, api_key=TEST_API_KEY), model_factory=TestModel
    )


@pytest.fixture
def authed_client(authed_application):
    with TestClient(authed_application) as active:
        yield active


@pytest.fixture
def auth_headers():
    return {"Authorization": f"Bearer {TEST_API_KEY}"}


@pytest.fixture(scope="session")
def real_client():
    # Never skip or substitute a stub on download/model failure.
    application = create_app(Settings(_env_file=None), model_factory=YoloDetector)
    with TestClient(application) as active:
        assert active.get("/health/ready").status_code == 200, (
            "Real model failed to load. Run scripts/prepare_model.py --verify."
        )
        yield active


def image_bytes(fmt='JPEG',mode='RGB',size=(64,48)):
    out=io.BytesIO()
    with Image.new(mode,size) as image: image.save(out,format=fmt)
    return out.getvalue()

@pytest.fixture
def jpeg_bytes(): return image_bytes()
@pytest.fixture
def png_bytes(): return image_bytes('PNG')
@pytest.fixture
def webp_bytes(): return image_bytes('WEBP')
