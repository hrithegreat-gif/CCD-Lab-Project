"""Live detection acceptance; standard library only, also usable from deploy bundles."""
import argparse
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def validate_detection(body):
    if body['status'] != 'success' or body['model'] != 'yolov8n':
        raise ValueError('Unexpected detection model/status')
    width, height = body['image']['width'], body['image']['height']
    detections = body['detections']
    if not (width > 0 and height > 0 and body['count'] == len(detections)
            and 0 < len(detections) <= body['parameters']['max_detections']):
        raise ValueError('Expected nonempty valid detections')
    previous = 1.0
    for item in detections:
        box, score = item['box'], item['confidence']
        if not (0 <= box['x1'] < box['x2'] <= width and 0 <= box['y1'] < box['y2'] <= height
                and body['parameters']['conf'] <= score <= previous
                and 0 <= item['class_id'] < 80 and item['label']):
            raise ValueError('Invalid box, class or score')
        previous = score
    for value in [body['processing_time_ms'], *body['timings_ms'].values()]:
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid measured timing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--api-key', default=None, help='Prefer API_KEY environment variable')
    parser.add_argument('--image', type=Path, default=Path(__file__).resolve().parents[1] / 'loadtest/sample.jpg')
    args = parser.parse_args()
    key = (args.api_key if args.api_key is not None else os.environ.get('API_KEY', '')).strip()
    auth = {'Authorization': f'Bearer {key}'} if key else {}

    def call(path, data=None, content_type='image/jpeg', protected=True, raw=False):
        headers = dict(auth) if protected else {}
        if data is not None:
            headers['Content-Type'] = content_type
        request = Request(args.base_url.rstrip('/') + path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                status, payload = response.status, response.read()
        except HTTPError as exc:
            status, payload = exc.code, exc.read()
        return status, payload if raw else json.loads(payload)

    def expect_status(result, expected, check):
        if result[0] != expected:
            raise RuntimeError(f'{check}: expected {expected}, got {result[0]}')
        print(f'PASS: {check}')
        return result[1]

    try:
        for path in ('/health/live', '/health/ready'):
            expect_status(call(path, protected=False), 200, path)
        models = expect_status(call('/api/models'), 200, 'model registry')
        model = next(m for m in models if m['id'] == 'yolov8n')
        if not model['ready'] or len(model['labels']) != 80:
            raise ValueError('Model not ready or labels invalid')
        expect_status(call('/api/models/yolov8n'), 200, 'model metadata')
        if key:
            expect_status(call('/api/models', protected=False), 401, 'unauthenticated access rejected')
            expect_status(call('/api/detect', b'invalid', protected=False), 401, 'auth precedes decoding')
        image = args.image.read_bytes()
        body = expect_status(call('/api/detect', image), 200, 'real image inference')
        validate_detection(body)
        print(json.dumps(body))
        for path, data, content_type, status in (
            ('/api/detect', image, 'application/json', 415),
            ('/api/detect', b'\xff\xd8\xffbroken', 'image/jpeg', 422),
            ('/api/detect?conf=2', image, 'image/jpeg', 422),
            ('/api/detect', b'', 'image/jpeg', 422),
            ('/api/detect', b'x' * (model['max_image_bytes'] + 1), 'image/jpeg', 413),
        ):
            expect_status(call(path, data, content_type), status, f'validation {status}')
        expect_status(call('/api/models/unknown'), 404, 'unknown model')
        metrics = expect_status(call('/metrics', raw=True), 200, 'metrics')
        if not all(name in metrics for name in (b'http_requests_total', b'http_request_duration_seconds')):
            raise ValueError('Missing HTTP metrics')
        print('PASS: detection live HTTP acceptance checks.')
        return 0
    except (URLError, OSError, ValueError, KeyError, RuntimeError, StopIteration, TypeError) as exc:
        # Never echo URLs, headers or response bodies from failures.
        print(f'FAIL: detection acceptance ({type(exc).__name__}). Check server readiness/configuration.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
