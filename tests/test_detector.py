from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image
from app.config import Settings
from app.model_registry import YOLOV8N
from app.inference import YoloDetector
import app.inference as inference

class Session:
    def __init__(self):self.calls=0;self.bad_shape=False;self.nan=False;self.fail=False
    def get_inputs(self):return [SimpleNamespace(name='actual-input',shape=[1,3,640,640],type='tensor(float)')]
    def get_outputs(self):return [SimpleNamespace(name='actual-output',shape=[1,84,8400],type='tensor(float)')]
    def run(self,names,feed,run_options):
        assert names==['actual-output'] and list(feed)==['actual-input']
        self.calls+=1
        if self.fail:raise RuntimeError('failed warmup')
        raw=np.zeros((1,84,8400),np.float32)
        if self.nan:raw[0,0,0]=np.nan
        return [raw[:,0] if self.bad_shape else raw]

@pytest.fixture
def detector(monkeypatch,tmp_path):
    session=Session()
    monkeypatch.setattr(inference,'download_assets',lambda *a:{YOLOV8N.weights:tmp_path/'model'})
    monkeypatch.setattr(inference.ort,'InferenceSession',lambda *a,**k:session)
    return YoloDetector(YOLOV8N,Settings(_env_file=None)),session

def test_readiness_requires_real_warmup_and_close_is_idempotent(detector):
    model,session=detector;assert not model.ready
    model.load();assert model.ready and session.calls==1
    model.close();model.close();assert not model.ready and model._session is None

def test_failed_warmup_clears_partial_session(detector):
    model,session=detector;session.fail=True
    with pytest.raises(RuntimeError):model.load()
    assert not model.ready and model._session is None

@pytest.mark.parametrize('failure',['nan','bad_shape'])
def test_output_contract_rejection(detector,failure):
    model,session=detector;model.load();setattr(session,failure,True)
    with Image.new('RGB',(64,48)) as image:
        with pytest.raises(ValueError):model.predict(image,.25,.45,100)

def test_wrong_input_contract_never_warms_up(detector):
    model,session=detector
    session.get_inputs=lambda:[SimpleNamespace(name='bad',shape=[1,3,320,320],type='tensor(float)')]
    with pytest.raises(ValueError):model.load()
    assert not model.ready and session.calls==0
