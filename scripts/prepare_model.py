"""Download hash-pinned detection weights and optionally verify real inference."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from app.config import Settings
from app.inference import YoloDetector
from app.model_assets import download_assets
from app.model_registry import YOLOV8N


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--bake', type=Path, help='Build cache destination; ignores local .env')
    args = parser.parse_args()
    settings = (Settings(_env_file=None, model_cache_dir=args.bake, model_local_files_only=False)
                if args.bake else Settings())
    model = None
    try:
        assets = download_assets(YOLOV8N, settings)
        print(json.dumps({'source': YOLOV8N.source, 'revision': YOLOV8N.revision,
                          'sha256': YOLOV8N.sha256, 'bytes': sum(p.stat().st_size for p in assets.values())}))
        if args.verify:
            model = YoloDetector(YOLOV8N, settings)
            model.load()
            for sample, expected in [('cat', 'cat'), ('coffee', 'cup'), ('person', 'person')]:
                with Image.open(ROOT / f'app/static/sample-{sample}.jpg') as image:
                    result = model.predict(image, .25, .45, 100)
                if not any(d['label'] == expected and d['confidence'] > .5 for d in result.detections):
                    raise RuntimeError('Expected real sample detection missing')
                print(json.dumps({'sample': sample, 'detections': result.detections}))
        print('PASS: pinned model assets are available.')
        return 0
    except Exception as exc:
        print(f'FAIL: model preparation ({type(exc).__name__}). Check network and cache.')
        return 1
    finally:
        if model is not None:
            model.close()


if __name__ == '__main__':
    raise SystemExit(main())
