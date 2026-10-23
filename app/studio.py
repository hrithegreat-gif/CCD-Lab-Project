"""Public UI with allowlisted assets, safe configuration and strict security headers."""
from pathlib import Path
from typing import Mapping

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import MutableHeaders

from app.config import Settings

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Exactly these files are served, each with a fixed media type and cache
# policy. index.html is additionally exposed at / and /studio/.
_NO_STORE = "no-store"
_NO_CACHE = "no-cache"
ASSET_ALLOWLIST: Mapping[str, tuple[str, str]] = {
    "index.html": ("text/html; charset=utf-8", _NO_STORE),
    "studio.css": ("text/css; charset=utf-8", _NO_CACHE),
    "studio-core.mjs": ("text/javascript; charset=utf-8", _NO_CACHE),
    "studio.mjs": ("text/javascript; charset=utf-8", _NO_CACHE),
    "favicon.svg": ("image/svg+xml", _NO_CACHE),
}

# Required UI policy for every studio-served response (HTML, config, assets).
UI_CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "media-src 'self' blob:",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
])

def is_studio_path(path: object) -> bool:
    """True for the UI surface only: /, /studio, /studio/ and /studio/*."""
    return isinstance(path, str) and (
        path == "/" or path == "/studio" or path.startswith("/studio/")
    )


def _cache_control_for(path: str) -> str:
    """no-store for HTML/config responses; revalidate-only for assets."""
    if path.startswith("/studio/assets/"):
        return _NO_CACHE
    return _NO_STORE


class StudioSecurityHeadersMiddleware:
    """UI security headers for studio routes/assets/config only.

    Non-studio responses (API, metrics, docs, OpenAPI) are passed through
    untouched. Only response headers are modified; request bodies are never
    read or buffered and nothing is logged.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not is_studio_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = UI_CONTENT_SECURITY_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                headers["Cache-Control"] = _cache_control_for(scope.get("path", ""))
            await send(message)

        await self.app(scope, receive, with_headers)


def _serve_asset(base_dir: Path, name: str) -> Response:
    """Read one allowlisted asset; missing files degrade to a plain 404."""
    media_type, _ = ASSET_ALLOWLIST[name]  # callers guarantee membership
    try:
        resolved = (base_dir / name).resolve()
        if not resolved.is_relative_to(base_dir.resolve()):
            raise FileNotFoundError(name)  # defense in depth; never traverses
        data = resolved.read_bytes()
    except OSError:
        raise HTTPException(404, "Asset is not available") from None
    return Response(content=data, media_type=media_type)


def _ui_config(application: FastAPI, settings: Settings) -> dict[str, object]:
    """Safe UI config: booleans and integer limits only. No secrets or paths."""
    return {
        "title": application.title,
        "version": application.version,
        "auth_required": bool(settings.auth_enabled),
        "max_request_bytes": int(settings.max_request_bytes),
        "sentiment_max_input_chars": int(settings.max_input_chars),
    }


def install_studio(
    application: FastAPI,
    settings: Settings,
    *,
    static_dir: Path | str | None = None,
) -> None:
    """Register the public demo UI: static assets and safe UI config.

    Call once in ``create_app``. Raises
    RuntimeError on a second call instead of silently duplicating routes.
    Routes are excluded from OpenAPI so the API contract stays unchanged.
    """
    if getattr(application.state, "studio_installed", False):
        raise RuntimeError("install_studio must be called exactly once per application")
    application.state.studio_installed = True

    base_dir = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    application.add_middleware(StudioSecurityHeadersMiddleware)

    def serve_index() -> Response:
        return _serve_asset(base_dir, "index.html")

    @application.get("/", include_in_schema=False)
    def studio_root() -> Response:
        return serve_index()

    @application.get("/studio", include_in_schema=False)
    def studio_redirect() -> Response:
        return RedirectResponse("/studio/", status_code=307)

    @application.get("/studio/", include_in_schema=False)
    def studio_index() -> Response:
        return serve_index()

    @application.get("/studio/config", include_in_schema=False)
    def studio_config() -> dict[str, object]:
        return _ui_config(application, settings)

    @application.get("/studio/assets/{asset_name}", include_in_schema=False)
    def studio_asset(asset_name: str) -> Response:
        # Strict allowlist: any other name 404s before the filesystem is read.
        if asset_name not in ASSET_ALLOWLIST:
            raise HTTPException(404, "Unknown asset")
        return _serve_asset(base_dir, asset_name)

    return None
