import asyncio,io,json,logging,threading
from types import SimpleNamespace
import pytest
from app.main import create_app
from app.config import Settings
from app.observability import logger
from tests.conftest import TestModel,image_bytes

@pytest.mark.parametrize('fmt,ctype',[('JPEG','image/jpeg'),('PNG','image/png'),('WEBP','image/webp')])
def test_response_contract(client,fmt,ctype):
    response=client.post('/api/detect',content=image_bytes(fmt),headers={'Content-Type':ctype})
    assert response.status_code==200
    data=response.json();assert data['count']==len(data['detections'])==1
    assert data['image']==dict(width=64,height=48)
    assert set(data['timings_ms'])=={'decode','inference','postprocess'}
    scores=[d['confidence'] for d in data['detections']];assert scores==sorted(scores,reverse=True)

def test_configured_max_detections():
    from fastapi.testclient import TestClient
    with TestClient(create_app(Settings(_env_file=None,max_detections=2),model_factory=TestModel)) as client:
        assert client.post('/api/detect?max_detections=3',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==422

def test_decode_errors_release_capacity(client):
    assert client.post('/api/detect',content=b'\xff\xd8\xffbad',headers={'Content-Type':'image/jpeg'}).status_code==422
    assert client.post('/api/detect',content=image_bytes(),headers={'Content-Type':'image/jpeg'}).status_code==200

def test_logs_and_metrics_exclude_image_metadata_and_query(client):
    marker='PRIVATE-DETECTION-MARKER';stream=io.StringIO();handler=logging.StreamHandler(stream);logger.addHandler(handler)
    try:
        client.post('/api/detect?conf='+marker,content=image_bytes()+marker.encode(),headers={'Content-Type':'image/jpeg','X-Filename':marker})
        client.post('/api/detect',content=image_bytes()+marker.encode(),headers={'Content-Type':'image/jpeg','X-Filename':marker})
    finally:logger.removeHandler(handler)
    assert marker not in stream.getvalue() and marker not in client.get('/metrics').text
    for line in stream.getvalue().splitlines():
        event=json.loads(line)
        if event['event']=='detection':assert set(event)=={'event','status_code','processing_time_ms','count'}

def test_upload_does_not_hold_slot_and_cancel_does_not_release_native_work():
    async def scenario():
        app=create_app(Settings(_env_file=None),model_factory=TestModel)
        entered,release,done=threading.Event(),threading.Event(),threading.Event()
        class Blocked(TestModel):
            def predict(self,*args):
                entered.set()
                try:
                    assert release.wait(5)
                    return super().predict(*args)
                finally:done.set()
        model=Blocked(None,None);model.load();app.state.models['yolov8n']=model
        endpoint=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/api/detect')
        async def body():
            assert app.state.slots.acquire(False),'slow upload must not occupy inference capacity'
            app.state.slots.release();return image_bytes()
        request=SimpleNamespace(headers={'content-type':'image/jpeg'},body=body)
        task=asyncio.create_task(endpoint(request,.25,.45,100))
        try:
            for _ in range(500):
                if entered.is_set():break
                await asyncio.sleep(.01)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert not app.state.slots.acquire(False),'cancelled HTTP request must not bypass native capacity'
        finally:release.set()
        for _ in range(500):
            if done.is_set() and app.state.slots.acquire(False):
                app.state.slots.release();break
            await asyncio.sleep(.01)
        else:pytest.fail('native completion leaked the slot')
    asyncio.run(scenario())

@pytest.mark.parametrize('path',['/api/inference','/api/generate','/api/speech','/api/transcriptions','/api/capabilities'])
def test_removed_routes_are_absent(client,path):
    assert path not in client.get('/openapi.json').json()['paths']
    assert client.post(path).status_code==404
