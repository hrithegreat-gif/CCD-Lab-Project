"""Mandatory integration with the published, hash-verified CPU detector."""
from pathlib import Path
import pytest
from tests.conftest import image_bytes

@pytest.mark.integration
@pytest.mark.parametrize('sample,label',[('cat','cat'),('coffee','cup'),('person','person')])
def test_real_detections(real_client,sample,label):
    data=Path('app/static/sample-'+sample+'.jpg').read_bytes()
    r=real_client.post('/api/detect',content=data,headers={'Content-Type':'image/jpeg'})
    assert r.status_code==200
    b=r.json();assert b['count']==len(b['detections'])
    assert any(d['label']==label and d['confidence']>.5 for d in b['detections'])
    assert b['processing_time_ms']>0

@pytest.mark.integration
def test_valid_blank_image_can_have_no_detections(real_client):
    r=real_client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'})
    assert r.status_code==200 and r.json()['count']==0

@pytest.mark.integration
def test_real_model_uses_cpu_provider(real_client):
    # Keep the real-model integration assertion deterministic. Timeout-to-504
    # behavior is covered separately in tests/test_detect_api.py; using a
    # 1-microsecond deadline against native ONNX inference is scheduler-dependent.
    model=real_client.app.state.models['yolov8n']
    assert model._session.get_providers()==['CPUExecutionProvider']
    response=real_client.post(
        '/api/detect', content=Path('app/static/sample-cat.jpg').read_bytes(),
        headers={'Content-Type':'image/jpeg'},
    )
    assert response.status_code == 200
    assert response.json()['count'] == len(response.json()['detections'])
