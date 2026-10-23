"""Immutable allowlist: clients cannot select URLs, paths or weights."""
from dataclasses import dataclass
from types import MappingProxyType
from app.coco_labels import COCO_LABELS
from app.config import Settings
from app.schemas import ModelMetadata

ACCEPTED_CONTENT_TYPES = ('image/jpeg', 'image/png', 'image/webp')

@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    task: str
    source: str
    revision: str
    sha256: str
    size_bytes: int
    weights: str
    input_size: int
    license: str

YOLOV8N = ModelSpec(
    id='yolov8n', name='YOLOv8n (COCO, ONNX, 640)', task='object-detection',
    source='https://github.com/hrithegreat-gif/CCD-Lab-Project/releases/download/model-yolov8n-v1/yolov8n-640.onnx',
    revision='model-yolov8n-v1',
    sha256='946ca666d84aaf682e348c80996b94be55a36408bfa8a718aaf3e3c758e35b01',
    size_bytes=12851044, weights='yolov8n-640.onnx', input_size=640, license='AGPL-3.0',
)
REGISTRY = MappingProxyType({YOLOV8N.id: YOLOV8N})

def metadata(spec: ModelSpec, settings: Settings, ready: bool) -> ModelMetadata:
    return ModelMetadata(id=spec.id,name=spec.name,task=spec.task,source=spec.source,
        revision=spec.revision,runtime='ONNX Runtime CPU',labels=list(COCO_LABELS),ready=ready,
        input_size=spec.input_size,max_image_bytes=settings.max_image_bytes,
        max_image_pixels=settings.max_image_pixels,default_conf=settings.conf_threshold,
        default_iou=settings.iou_threshold,max_detections=settings.max_detections,
        accepted_content_types=list(ACCEPTED_CONTENT_TYPES))
