"""Verified, atomic downloads of allowlisted model assets."""
import hashlib
import os
from pathlib import Path
import tempfile

import httpx


def _valid(path: Path, spec) -> bool:
    if not path.is_file() or path.stat().st_size != spec.size_bytes:
        return False
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest() == spec.sha256


def download_assets(spec, settings) -> dict[str, Path]:
    directory = settings.model_cache_dir / spec.id / spec.sha256[:12]
    target = directory / spec.weights
    if _valid(target, spec):
        return {spec.weights: target}
    if settings.model_local_files_only:
        raise ValueError('Verified model assets are unavailable offline')
    directory.mkdir(parents=True, exist_ok=True)
    # A corrupt cache is never used; temporary files are removed on every failure.
    target.unlink(missing_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
            temp = Path(handle.name)
            digest = hashlib.sha256()
            size = 0
            with httpx.stream('GET', spec.source, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                for block in response.iter_bytes(chunk_size=65536):
                    size += len(block)
                    if size > spec.size_bytes:
                        raise ValueError('Model asset exceeds pinned size')
                    digest.update(block)
                    handle.write(block)
            if size != spec.size_bytes or digest.hexdigest() != spec.sha256:
                raise ValueError('Model asset verification failed')
        os.replace(temp, target)
        return {spec.weights: target}
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
