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
def test_real_model_cpu_provider_and_timeout(real_client):
    model=real_client.app.state.models['yolov8n']
    assert model._session.get_providers()==['CPUExecutionProvider']
    previous=model.settings.inference_timeout_seconds
    try:
        model.settings.inference_timeout_seconds=.000001
        r=real_client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'})
        assert r.status_code==504
    finally:model.settings.inference_timeout_seconds=previous
    assert real_client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==200
