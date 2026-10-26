"""Detection API failures retain bounded capacity and private error details."""
import pytest
from PIL import Image
from app import inference as inference_module
from app.config import Settings
from app.inference import InferenceTimeout, YoloDetector
from app.model_registry import YOLOV8N
from tests.conftest import image_bytes

@pytest.mark.parametrize('query',['conf=.01','conf=nan','conf=inf','iou=.09','iou=.96','max_detections=0','max_detections=101','max_detections=1.5'])
def test_bad_query_returns_422(client,query):
    response=client.post('/api/detect?'+query,content=image_bytes(),headers={'Content-Type':'image/jpeg'})
    assert response.status_code==422
    assert all('input' not in item for item in response.json()['detail'])

@pytest.mark.parametrize('body,ctype,status',[(b'','image/jpeg',422),(b'garbage','image/jpeg',415),(b'abc','text/plain',415),(b'\xff\xd8\xffbroken','image/jpeg',422)])
def test_invalid_upload(client,body,ctype,status):
    assert client.post('/api/detect',content=body,headers={'Content-Type':ctype}).status_code==status


def test_capacity_limit_and_slot_recovery(application,client):
    slots=application.state.slots
    for _ in range(application.state.settings.max_concurrent_inferences): assert slots.acquire(False)
    response=client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'})
    assert response.status_code==429 and response.headers['retry-after']=='1'
    for _ in range(application.state.settings.max_concurrent_inferences): slots.release()
    assert client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==200

@pytest.mark.parametrize('failure,status,detail',[(RuntimeError('PRIVATE'),500,'Inference failed'),(InferenceTimeout('PRIVATE'),504,'Inference timed out')])
def test_safe_failures_release_slot(client,application,monkeypatch,failure,status,detail):
    model=application.state.models['yolov8n'];original=model.predict
    def fail(*args):raise failure
    monkeypatch.setattr(model,'predict',fail)
    response=client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'})
    assert response.status_code==status and response.json()=={'detail':detail}
    monkeypatch.setattr(model,'predict',original)
    assert client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==200


def test_slot_held_during_predict(application,client,monkeypatch):
    model=application.state.models['yolov8n'];original=model.predict
    def spy(*args):
        assert not application.state.slots.acquire(False)
        return original(*args)
    monkeypatch.setattr(model,'predict',spy)
    assert client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==200
    assert application.state.slots.acquire(False)
    application.state.slots.release()


def test_detector_timeout_is_deterministic(monkeypatch):
    """Exercise the native-timeout branch without a scheduler-sensitive deadline."""
    class ImmediateTimer:
        def __init__(self, _interval, function):
            self.function = function

        def start(self):
            self.function()

        def cancel(self):
            pass

    class Item:
        def __init__(self, name):
            self.name = name

    class TerminatedSession:
        def get_inputs(self):
            return [Item("images")]

        def get_outputs(self):
            return [Item("output")]

        def run(self, _outputs, _feeds, run_options=None):
            assert run_options is not None and run_options.terminate
            raise RuntimeError("terminated by test timer")

    monkeypatch.setattr(inference_module.threading, "Timer", ImmediateTimer)
    model = YoloDetector(YOLOV8N, Settings(_env_file=None))
    model._session = TerminatedSession()
    with Image.new("RGB", (64, 48)) as image:
        with pytest.raises(InferenceTimeout):
            model.predict(image, 0.25, 0.45, 100)
