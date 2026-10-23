"""Object detection response contract."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Box(StrictModel):
    x1: float = Field(ge=0)
    y1: float = Field(ge=0)
    x2: float = Field(gt=0)
    y2: float = Field(gt=0)
    @model_validator(mode='after')
    def positive_area(self):
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError('Box must have positive area')
        return self

class Detection(StrictModel):
    class_id: int = Field(ge=0, le=79, strict=True)
    label: str
    confidence: float = Field(ge=0, le=1)
    box: Box

class ImageInfo(StrictModel):
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)

class DetectParameters(StrictModel):
    conf: float = Field(ge=.05, le=.95)
    iou: float = Field(ge=.10, le=.95)
    max_detections: int = Field(ge=1, le=300)

class Timings(StrictModel):
    decode: float = Field(ge=0)
    inference: float = Field(ge=0)
    postprocess: float = Field(ge=0)

class DetectResponse(StrictModel):
    status: Literal['success'] = 'success'
    model: Literal['yolov8n'] = 'yolov8n'
    image: ImageInfo
    count: int = Field(ge=0, le=300)
    detections: list[Detection]
    parameters: DetectParameters
    processing_time_ms: float = Field(ge=0)
    timings_ms: Timings
    @model_validator(mode='after')
    def consistent_detections(self):
        from app.coco_labels import COCO_LABELS
        if self.count != len(self.detections) or self.count > self.parameters.max_detections:
            raise ValueError('Invalid detection count')
        previous = 1.0
        for item in self.detections:
            if (item.box.x2 > self.image.width or item.box.y2 > self.image.height
                    or item.confidence > previous or item.confidence < self.parameters.conf
                    or item.label != COCO_LABELS[item.class_id]):
                raise ValueError('Invalid detection')
            previous = item.confidence
        return self

class ModelMetadata(StrictModel):
    id: str
    name: str
    task: str
    source: str
    revision: str
    runtime: str
    labels: list[str]
    ready: bool
    input_size: int
    max_image_bytes: int
    max_image_pixels: int
    default_conf: float
    default_iou: float
    max_detections: int
    accepted_content_types: list[str]

class ErrorResponse(BaseModel):
    detail: str
