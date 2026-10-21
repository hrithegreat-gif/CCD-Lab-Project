"""Structured events intentionally exclude user text and exception messages.

Prometheus metrics use only low-cardinality labels: HTTP method, a normalized
route template and the numeric status. Client input, API keys, model IDs and
raw request paths never become labels.
"""
import json
import logging

from prometheus_client import REGISTRY, Counter, Histogram

logger = logging.getLogger("ai_serving")

REQUEST_COUNT = Counter(
    "http_requests",
    "HTTP requests processed, by method, route template and status.",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, by method and route template.",
    ["method", "endpoint"],
    buckets=(
        0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75,
        1.0, 2.5, 5.0, 7.5, 10.0, 15.0,
    ),
)

# Paths that may be labeled verbatim: fixed strings owned by this application,
# plus the contract route templates of the optional extensions.
_KNOWN_PATHS = frozenset({
    "/health/live",
    "/health/ready",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/api/models",
    "/api/inference",
    "/api/generate",
    "/api/speech",
    "/api/transcriptions",
    "/api/capabilities",
})
_MODEL_DETAILS_PREFIX = "/api/models/"
_UNMATCHED = "unmatched"

# scope["method"] is client-controlled in raw ASGI; only known methods may
# become labels. Anything else collapses to "OTHER".
_KNOWN_METHODS = frozenset({
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE",
})


def normalize_method(method: object) -> str:
    """Bound the HTTP method label to known methods; unknown ones become OTHER."""
    return method if isinstance(method, str) and method in _KNOWN_METHODS else "OTHER"


def normalize_endpoint(scope: dict) -> str:
    """Return a bounded-cardinality label for the request target.

    Prefers the matched route template (e.g. /api/models/{model_id}). Without a
    matched route (404s, auth rejections before routing) only fixed allowlisted
    paths are echoed; every other client-supplied path collapses to "unmatched".
    """
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path.startswith("/"):
        return route_path
    path = scope.get("path", "")
    if not isinstance(path, str):
        return _UNMATCHED
    if path in _KNOWN_PATHS:
        return path
    # Mirror the router's {model_id} convertor ([^/]+): only a single
    # non-empty segment after the prefix is the parameterized route.
    if path.startswith(_MODEL_DETAILS_PREFIX):
        remainder = path[len(_MODEL_DETAILS_PREFIX):]
        if remainder and "/" not in remainder:
            return "/api/models/{model_id}"
    return _UNMATCHED


def configure_logging(level: str) -> None:
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(level)
    logger.propagate = False


def event(name: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, json.dumps({"event": name, **fields}, separators=(",", ":")))


__all__ = [
    "REGISTRY",
    "REQUEST_COUNT",
    "REQUEST_LATENCY",
    "configure_logging",
    "event",
    "normalize_endpoint",
    "normalize_method",
]
