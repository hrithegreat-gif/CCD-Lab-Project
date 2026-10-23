"""Pinned YOLO inference using CPU ONNX Runtime; no exporter dependencies."""
import threading
from dataclasses import dataclass
from time import perf_counter
import onnxruntime as ort
from PIL import Image
from app.coco_labels import COCO_LABELS
from app.model_assets import download_assets
from app.postprocess import preprocess, postprocess

class InferenceTimeout(Exception):
    """The native ONNX run exceeded its deadline."""

@dataclass(frozen=True)
class DetectionResult:
    detections: list[dict]
    inference_ms: float
    postprocess_ms: float

class YoloDetector:
    def __init__(self,spec,settings):
        self.spec,self.settings=spec,settings
        self._session=None
        self._ready=False

    @property
    def ready(self):
        return self._ready and self._session is not None

    def load(self):
        assets=download_assets(self.spec,self.settings)
        options=ort.SessionOptions()
        options.intra_op_num_threads=self.settings.inference_threads
        options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        session=ort.InferenceSession(str(assets[self.spec.weights]),sess_options=options,
                                     providers=['CPUExecutionProvider'])
        inputs,outputs=session.get_inputs(),session.get_outputs()
        if (len(inputs)!=1 or inputs[0].shape != [1,3,self.spec.input_size,self.spec.input_size]
                or inputs[0].type!='tensor(float)' or len(outputs)!=1
                or outputs[0].shape != [1,4+len(COCO_LABELS),8400]
                or outputs[0].type!='tensor(float)'):
            raise ValueError('Unexpected ONNX model contract')
        self._session=session
        try:
            with Image.new('RGB',(self.spec.input_size,self.spec.input_size),(114,114,114)) as image:
                self.predict(image,self.settings.conf_threshold,self.settings.iou_threshold,self.settings.max_detections)
            self._ready=True
        except BaseException:
            self.close()
            raise

    def predict(self,image,conf,iou,max_det):
        session=self._session
        if session is None: raise RuntimeError('Model is not loaded')
        tensor,letterbox=preprocess(image,self.spec.input_size)
        run_options=ort.RunOptions()
        expired=threading.Event()
        def terminate():
            expired.set()
            run_options.terminate=True
        timer=threading.Timer(self.settings.inference_timeout_seconds,terminate)
        timer.daemon=True
        began=perf_counter()
        timer.start()
        try:
            raw=session.run([session.get_outputs()[0].name],{session.get_inputs()[0].name:tensor},run_options=run_options)[0]
        except Exception as exc:
            if expired.is_set(): raise InferenceTimeout('Inference deadline exceeded') from exc
            raise
        finally:
            timer.cancel()
        if expired.is_set(): raise InferenceTimeout('Inference deadline exceeded')
        inference_ms=(perf_counter()-began)*1000
        began=perf_counter()
        detections=postprocess(raw,letterbox,conf,iou,max_det)
        for item in detections: item['label']=COCO_LABELS[item['class_id']]
        return DetectionResult(detections,inference_ms,(perf_counter()-began)*1000)

    def close(self):
        self._ready=False
        self._session=None
