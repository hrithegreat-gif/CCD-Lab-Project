"""Reference pre/post-processing for a YOLOv8 ONNX detector (numpy + Pillow only)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

INPUT_SIZE = 640
PAD_VALUE = 114
PRE_NMS_TOP_K = 1000


@dataclass(frozen=True)
class Letterbox:
    scale: float
    pad_x: int
    pad_y: int
    orig_w: int
    orig_h: int


def preprocess(img: Image.Image, size: int = INPUT_SIZE) -> tuple[np.ndarray, Letterbox]:
    """RGB PIL image -> float32 NCHW tensor in [0,1] plus the mapping back to the original."""
    img = img.convert("RGB")
    w, h = img.size
    scale = min(size / w, size / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (PAD_VALUE,) * 3)
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0          # HWC
    tensor = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # 1x3xHxW
    return tensor, Letterbox(scale, pad_x, pad_y, w, h)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy NMS on xyxy boxes; returns indices of kept boxes, highest score first."""
    order = scores.argsort()[::-1]
    keep: list[int] = []
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


def postprocess(raw: np.ndarray, lb: Letterbox, conf: float, iou: float, max_det: int,
                num_classes: int = 80) -> list[dict]:
    """raw: (1, 4+num_classes, N) YOLOv8 output -> detections in ORIGINAL image pixels."""
    if raw.ndim != 3 or raw.shape[0] != 1 or raw.shape[1] != 4 + num_classes:
        raise ValueError("Unexpected detector output shape")
    if not np.isfinite(raw).all() or (raw[:, 4:] < 0).any() or (raw[:, 4:] > 1).any():
        raise ValueError("Non-finite detector output")
    preds = raw[0].T                                  # (N, 4+C)
    class_scores = preds[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(preds)), class_ids]
    mask = scores >= conf
    if not mask.any():
        return []
    preds, class_ids, scores = preds[mask], class_ids[mask], scores[mask]
    if len(scores) > PRE_NMS_TOP_K:                   # bound worst-case NMS time
        top = scores.argsort()[::-1][:PRE_NMS_TOP_K]
        preds, class_ids, scores = preds[top], class_ids[top], scores[top]
    cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
    xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
    # Group by class: fixed coordinate offsets are unsafe for unbounded boxes.
    keep = []
    for cls in np.unique(class_ids):
        indices = np.flatnonzero(class_ids == cls)
        keep.extend(int(indices[j]) for j in nms(xyxy[indices], scores[indices], iou))
    keep = sorted(keep, key=lambda i: float(scores[i]), reverse=True)[:max_det]
    out = []
    for i in keep:
        x1, y1, x2, y2 = xyxy[i]
        x1 = float(np.clip((x1 - lb.pad_x) / lb.scale, 0, lb.orig_w))
        x2 = float(np.clip((x2 - lb.pad_x) / lb.scale, 0, lb.orig_w))
        y1 = float(np.clip((y1 - lb.pad_y) / lb.scale, 0, lb.orig_h))
        y2 = float(np.clip((y2 - lb.pad_y) / lb.scale, 0, lb.orig_h))
        if x2 - x1 < 1 or y2 - y1 < 1:
            continue
        out.append({"class_id": int(class_ids[i]), "confidence": float(scores[i]),
                    "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}})
    return out
