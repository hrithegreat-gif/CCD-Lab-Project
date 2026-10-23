# Model provenance and operation

Model ID: `yolov8n`.

- Task: object detection, 80 COCO classes.
- Runtime: ONNX Runtime CPU, static 640 by 640 FP32 input.
- Exporter: `ultralytics==8.3.221`, ONNX opset 17.
- Input: `images`, shape `[1, 3, 640, 640]`.
- Output: `output0`, shape `[1, 84, 8400]` (four box values plus 80 class scores).
- Export size: 12,851,044 bytes.
- SHA-256: `946ca666d84aaf682e348c80996b94be55a36408bfa8a718aaf3e3c758e35b01`.
- Release: `model-yolov8n-v1`, pinned in `app/model_registry.py`.

The export metadata and upstream checkpoint digest are in
[yolov8n-export.json](yolov8n-export.json). The ONNX asset is downloaded from
the project's GitHub Release and hash-verified before use. Docker builds bake
the verified model for offline startup; Kubernetes sets
`MODEL_LOCAL_FILES_ONLY=true`. PyTorch and Ultralytics are developer export
tools, not serving dependencies.

Ultralytics YOLOv8 code and pretrained weights are distributed under AGPL-3.0,
with a separate commercial license option. Review current upstream terms and
obligations with the project owner before shipping; this is not legal advice.
See [NOTICE.md](NOTICE.md) and the included
[AGPL-3.0 license](LICENSE-AGPL-3.0.txt). The public project source is at
https://github.com/hrithegreat-gif/CCD-Lab-Project.

COCO training-data attribution and bundled sample-photo provenance are
described in [NOTICE.md](NOTICE.md) and [app/static/SAMPLES.md](../app/static/SAMPLES.md).
The sample photos are functional demonstrations, not an accuracy evaluation
set. Detection confidence is not calibrated correctness; results can be wrong
or missing.

The detector loads once per process, runs on `CPUExecutionProvider`, and must
complete a synthetic warm-up before readiness succeeds. Runtime model downloads
are restricted to the pinned registry asset; client requests cannot select a
remote model.
Use synthetic sentences for demos and omit input text from logs.
