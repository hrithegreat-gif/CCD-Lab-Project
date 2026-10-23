# API contract

The service accepts one model, `yolov8n`, and raw image bytes. It does not
accept JSON/base64 inference requests or client-selected model identifiers.

## Authentication

When `API_KEY` is nonempty, send `Authorization: Bearer <key>` to every `/api/*`
route and `/metrics`. An empty key disables auth for local-only development.
Health probes and Studio assets are public. Auth rejection occurs before the
protected request body is read.

## Routes

| Method and path | Purpose |
| --- | --- |
| `GET /health/live` | Process liveness; independent of model readiness. |
| `GET /health/ready` | 200 after model load and warm-up; otherwise 503. |
| `GET /metrics` | Prometheus exposition; authenticated when a key is configured. |
| `GET /api/models` | Allowlisted model metadata. |
| `GET /api/models/{model_id}` | Metadata for `yolov8n`; unknown IDs return 404. |
| `POST /api/detect` | Detect COCO objects from a raw image request body. |

## Detection request

The body is the image file itself. Allowed media types are `image/jpeg`,
`image/png`, and `image/webp`; media-type parameters are ignored. The declared
type must agree with the file signature. The default image body limit is 5 MiB;
the HTTP request limit includes framing allowance.

| Query | Default | Range |
| --- | ---: | ---: |
| `conf` | `0.25` | `0.05` to `0.95` |
| `iou` | `0.45` | `0.10` to `0.95` |
| `max_detections` | `100` | `1` to the configured maximum, default `100` |

Example:

```sh
curl --data-binary @app/static/sample-cat.jpg \
  -H 'Content-Type: image/jpeg' \
  'http://127.0.0.1:8000/api/detect?conf=0.25&iou=0.45'
```

## Success response

```json
{
  "status": "success",
  "model": "yolov8n",
  "image": {"width": 640, "height": 480},
  "count": 1,
  "detections": [
    {"class_id": 15, "label": "cat", "confidence": 0.9,
     "box": {"x1": 100.0, "y1": 80.0, "x2": 400.0, "y2": 420.0}}
  ],
  "parameters": {"conf": 0.25, "iou": 0.45, "max_detections": 100},
  "processing_time_ms": 142.7,
  "timings_ms": {"decode": 9.1, "inference": 118.0, "postprocess": 1.4}
}
```

Coordinates are pixels in the EXIF-oriented original image. Boxes are clipped
to image bounds and have positive area. Detections are sorted by confidence
descending, `count` equals the list length, and a valid image with no objects
returns 200 with an empty list.

## Errors

| Status | Meaning | Detail |
| ---: | --- | --- |
| 401 | Missing or invalid Bearer key | `Unauthorized` |
| 413 | Request or image byte limit exceeded | `Request body too large` |
| 415 | Unsupported media type or signature mismatch | `Unsupported image type` |
| 422 | Invalid query/image, animation, or pixel limit | `Invalid image` or sanitized validation details |
| 429 | No inference slot; includes `Retry-After: 1` | `Inference capacity reached; retry later` |
| 503 | Model is not ready | `Model is not ready` |
| 504 | ONNX inference deadline exceeded | `Inference timed out` |
| 500 | Unexpected server error | `Inference failed` |

Errors do not echo image bytes, filenames, metadata, or query values. The
decoder checks dimensions before loading pixels, rejects animation, applies
EXIF orientation, and converts accepted images to RGB.