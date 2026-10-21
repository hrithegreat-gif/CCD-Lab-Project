"""Startup and shutdown behavior: blocking default, background loading, draining."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.config import Settings
from app.main import create_app
from tests.conftest import TestModel

WAIT_SECONDS = 10.0


def _wait_until(predicate, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class GatedModel(TestModel):
    """Load blocks until released; records close calls and worker completion.

    A daemon timer auto-releases the gate so a worker thread can never stay
    blocked past the test (executor threads are joined at interpreter exit).
    """
    __test__ = False

    def __init__(self, spec, settings, auto_release=5.0):
        super().__init__(spec, settings)
        self.gate = threading.Event()
        self.closed_count = 0
        self.load_finished = threading.Event()
        # Timer does not accept daemon=; set the attribute after construction.
        self._release_timer = threading.Timer(auto_release, self.gate.set)
        self._release_timer.daemon = True
        self._release_timer.start()

    def load(self):
        self.gate.wait(timeout=WAIT_SECONDS)
        super().load()
        self.load_finished.set()

    def close(self):
        self.closed_count += 1
        super().close()


def _gated_factory(created, auto_release=5.0):
    def factory(spec, settings):
        model = GatedModel(spec, settings, auto_release=auto_release)
        created.append(model)
        return model
    return factory


# ---------------------------------------------------------------------------
# Default blocking startup (existing behavior preserved)
# ---------------------------------------------------------------------------

def test_default_settings_block_until_loaded(client, application):
    assert Settings(_env_file=None).background_model_load is False
    assert application.state.load_tracker.snapshot() == {"sentiment": "ready"}
    assert application.state.load_tracker.task is None
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_background_model_load_env_flag(monkeypatch):
    monkeypatch.setenv("BACKGROUND_MODEL_LOAD", "true")
    assert Settings(_env_file=None).background_model_load is True
    monkeypatch.setenv("BACKGROUND_MODEL_LOAD", "false")
    assert Settings(_env_file=None).background_model_load is False


# ---------------------------------------------------------------------------
# Optional background loading
# ---------------------------------------------------------------------------

def test_background_load_readiness_progression():
    created = []
    application = create_app(
        Settings(_env_file=None, background_model_load=True),
        model_factory=_gated_factory(created, auto_release=WAIT_SECONDS),
    )
    with TestClient(application) as client:
        # Startup returned before the model finished loading.
        assert client.get("/health/live").json() == {"status": "alive"}
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/models").json()[0]["ready"] is False
        response = client.post(
            "/api/inference", json={"model": "sentiment", "input": "hello"}
        )
        assert response.status_code == 503

        created[0].gate.set()
        assert _wait_until(
            lambda: client.get("/health/ready").status_code == 200
        )
        assert client.get("/health/ready").json() == {"status": "ready"}
        assert client.get("/api/models/sentiment").json()["ready"] is True
        response = client.post(
            "/api/inference", json={"model": "sentiment", "input": "hello"}
        )
        assert response.status_code == 200
    assert created[0].closed_count == 1
    assert application.state.models == {}


def test_background_load_failure_fails_readiness_not_liveness():
    class BrokenModel(TestModel):
        def load(self):
            raise RuntimeError("private startup detail")

    application = create_app(
        Settings(_env_file=None, background_model_load=True),
        model_factory=BrokenModel,
    )
    with TestClient(application) as client:
        assert _wait_until(
            lambda: application.state.load_tracker.snapshot()["sentiment"] == "failed"
        )
        assert client.get("/health/live").status_code == 200
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert "private startup detail" not in ready.text
        assert client.get("/api/models/sentiment").json()["ready"] is False
        response = client.post(
            "/api/inference", json={"model": "sentiment", "input": "hello"}
        )
        assert response.status_code == 503
    # Shutdown after failed initialization stays clean.
    assert application.state.models == {}


def test_shutdown_drains_in_flight_background_load():
    """Shutdown during a load within the drain window: the worker cleans up."""
    created = []
    application = create_app(
        Settings(_env_file=None, background_model_load=True),
        model_factory=_gated_factory(created, auto_release=0.3),
    )
    with TestClient(application):
        pass  # exit immediately while the worker is still inside load()

    tracker = application.state.load_tracker
    assert tracker.shutdown_started is True
    assert application.state.models == {}
    assert _wait_until(lambda: created[0].load_finished.is_set())
    assert _wait_until(lambda: tracker.task.done())
    assert not tracker.task.cancelled()  # drained to completion, not cancelled
    assert created[0].closed_count == 1  # exactly-once close, worker-owned
    assert tracker.snapshot()["sentiment"] != "ready"


def test_background_load_cancelled_after_drain_timeout(monkeypatch):
    """A load exceeding the drain window is cancelled; cleanup stays safe."""
    monkeypatch.setattr(main_module, "_LOAD_DRAIN_SECONDS", 0.05)
    created = []
    application = create_app(
        Settings(_env_file=None, background_model_load=True),
        model_factory=_gated_factory(created, auto_release=0.3),
    )
    with TestClient(application):
        pass  # shutdown begins while the worker is blocked in load()

    tracker = application.state.load_tracker
    assert tracker.shutdown_started is True
    assert application.state.models == {}
    assert _wait_until(lambda: created[0].load_finished.is_set())
    assert _wait_until(lambda: created[0].closed_count >= 1)
    assert tracker.task.done()
    assert tracker.task.cancelled()
    # The worker still closed the model after its native load completed.
    assert created[0].closed_count == 1
    assert application.state.models == {}


def test_cancelled_slow_load_that_then_fails_is_closed_by_worker(monkeypatch):
    """Cancelled task + load that later raises: the worker closes the model.

    The cancelled task has no outer handler left, so worker-owned failure
    cleanup is the only thing that can release the partial model.
    """
    monkeypatch.setattr(main_module, "_LOAD_DRAIN_SECONDS", 0.05)
    created = []

    class GatedFailingModel(TestModel):
        def __init__(self, spec, settings):
            super().__init__(spec, settings)
            self.gate = threading.Event()
            self.closed_count = 0
            self.load_finished = threading.Event()
            self._release_timer = threading.Timer(0.3, self.gate.set)
            self._release_timer.daemon = True
            self._release_timer.start()

        def load(self):
            self.gate.wait(timeout=WAIT_SECONDS)
            self.load_finished.set()
            raise RuntimeError("load failed after long wait")

        def close(self):
            self.closed_count += 1
            super().close()

    def factory(spec, settings):
        model = GatedFailingModel(spec, settings)
        created.append(model)
        return model

    application = create_app(
        Settings(_env_file=None, background_model_load=True), model_factory=factory,
    )
    with TestClient(application):
        pass  # shutdown cancels the task while the load is still blocked

    tracker = application.state.load_tracker
    assert tracker.shutdown_started is True
    assert _wait_until(lambda: created[0].load_finished.is_set())
    assert _wait_until(lambda: created[0].closed_count >= 1)
    assert created[0].closed_count == 1  # exactly-once close of the partial model
    assert application.state.models == {}
    assert tracker.snapshot()["sentiment"] != "ready"  # readiness is never fabricated


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

def test_max_request_bytes_bounds():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_request_bytes=1023)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_request_bytes=10_485_761)
    settings = Settings(_env_file=None)
    assert settings.max_request_bytes == 65536
