"""YOLOv8n-seg CPU inference and bounded per-instance PNG mask encoding."""
import base64
import io
import threading
from dataclasses import dataclass
from time import perf_counter

import numpy as np
import onnxruntime as ort
from PIL import Image

from app.coco_labels import COCO_LABELS
from app.inference import InferenceTimeout
from app.model_assets import download_assets
from app.postprocess import preprocess, postprocess_with_masks

_MASK_CHANNELS = 32
_MASK_SIZE = 160


@dataclass(frozen=True)
class SegmentationResult:
    segmentations: list[dict]
    inference_ms: float
    postprocess_ms: float


def _encode_instance_mask(
    prototypes: np.ndarray,
    coefficients: np.ndarray,
    detection: dict,
    letterbox,
) -> dict:
    proto_height, proto_width = prototypes.shape[1:]
    mask_logits = coefficients @ prototypes.reshape(_MASK_CHANNELS, -1)
    mask_logits = mask_logits.reshape(proto_height, proto_width)
    box = detection['box']
    model_x1 = box['x1'] * letterbox.scale + letterbox.pad_x
    model_y1 = box['y1'] * letterbox.scale + letterbox.pad_y
    model_x2 = box['x2'] * letterbox.scale + letterbox.pad_x
    model_y2 = box['y2'] * letterbox.scale + letterbox.pad_y
    x1 = max(0, int(np.floor(model_x1 * proto_width / 640)))
    y1 = max(0, int(np.floor(model_y1 * proto_height / 640)))
    x2 = min(proto_width, int(np.ceil(model_x2 * proto_width / 640)))
    y2 = min(proto_height, int(np.ceil(model_y2 * proto_height / 640)))
    cropped = mask_logits[y1:y2, x1:x2]
    if cropped.size == 0:
        raise ValueError('Empty predicted segmentation mask')

    left = max(0, int(np.floor(box['x1'])))
    top = max(0, int(np.floor(box['y1'])))
    right = min(letterbox.orig_w, int(np.ceil(box['x2'])))
    bottom = min(letterbox.orig_h, int(np.ceil(box['y2'])))
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError('Invalid segmentation bounds')
    mask_size = (min(width, _MASK_SIZE), min(height, _MASK_SIZE))
    resized = Image.fromarray(cropped.astype(np.float32), mode='F').resize(
        mask_size, Image.Resampling.BILINEAR
    )
    binary = (np.asarray(resized, dtype=np.float32) > 0).astype(np.uint8) * 255
    output = io.BytesIO()
    Image.fromarray(binary, mode='L').save(output, format='PNG', optimize=True)
    return {
        'x': left, 'y': top, 'width': mask_size[0], 'height': mask_size[1],
        'png_base64': base64.b64encode(output.getvalue()).decode('ascii'),
    }


class YoloSegmenter:
    def __init__(self, spec, settings):
        self.spec, self.settings = spec, settings
        self._session = None
        self._ready = False

    @property
    def ready(self):
        return self._ready and self._session is not None

    def load(self):
        assets = download_assets(self.spec, self.settings)
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.settings.inference_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(
            str(assets[self.spec.weights]), sess_options=options,
            providers=['CPUExecutionProvider'],
        )
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].shape != [1, 3, self.spec.input_size, self.spec.input_size]
            or inputs[0].type != 'tensor(float)'
            or len(outputs) != 2
            or outputs[0].shape != [1, 116, 8400]
            or outputs[1].shape != [1, _MASK_CHANNELS, _MASK_SIZE, _MASK_SIZE]
            or any(output.type != 'tensor(float)' for output in outputs)
            or session.get_providers() != ['CPUExecutionProvider']
        ):
            raise ValueError('Unexpected segmentation ONNX model contract')
        self._session = session
        try:
            with Image.new('RGB', (self.spec.input_size, self.spec.input_size)) as image:
                self.predict(image, self.settings.conf_threshold,
                             self.settings.iou_threshold, self.settings.max_detections)
            self._ready = True
        except BaseException:
            self.close()
            raise

    def predict(self, image, conf, iou, max_det) -> SegmentationResult:
        session = self._session
        if session is None:
            raise RuntimeError('Segmentation model is not loaded')
        tensor, letterbox = preprocess(image, self.spec.input_size)
        options = ort.RunOptions()
        expired = threading.Event()

        def terminate():
            expired.set()
            options.terminate = True

        timer = threading.Timer(self.settings.inference_timeout_seconds, terminate)
        timer.daemon = True
        started = perf_counter()
        timer.start()
        try:
            raw, prototypes = session.run(
                [output.name for output in session.get_outputs()],
                {session.get_inputs()[0].name: tensor},
                options,
            )
        except Exception as exc:
            if expired.is_set():
                raise InferenceTimeout('Inference deadline exceeded') from exc
            raise
        finally:
            timer.cancel()
        if expired.is_set():
            raise InferenceTimeout('Inference deadline exceeded')
        inference_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        if (
            raw.shape != (1, 116, 8400)
            or prototypes.shape != (1, _MASK_CHANNELS, _MASK_SIZE, _MASK_SIZE)
            or not np.isfinite(prototypes).all()
        ):
            raise ValueError('Invalid segmentation output')
        detections = postprocess_with_masks(
            raw, letterbox, conf, iou, max_det,
            mask_channels=_MASK_CHANNELS,
        )
        segmentations = []
        for detection, coefficients in detections:
            detection['label'] = COCO_LABELS[detection['class_id']]
            detection['mask'] = _encode_instance_mask(
                prototypes[0], coefficients, detection, letterbox
            )
            segmentations.append(detection)
        return SegmentationResult(
            segmentations, inference_ms, (perf_counter() - started) * 1000
        )

    def close(self):
        self._ready = False
        self._session = None
