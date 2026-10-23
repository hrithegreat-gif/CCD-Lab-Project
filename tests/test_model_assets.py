"""Asset verification must fail closed, offline and on interrupted downloads."""
from contextlib import contextmanager
import hashlib
from types import SimpleNamespace

import pytest
from app import model_assets


@pytest.fixture
def asset(tmp_path):
    data=b'verified model fixture'
    spec=SimpleNamespace(id='test',weights='model.onnx',sha256=hashlib.sha256(data).hexdigest(),
                         size_bytes=len(data),source='https://example.invalid/model')
    settings=SimpleNamespace(model_cache_dir=tmp_path,model_local_files_only=False)
    return data,spec,settings


def stream(monkeypatch, blocks):
    @contextmanager
    def fake(*args,**kwargs):
        yield SimpleNamespace(raise_for_status=lambda:None,iter_bytes=lambda **kw:iter(blocks))
    monkeypatch.setattr(model_assets.httpx,'stream',fake)


def test_download_and_offline_reuse(asset,monkeypatch):
    data,spec,settings=asset;stream(monkeypatch,[data[:3],data[3:]])
    path=model_assets.download_assets(spec,settings)[spec.weights]
    assert path.read_bytes()==data
    settings.model_local_files_only=True
    monkeypatch.setattr(model_assets.httpx,'stream',lambda *a,**k:pytest.fail('offline network'))
    assert model_assets.download_assets(spec,settings)[spec.weights]==path
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError):model_assets.download_assets(spec,settings)


@pytest.mark.parametrize('kind',['oversize','hash','short'])
def test_invalid_download_leaves_no_asset_or_temp(asset,monkeypatch,kind):
    data,spec,settings=asset
    bad={'oversize':data+b'x','hash':b'x'*len(data),'short':data[:-1]}[kind]
    stream(monkeypatch,[bad])
    with pytest.raises(ValueError):model_assets.download_assets(spec,settings)
    assert not any(p.is_file() for p in settings.model_cache_dir.rglob('*'))


def test_missing_offline_never_downloads(asset,monkeypatch):
    _,spec,settings=asset;settings.model_local_files_only=True
    monkeypatch.setattr(model_assets.httpx,'stream',lambda *a,**k:pytest.fail('offline network'))
    with pytest.raises(ValueError):model_assets.download_assets(spec,settings)
