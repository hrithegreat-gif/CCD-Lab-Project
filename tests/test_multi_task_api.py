from tests.conftest import image_bytes


def test_segmentation_response_has_bounded_mask_payload(client):
    response = client.post(
        '/api/segment', content=image_bytes(),
        headers={'Content-Type': 'image/jpeg'},
    )
    assert response.status_code == 200
    data = response.json()
    assert data['model'] == 'yolov8n-seg'
    assert data['count'] == len(data['segmentations']) == 1
    mask = data['segmentations'][0]['mask']
    assert mask['width'] <= 160 and mask['height'] <= 160
    assert mask['png_base64']
    assert data['timings_ms']['inference'] >= 0


def test_classification_response_honors_top_k(client):
    response = client.post(
        '/api/classify?top_k=3', content=image_bytes(),
        headers={'Content-Type': 'image/jpeg'},
    )
    assert response.status_code == 200
    data = response.json()
    assert data['model'] == 'yolov8n-cls'
    assert data['parameters'] == {'top_k': 3}
    assert [item['class_id'] for item in data['predictions']] == [0, 1, 2]
    assert [item['confidence'] for item in data['predictions']] == sorted(
        [item['confidence'] for item in data['predictions']], reverse=True,
    )


def test_classification_rejects_invalid_top_k(client):
    for query in ('top_k=0', 'top_k=11', 'top_k=nan'):
        assert client.post(
            f'/api/classify?{query}', content=image_bytes(),
            headers={'Content-Type': 'image/jpeg'},
        ).status_code == 422


def test_multi_task_routes_share_image_validation(client):
    for endpoint in ('/api/segment', '/api/classify'):
        for body, content_type, expected in (
            (b'', 'image/jpeg', 422),
            (b'not an image', 'image/jpeg', 415),
            (image_bytes(), 'text/plain', 415),
        ):
            response = client.post(
                endpoint, content=body,
                headers={'Content-Type': content_type},
            )
            assert response.status_code == expected


def test_task_routes_are_documented_in_openapi(client):
    paths = client.get('/openapi.json').json()['paths']
    assert '/api/detect' in paths
    assert '/api/segment' in paths
    assert '/api/classify' in paths
    assert paths['/api/segment']['post']['responses']['200']['content']['application/json']['schema']
    assert paths['/api/classify']['post']['responses']['200']['content']['application/json']['schema']
