"""Export and verify pinned YOLOv8n-seg and YOLOv8n-cls ONNX CPU assets."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.model_registry import YOLOV8N_CLS, YOLOV8N_SEG

CHECKPOINTS = {
    YOLOV8N_SEG.id: (
        YOLOV8N_SEG,
        'a7cd8f929e1903d78a12a48efecab430209f18dc46cb96c3599a5980c63c423c',
        [1, 116, 8400],
        [[1, 32, 160, 160]],
    ),
    YOLOV8N_CLS.id: (
        YOLOV8N_CLS,
        '11fa19f2aea79bc960d680a13f82f22105982b325eb9e17a4a5e1a9f8245980a',
        [1, 1000],
        [],
    ),
}
EXPORTER_VERSION = '8.3.221'


def _download_checkpoint(spec, expected_sha256: str, destination: Path) -> None:
    import httpx

    digest = hashlib.sha256()
    temp = destination.with_suffix('.download')
    try:
        with httpx.stream(
            'GET', spec.source, follow_redirects=True, timeout=60
        ) as response:
            response.raise_for_status()
            with temp.open('wb') as handle:
                for chunk in response.iter_bytes(chunk_size=65536):
                    digest.update(chunk)
                    handle.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError(f'Checkpoint checksum mismatch for {spec.id}')
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _verify_export(path: Path, spec, expected_output, expected_extra) -> None:
    import ast
    import numpy as np
    import onnx
    import onnxruntime as ort
    import ultralytics

    if ultralytics.__version__ != EXPORTER_VERSION:
        raise ValueError('Unexpected Ultralytics export version')
    graph = onnx.load(str(path))
    metadata = {item.key: item.value for item in graph.metadata_props}
    props = [item for item in graph.metadata_props if item.key != 'date']
    del graph.metadata_props[:]
    graph.metadata_props.extend(props)
    onnx.save(graph, str(path))

    session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (
        len(inputs) != 1
        or inputs[0].shape != [1, 3, spec.input_size, spec.input_size]
        or inputs[0].type != 'tensor(float)'
        or [output.shape for output in outputs] != [expected_output, *expected_extra]
        or any(output.type != 'tensor(float)' for output in outputs)
        or session.get_providers() != ['CPUExecutionProvider']
        or metadata.get('task') != {
            'instance-segmentation': 'segment',
            'image-classification': 'classify',
        }[spec.task]
    ):
        raise ValueError(f'Unexpected exported ONNX contract for {spec.id}')
    names = ast.literal_eval(metadata.get('names', ''))
    expected_classes = 1000 if spec.id == YOLOV8N_CLS.id else 80
    if not isinstance(names, dict) or set(names) != set(range(expected_classes)):
        raise ValueError(f'Unexpected exported class labels for {spec.id}')

    inputs_value = np.zeros(inputs[0].shape, dtype=np.float32)
    results = session.run(None, {inputs[0].name: inputs_value})
    if spec.id == YOLOV8N_CLS.id:
        probabilities = results[0]
        if (
            not np.isfinite(probabilities).all()
            or (probabilities < 0).any()
            or (probabilities > 1).any()
            or not np.isclose(probabilities.sum(), 1.0, atol=1e-3)
        ):
            raise ValueError('Classification export did not return probabilities')
    elif any(not np.isfinite(value).all() for value in results):
        raise ValueError('Segmentation export returned non-finite output')

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.stat().st_size != spec.size_bytes or digest != spec.sha256:
        raise ValueError(f'Export digest mismatch for {spec.id}')


def prepare(cache_dir: Path) -> list[dict]:
    from ultralytics import YOLO

    records = []
    with tempfile.TemporaryDirectory(prefix='yolo-model-export-') as work:
        temporary = Path(work)
        for model_id, (spec, checkpoint_sha256, output, extra) in CHECKPOINTS.items():
            checkpoint = temporary / f'{model_id}.pt'
            _download_checkpoint(spec, checkpoint_sha256, checkpoint)
            exported = Path(YOLO(str(checkpoint)).export(
                format='onnx',
                imgsz=spec.input_size,
                opset=17,
                simplify=True,
                dynamic=False,
                half=False,
                device='cpu',
                project=str(temporary / 'exports'),
                name=model_id,
                exist_ok=True,
            ))
            _verify_export(exported, spec, output, extra)
            target_dir = cache_dir / model_id / spec.sha256[:12]
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / spec.weights
            shutil.copyfile(exported, target)
            records.append({
                'id': model_id,
                'source': spec.source,
                'revision': spec.revision,
                'checkpoint_sha256': checkpoint_sha256,
                'onnx_sha256': spec.sha256,
                'onnx_bytes': spec.size_bytes,
                'input_shape': [1, 3, spec.input_size, spec.input_size],
                'output_shapes': [output, *extra],
                'path': str(target),
            })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bake', type=Path, help='Cache destination; ignores local .env')
    parser.add_argument('--verify', action='store_true',
                        help='Validate already-prepared assets without network access')
    args = parser.parse_args()
    cache_dir = args.bake or Settings().model_cache_dir
    if args.verify:
        from app.model_assets import download_assets
        for spec, *_ in CHECKPOINTS.values():
            asset = download_assets(
                spec, Settings(_env_file=None, model_cache_dir=cache_dir,
                               model_local_files_only=True)
            )[spec.weights]
            print(f'PASS {spec.id}: {asset.stat().st_size} bytes, sha256={spec.sha256}')
        return 0
    try:
        for record in prepare(cache_dir):
            print(
                f"PASS {record['id']}: {record['onnx_bytes']} bytes, "
                f"sha256={record['onnx_sha256']}, outputs={record['output_shapes']}"
            )
    except Exception as exc:
        print(f'FAIL: model preparation ({type(exc).__name__}: {exc}). Check exporter, network, and cache.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
