"""Run runtime acceptance checks against a live HTTP server (standard library only).

The configured input limit is discovered from model metadata instead of being
hardcoded. If the server protects /api/* with an API key, pass it with
--api-key or the API_KEY environment variable; the key is never printed.
"""
import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None,
                        help="Bearer API key for protected endpoints. Prefer the "
                             "API_KEY environment variable. The value is never printed.")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    # The server strips whitespace from its configured key; match that here.
    supplied_key = (args.api_key if args.api_key is not None
                    else os.environ.get("API_KEY", "")).strip()
    auth_header = {"Authorization": f"Bearer {supplied_key}"} if supplied_key else {}
    if auth_header:
        print("Auth: enabled (key supplied via argument or API_KEY env; not shown)")
    else:
        print("Auth: no key supplied; expecting local-only mode")

    def call(path, body=None, headers=None):
        """JSON request; returns (status, parsed body)."""
        data = json.dumps(body).encode() if body is not None else None
        merged = dict(headers or {})
        if data:
            merged["Content-Type"] = "application/json"
        request = Request(base + path, data=data, headers=merged)
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError):
                payload = {}
            return exc.code, payload

    def raw_call(path, headers=None):
        """Request without JSON parsing; returns (status, bytes)."""
        request = Request(base + path, headers=dict(headers or {}))
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()

    try:
        for path in ("/health/live", "/health/ready"):
            status, _ = call(path)
            if status != 200:
                raise RuntimeError(f"{path}: expected 200, got {status}")
            print(f"PASS: {path}")

        status, payload = call("/api/models", headers=auth_header)
        if status == 401 and not auth_header:
            raise RuntimeError(
                "/api/models requires authentication. Supply the server API key "
                "with --api-key or the API_KEY environment variable."
            )
        if status != 200:
            raise RuntimeError(f"/api/models: expected 200, got {status}")
        entry = next((m for m in payload if m.get("id") == "sentiment"), None)
        if entry is None:
            raise RuntimeError("/api/models: sentiment model missing from registry")
        if not entry.get("ready"):
            raise RuntimeError("/api/models: sentiment model is not ready")
        print("PASS: /api/models")

        if auth_header:
            unauthenticated_status, _ = call("/api/models")
            if unauthenticated_status != 401:
                raise RuntimeError(
                    "Server accepted an unauthenticated /api/models request "
                    f"(status {unauthenticated_status}) although an API key was "
                    "supplied. Check the server API_KEY configuration."
                )
            print("PASS: /api/models rejects unauthenticated access")

        # The configured character limit comes from metadata, never a hardcoded number.
        limit = int(entry["max_input_chars"])
        if limit < 1:
            raise RuntimeError("Metadata max_input_chars must be positive")
        status, details = call("/api/models/sentiment", headers=auth_header)
        if status != 200 or details.get("max_input_chars") != limit:
            raise RuntimeError("/api/models/sentiment metadata mismatch")
        print(f"PASS: /api/models/sentiment (configured input limit {limit})")

        for text, expected in (
            ("I love this product. It is absolutely wonderful!", "positive"),
            ("I hate this product. It is terrible and disappointing.", "negative"),
        ):
            status, body = call("/api/inference",
                                {"model": "sentiment", "input": text},
                                headers=auth_header)
            if status != 200 or body.get("prediction") != expected:
                raise RuntimeError(f"Real {expected} prediction failed: {status}")
            if not (0 <= body["confidence"] <= 1 and body["processing_time_ms"] > 0):
                raise RuntimeError("Invalid prediction score or measured timing")
            print(f"PASS: real {expected} prediction")
            print(json.dumps(body))

        oversized = "x" * (limit + 1)
        for request_body, expected in (
            ({"model": "unknown", "input": "hello"}, 404),
            ({"model": "sentiment", "input": ""}, 422),
            ({"model": "sentiment", "input": " \t"}, 422),
            ({"model": "sentiment"}, 422),
            ({"model": "sentiment", "input": oversized}, 422),
        ):
            status, payload = call("/api/inference", request_body, headers=auth_header)
            if status != expected:
                raise RuntimeError(f"Validation: expected {expected}, got {status}")
            if request_body.get("input") == oversized and oversized in json.dumps(payload):
                raise RuntimeError("Error response echoed the rejected input")
            print(f"PASS: error status {expected}")

        status, metrics_body = raw_call("/metrics", headers=auth_header)
        if status == 401 and not auth_header:
            print("WARN: /metrics requires authentication; supply --api-key or "
                  "API_KEY to verify the exposition.")
        elif status != 200:
            raise RuntimeError(f"/metrics: expected 200, got {status}")
        else:
            exposition = metrics_body.decode("utf-8", "replace")
            for name in ("http_requests_total", "http_request_duration_seconds"):
                if name not in exposition:
                    raise RuntimeError(f"/metrics is missing {name}")
            print("PASS: /metrics exposition")

        print("PASS: runtime live HTTP acceptance checks.")
        return 0
    except (URLError, OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"FAIL: {exc}. Check server startup and docs/RUNTIME.md.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
