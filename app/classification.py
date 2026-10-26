"""YOLOv8n-cls CPU inference with its own resize/crop and probability contract."""
import ast
import threading
from dataclasses import dataclass
from time import perf_counter

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps

from app.inference import InferenceTimeout
from app.model_assets import download_assets


@dataclass(frozen=True)
class ClassificationResult:
    predictions: list[dict]
    inference_ms: float
    postprocess_ms: float


def preprocess_classification(image: Image.Image, size: int) -> np.ndarray:
    """Match Ultralytics classify_transforms: shortest-edge resize, center crop, RGB / 255."""
    image = image.convert('RGB')
    width, height = image.size
    scale = size / min(width, height)
    resized_size = (int(width * scale), int(height * scale))
    resized = image.resize(resized_size, Image.Resampling.BILINEAR)
    left = round((resized.width - size) / 2)
    top = round((resized.height - size) / 2)
    cropped = resized.crop((left, top, left + size, top + size))
    pixels = np.asarray(cropped, dtype=np.float32) / 255.0
    return np.ascontiguousarray(pixels.transpose(2, 0, 1)[None])


class YoloClassifier:
    def __init__(self, spec, settings):
        self.spec, self.settings = spec, settings
        self._session = None
        self._ready = False
        self.labels: list[str] = []

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
            or len(outputs) != 1
            or outputs[0].shape != [1, 1000]
            or outputs[0].type != 'tensor(float)'
            or session.get_providers() != ['CPUExecutionProvider']
        ):
            raise ValueError('Unexpected classification ONNX model contract')
        raw_names = session.get_modelmeta().custom_metadata_map.get('names')
        try:
            names = ast.literal_eval(raw_names) if raw_names else None
        except (SyntaxError, ValueError):
            names = None
        if not isinstance(names, dict) or set(names) != set(range(1000)):
            raise ValueError('Unexpected classification labels')
        self.labels = [names[index] for index in range(1000)]
        if not all(isinstance(label, str) and label for label in self.labels):
            raise ValueError('Invalid classification labels')
        self._session = session
        try:
            with Image.new('RGB', (self.spec.input_size, self.spec.input_size)) as image:
                self.predict(image, 5)
            self._ready = True
        except BaseException:
            self.close()
            raise

    def predict(self, image, top_k: int) -> ClassificationResult:
        session = self._session
        if session is None:
            raise RuntimeError('Classification model is not loaded')
        tensor = preprocess_classification(image, self.spec.input_size)
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
            raw = session.run(
                [session.get_outputs()[0].name],
                {session.get_inputs()[0].name: tensor},
                options,
            )[0]
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
        probabilities = raw[0]
        if (
            probabilities.shape != (1000,)
            or not np.isfinite(probabilities).all()
            or (probabilities < 0).any()
            or (probabilities > 1).any()
            or not np.isclose(probabilities.sum(), 1.0, atol=1e-3)
        ):
            raise ValueError('Invalid classification output')
        indices = np.argsort(probabilities)[::-1][:top_k]
        predictions = [
            {'class_id': int(index), 'label': self.labels[index],
             'confidence': float(probabilities[index])}
            for index in indices
        ]
        return ClassificationResult(
            predictions, inference_ms, (perf_counter() - started) * 1000
        )

    def close(self):
        self._ready = False
        self._session = None
        self.labels = []
