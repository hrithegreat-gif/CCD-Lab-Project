"""Developer-only reproducible FP32 YOLOv8n export; never imported by serving."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.coco_labels import COCO_LABELS


def main():
    import numpy as np
    import onnx
    import onnxruntime as ort
    import ultralytics
    from ultralytics import YOLO

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--weights', default='yolov8n.pt')
    args = parser.parse_args()
    target = ROOT / 'models/artifacts/yolov8n-640.onnx'
    target.parent.mkdir(parents=True, exist_ok=True)
    exported = Path(YOLO(args.weights).export(format='onnx', imgsz=640, opset=17,
                                            simplify=True, dynamic=False, half=False, device='cpu'))
    if exported.resolve() != target.resolve():
        shutil.copyfile(exported, target)
    session = ort.InferenceSession(str(target), providers=['CPUExecutionProvider'])
    names = ast.literal_eval(session.get_modelmeta().custom_metadata_map['names'])
    assert tuple(names[i] for i in range(80)) == COCO_LABELS and len(names) == 80
    assert session.get_inputs()[0].shape == [1, 3, 640, 640]
    assert session.get_outputs()[0].shape == [1, 84, 8400]
    output = session.run(None, {session.get_inputs()[0].name: np.full((1,3,640,640),114/255,np.float32)})[0]
    assert np.isfinite(output).all() and (output[:,4:] >= 0).all() and (output[:,4:] <= 1).all()
    manifest = dict(size_bytes=target.stat().st_size, sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    ultralytics=ultralytics.__version__, opsets=[x.version for x in onnx.load(str(target)).opset_import],
                    inputs=[(x.name,x.shape,x.type) for x in session.get_inputs()],
                    outputs=[(x.name,x.shape,x.type) for x in session.get_outputs()], names=names,
                    weights_sha256=hashlib.sha256(Path(args.weights).read_bytes()).hexdigest())
    target.with_suffix('.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()
