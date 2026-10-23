"""FastAPI model serving with authenticated, bounded requests and lifecycle probes."""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Callable

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client import REGISTRY as PROMETHEUS_REGISTRY
from starlette.datastructures import Headers

from app.config import Settings
from app.inference import InferenceTimeout, YoloDetector
from app.model_registry import REGISTRY, ModelSpec, metadata, ACCEPTED_CONTENT_TYPES
from app.observability import (
    REQUEST_COUNT,
    REQUEST_LATENCY,
    configure_logging,
    event,
    normalize_endpoint,
    normalize_method,
)
from app.schemas import ErrorResponse, DetectResponse, ModelMetadata
from app.studio import install_studio
from app.imaging import decode_image, ImageRejected

# Grace period for an in-flight native model load during shutdown. Native
# threads cannot be force-killed, so shutdown first drains, then cancels.
_LOAD_DRAIN_SECONDS = 5.0


class ModelLoadTracker:
    """Per-model load state shared between startup, probes and shutdown."""

    def __init__(self, model_ids) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, str] = dict.fromkeys(model_ids, "pending")
        self.task: asyncio.Task | None = None
        self.shutdown_started = False

    def set(self, model_id: str, state: str) -> None:
        with self._lock:
            self._states[model_id] = state

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._states)

    def all_ready(self) -> bool:
        return all(state == "ready" for state in self.snapshot().values())


class RequestBodyTooLarge(HTTPException):
    """Raised from the body stream when the byte cap is exceeded.

    Subclassing HTTPException is essential: FastAPI's body parsing converts
    every other exception raised while reading the body into a generic 400,
    but re-raises HTTPException untouched, so the cap always produces 413.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=413,
            detail="Request body too large",
            headers={"Connection": "close"},
        )


class BodyLimitMiddleware:
    """Byte-level body cap enforced before and during the body read.

    A declared Content-Length above the limit is rejected before any byte is
    read. The actual number of received bytes is counted regardless of the
    header, so absent, chunked or lying Content-Length values cannot smuggle an
    unbounded body. Nothing beyond the cap is ever buffered.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    event("request_body_rejected", logging.WARNING, reason="declared")
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                # Malformed header: fall through to actual byte counting.
                pass
        received = 0
        response_started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except RequestBodyTooLarge:
            # Belt-and-braces for consumers below that do not map
            # HTTPException themselves; FastAPI routes produce the 413 via
            # the exception middleware before this point.
            event("request_body_rejected", logging.WARNING, reason="streamed")
            if not response_started:
                await self._reject(scope, receive, send)
            # If a response already started (streaming responses), the server
            # closes the connection; nothing else can be sent.

    async def _reject(self, scope, receive, send) -> None:
        response = JSONResponse(
            {"detail": "Request body too large"},
            status_code=413,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


def _bearer_token(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class BearerAuthMiddleware:
    """Constant-time Bearer authentication for /api/* and /metrics.

    With an empty configured API key the service runs in local-only mode and
    every request passes. With a nonempty key, requests are rejected before any
    body byte is read or route is resolved, so malformed or oversized bodies
    cannot bypass authentication.
    """

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.settings.auth_enabled:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path != "/metrics" and not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return
        token = _bearer_token(Headers(scope=scope).get("authorization"))
        if token is None or not self.settings.bearer_matches(token):
            # One generic rejection for missing, malformed and wrong tokens.
            event("auth_rejected", logging.WARNING)
            response = JSONResponse(
                {"detail": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RequestMetricsMiddleware:
    """Prometheus counters and histograms for every HTTP response."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = normalize_method(scope.get("method"))
        started = perf_counter()
        recorded = False

        async def metrics_send(message):
            nonlocal recorded
            if message["type"] == "http.response.start":
                recorded = True
                self._record(scope, method, str(message["status"]), perf_counter() - started)
            await send(message)

        try:
            await self.app(scope, receive, metrics_send)
        except Exception:
            # ServerErrorMiddleware sits outside and will answer 500; the
            # failure is still measured here.
            if not recorded:
                self._record(scope, method, "500", perf_counter() - started)
            raise

    @staticmethod
    def _record(scope, method: str, status: str, duration: float) -> None:
        endpoint = normalize_endpoint(scope)
        REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status).inc()
        REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)


def _expose_bearer_scheme(application: FastAPI, settings: Settings) -> None:
    """Mark protected operations in OpenAPI so Swagger UI offers the key field."""
    original_openapi = application.openapi

    def openapi() -> dict:
        schema = original_openapi()
        if schema is not None and settings.auth_enabled:
            components = schema.setdefault("components", {})
            components.setdefault("securitySchemes", {})["bearerAuth"] = {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "Server API key issued out of band. "
                    "Send `Authorization: Bearer <key>`."
                ),
            }
            requirement = [{"bearerAuth": []}]
            for path, operations in schema.get("paths", {}).items():
                if path == "/metrics" or path.startswith("/api/"):
                    for operation in operations.values():
                        if isinstance(operation, dict):
                            operation["security"] = requirement
        return schema

    application.openapi = openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    model_factory: Callable[[ModelSpec, Settings], YoloDetector] = YoloDetector,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        tracker: ModelLoadTracker = application.state.load_tracker

        async def load_models() -> None:
            for model_id, spec in REGISTRY.items():
                if tracker.shutdown_started:
                    return
                tracker.set(model_id, "loading")
                model = None
                worker_closed = threading.Event()

                try:
                    model = model_factory(spec, settings)

                    def _load_in_worker(model=model, worker_closed=worker_closed):
                        """Owns the native load; runs in the worker thread.

                        Cancelling the awaiting task does NOT stop this thread
                        (native loads cannot be force-killed), so post-load
                        cleanup happens HERE whenever shutdown was requested,
                        never inside the cancelled task. The event records the
                        decision so the task never closes twice. Defaults bind
                        model at definition time, which is after the factory
                        call above.
                        """
                        try:
                            if tracker.shutdown_started:
                                return
                            model.load()
                        except BaseException:
                            # A cancelled task has no outer handler left, and a
                            # drain-waiting one would race this thread; the
                            # worker closes the partial model itself.
                            if tracker.shutdown_started:
                                model.close()
                                worker_closed.set()
                            raise
                        if tracker.shutdown_started:
                            model.close()
                            worker_closed.set()

                    await asyncio.to_thread(_load_in_worker)
                except asyncio.CancelledError:
                    # The worker thread may still be running its native load.
                    # It performs its own cleanup via the shutdown flag; the
                    # cancelled task must not close or register anything.
                    raise
                except Exception as exc:
                    # The worker finished (the failure propagated), so closing
                    # the partially loaded model is safe. Liveness stays up,
                    # readiness fails, and inference is never faked.
                    if model is not None and not worker_closed.is_set():
                        model.close()
                    tracker.set(model_id, "failed")
                    event("model_load_failed", logging.ERROR, model=model_id,
                          error_type=type(exc).__name__)
                    continue
                # The worker thread has finished; its cleanup decision is final.
                if tracker.shutdown_started:
                    if not worker_closed.is_set():
                        model.close()  # flag flipped after the worker's checks
                    return
                application.state.models[model_id] = model
                tracker.set(model_id, "ready")
                event("model_ready", model=model_id, revision=spec.revision)

        try:
            if settings.background_model_load:
                tracker.task = asyncio.create_task(load_models())
            else:
                await load_models()
            yield
        finally:
            tracker.shutdown_started = True
            task = tracker.task
            if task is not None and not task.done():
                # Drain first so a mid-flight native load completes and the
                # task itself performs its cleanup; native threads are never
                # cancelled mid-run.
                await asyncio.wait({task}, timeout=_LOAD_DRAIN_SECONDS)
                if not task.done():
                    event("model_load_cancelled", logging.WARNING)
                    task.cancel()
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    event("model_load_task_failed", logging.ERROR)
            for model in application.state.models.values():
                model.close()
            application.state.models.clear()

    application = FastAPI(
        title="AI Model Serving Platform", version="0.2.0", lifespan=lifespan,
        description="CPU object detection with bounded uploads and inference.",
    )
    application.state.models = {}
    application.state.settings = settings
    application.state.slots = threading.BoundedSemaphore(settings.max_concurrent_inferences)
    application.state.load_tracker = ModelLoadTracker(REGISTRY)

    # Execution order: RequestMetrics (outermost), BearerAuth, BodyLimit, app.
    application.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes)
    application.add_middleware(BearerAuthMiddleware, settings=settings)
    application.add_middleware(RequestMetricsMiddleware)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        # FastAPI's default errors echo input. Exclude input and validator context.
        errors = [
            {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.get("/health/live", tags=["health"])
    def live():
        return {"status": "alive"}

    @application.get("/health/ready", tags=["health"],
                     responses={503: {"model": ErrorResponse}})
    def ready():
        states = application.state.load_tracker.snapshot()
        if not all(
            states.get(model_id) == "ready"
            and application.state.models.get(model_id) is not None
            and application.state.models[model_id].ready
            for model_id in REGISTRY
        ):
            raise HTTPException(503, "Model is not ready")
        return {"status": "ready"}

    @application.get("/metrics", tags=["observability"])
    def metrics():
        return Response(
            content=generate_latest(PROMETHEUS_REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )

    @application.get("/api/models", response_model=list[ModelMetadata], tags=["models"])
    def models():
        return [
            metadata(spec, settings, bool(application.state.models.get(model_id)
                     and application.state.models[model_id].ready))
            for model_id, spec in REGISTRY.items()
        ]

    @application.get("/api/models/{model_id}", response_model=ModelMetadata,
                     tags=["models"], responses={404: {"model": ErrorResponse}})
    def model_details(model_id: str):
        if model_id not in REGISTRY:
            raise HTTPException(404, "Unknown model")
        model = application.state.models.get(model_id)
        return metadata(REGISTRY[model_id], settings, bool(model and model.ready))

    @application.post('/api/detect', response_model=DetectResponse, tags=['detection'],
                      responses={code: {'model': ErrorResponse} for code in (413,415,422,429,500,503,504)})
    async def detect(request: Request,
                     conf: float = Query(default=settings.conf_threshold,ge=.05,le=.95,allow_inf_nan=False),
                     iou: float = Query(default=settings.iou_threshold,ge=.10,le=.95,allow_inf_nan=False),
                     max_detections: int = Query(default=settings.max_detections,ge=1,le=settings.max_detections)):
        started=perf_counter()
        code,count=500,0
        acquired=False
        transferred=False
        try:
            content_type=request.headers.get('content-type','').split(';',1)[0].strip().lower()
            if content_type not in ACCEPTED_CONTENT_TYPES:
                raise HTTPException(415,'Unsupported image type')
            model=application.state.models.get('yolov8n')
            if model is None or not model.ready:
                raise HTTPException(503,'Model is not ready')
            body=await request.body()
            if len(body)>settings.max_image_bytes:
                raise HTTPException(413,'Request body too large')
            if not body:
                raise HTTPException(422,'Invalid image')
            acquired=application.state.slots.acquire(blocking=False)
            if not acquired:
                raise HTTPException(429,'Inference capacity reached; retry later',headers={'Retry-After':'1'})
            def run():
                try:
                    decode_started=perf_counter()
                    with decode_image(body,content_type,settings) as image:
                        decode_ms=(perf_counter()-decode_started)*1000
                        result=model.predict(image,conf,iou,max_detections)
                        return DetectResponse(image={'width':image.width,'height':image.height},
                            count=len(result.detections),detections=result.detections,
                            parameters={'conf':conf,'iou':iou,'max_detections':max_detections},
                            processing_time_ms=(perf_counter()-started)*1000,
                            timings_ms={'decode':decode_ms,'inference':result.inference_ms,'postprocess':result.postprocess_ms})
                finally:
                    # Native work outlives cancellation: never release early.
                    application.state.slots.release()
            worker=asyncio.create_task(asyncio.to_thread(run))
            transferred=True
            def consume(task):
                if not task.cancelled(): task.exception()
            worker.add_done_callback(consume)
            response=await asyncio.shield(worker)
            code,count=200,response.count
            return response
        except ImageRejected as exc:
            code=exc.status_code
            raise HTTPException(code,exc.detail) from None
        except HTTPException as exc:
            code=exc.status_code
            raise
        except InferenceTimeout:
            code=504
            raise HTTPException(504,'Inference timed out') from None
        except asyncio.CancelledError:
            code=499
            raise
        except Exception as exc:
            event('detection_failed',logging.ERROR,error_type=type(exc).__name__)
            raise HTTPException(500,'Inference failed') from None
        finally:
            if acquired and not transferred: application.state.slots.release()
            event('detection',status_code=code,processing_time_ms=round((perf_counter()-started)*1000,3),count=count)

    _expose_bearer_scheme(application, settings)
    install_studio(application, settings)
    return application


app = create_app()
