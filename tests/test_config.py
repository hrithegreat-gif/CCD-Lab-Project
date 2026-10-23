import pytest
from pydantic import ValidationError
from app.config import Settings


@pytest.mark.parametrize("key,value", [
    ("max_image_bytes", 1023), ("max_image_pixels", 50000001),
    ("conf_threshold", .01), ("iou_threshold", .99), ("max_detections", 301),
    ("max_concurrent_inferences", 0), ("inference_threads", 0),
    ("inference_timeout_seconds", 0),
])
def test_invalid_settings_fail_fast(key, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{key: value})


def test_environment_overrides(monkeypatch):
    monkeypatch.setenv("MAX_IMAGE_BYTES", "12345")
    monkeypatch.setenv("MODEL_LOCAL_FILES_ONLY", "true")
    settings = Settings(_env_file=None)
    assert settings.max_image_bytes == 12345
    assert settings.model_local_files_only is True
