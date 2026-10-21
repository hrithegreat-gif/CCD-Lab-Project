"""Browser acceptance against a running loopback demo.

The first context verifies REAL cached sentiment inference. Optional text/audio
and auth/error paths use explicitly intercepted HTTP fixtures: they do not load
optional weights, contact providers or establish real optional-model acceptance.
Requires playwright==1.58.0 and an installed Chromium/Chrome/Edge browser.
"""
from __future__ import annotations

import argparse
import io
import json
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.port is None
    ):
        raise ValueError("Browser checks require an explicit HTTP loopback server port")
    return value.rstrip("/")


def wait_ready(base_url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with opener.open(f"{base_url}/health/ready", timeout=2) as response:
                if json.load(response).get("status") == "ready":
                    return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("Real local sentiment model did not become ready within 90 seconds")


def wav_fixture() -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(struct.pack("<h", 0) * 800)
    return stream.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--browser-channel", choices=("chromium", "chrome", "msedge"), default="chromium")
    parser.add_argument("--artifacts", type=Path, default=Path("results/frontend"))
    args = parser.parse_args()
    base = validate_base_url(args.base_url)
    wait_ready(base)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    checks: list[str] = []
    errors: list[str] = []

    def passed(label: str) -> None:
        checks.append(label)
        print(f"PASS {label}", flush=True)

    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel=args.browser_channel if args.browser_channel != "chromium" else None, headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "Content Security Policy" in message.text else None)
        page.goto(base, wait_until="networkidle")
        expect(page.locator("#service-status-label")).to_have_text("API connected")
        expect(page.locator("#result-empty")).to_be_visible()
        expect(page.locator("#run-button")).to_be_disabled()
        passed("real page initializes without fabricated results")

        ids = page.locator("[id]").evaluate_all("nodes => nodes.map(n => n.id)")
        assert len(ids) == len(set(ids)), "Duplicate HTML ids"
        assert not page.locator("[onclick], [onload], [style]").count(), "Inline code/style violates the studio CSP"
        passed("unique DOM IDs and no inline event handlers/styles")

        page.locator("#examples button").nth(0).click()
        page.locator("#run-button").click()
        expect(page.locator("#result-value")).to_have_text("Positive sentiment", timeout=30000)
        data = json.loads(page.locator("#result-json").text_content())
        assert data["status"] == "success" and data["prediction"] == "positive" and 0.5 < data["confidence"] <= 1
        expect(page.locator("#session-count")).to_have_text("1")
        passed("REAL ONNX positive inference, confidence and session telemetry")
        expect(page.locator("#toast")).to_be_hidden(timeout=5000)
        page.screenshot(path=str(args.artifacts / "demo-desktop.png"), full_page=True)

        page.locator("#examples button").nth(1).click()
        page.locator("#input-text").press("Control+Enter")
        expect(page.locator("#result-value")).to_have_text("Negative sentiment", timeout=30000)
        expect(page.locator("#session-count")).to_have_text("2")
        passed("REAL ONNX negative inference through the keyboard shortcut")
        page.locator('[data-result-view="json"]').click()
        expect(page.locator("#result-json")).to_be_visible()
        with page.expect_download() as download_info:
            page.locator("#download-result").click()
        assert download_info.value.suggested_filename == "inference-studio-result.json"
        passed("JSON view and downloadable real response")

        page.locator("#input-text").fill("good " * 350)
        page.locator("#run-button").click()
        expect(page.locator("#session-count")).to_have_text("3", timeout=30000)
        expect(page.locator("#truncated-notice")).to_be_visible()
        passed("REAL token truncation disclosure")
        page.locator("#input-text").fill("a" * 2001)
        expect(page.locator("#run-button")).to_be_disabled()
        page.locator("#input-text").fill("   ")
        expect(page.locator("#run-button")).to_be_disabled()
        passed("blank and oversized text cannot be submitted")

        page.locator('[data-task="generate"]').click()
        expect(page.locator("#model-state")).to_have_text("Not enabled")
        expect(page.locator("#run-button")).to_be_disabled()
        page.locator('[data-task="speech"]').click()
        expect(page.locator("#run-button")).to_be_disabled()
        page.locator('[data-task="transcribe"]').click()
        expect(page.locator("#audio-input-group")).to_be_visible()
        expect(page.locator("#run-button")).to_be_disabled()
        passed("real disabled optional adapters never trigger inference")
        page.locator("#nav-models").click()
        expect(page.locator("#catalog-grid .catalog-card")).to_have_count(5)
        page.locator("#catalog-grid .catalog-open").first.click()
        expect(page.locator("#view-playground")).to_be_visible()
        page.locator("#nav-runs").click()
        expect(page.locator("#runs-list .run-item")).to_have_count(3)
        page.locator("#runs-list .run-open").first.click()
        expect(page.locator("#view-playground")).to_be_visible()
        passed("model catalog and session history navigation are functional")
        page.locator('#task-sentiment').focus()
        page.keyboard.press('ArrowRight')
        expect(page.locator('#task-generate')).to_have_attribute('aria-selected', 'true')
        page.keyboard.press('Home')
        expect(page.locator('#task-sentiment')).to_have_attribute('aria-selected', 'true')
        expect(page.locator('#run-form')).to_have_attribute('aria-labelledby', 'task-sentiment')
        page.locator('#result-tab-overview').focus()
        page.keyboard.press('End')
        expect(page.locator('#result-json')).to_be_visible()
        page.keyboard.press('Home')
        expect(page.locator('#result-overview')).to_be_visible()
        passed("keyboard-operated task/result tabs and labelled panels")

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#examples button").nth(0).click()
        page.locator("#run-button").click()
        expect(page.locator("#result-value")).to_have_text("Positive sentiment", timeout=30000)
        expect(page.locator("#session-count")).to_have_text("4", timeout=30000)
        expect(page.locator("#run-button")).to_be_enabled()
        expect(page.locator("#toast")).to_be_hidden(timeout=5000)
        page.screenshot(path=str(args.artifacts / "demo-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "Mobile horizontal overflow"
        passed("390px mobile layout and REAL sentiment submission")
        page.locator("#nav-runs").click()
        page.locator("#clear-runs").click()
        expect(page.locator("#runs-empty")).to_be_visible()
        expect(page.locator("#session-count")).to_have_text("0")
        page.reload(wait_until="networkidle")
        expect(page.locator("#session-count")).to_have_text("0")
        passed("clear and reload erase in-memory session history")
        context.close()

        # Everything below is an explicitly mocked browser/API-contract test.
        mock = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        mock_page = mock.new_page()
        mock_page.on("pageerror", lambda error: errors.append(str(error)))
        captures: list[dict] = []
        pending_routes = []
        mode = {"status": 200, "slow": False}
        caps = {"capabilities": {name: {"state": "ready", "voices": ["Bella", "Jasper"]} for name in (
            "qwen-generation", "groq-text", "kitten-speech", "groq-transcription"
        )}}
        key = "browser-fixture-tab-only-key"
        config = {
            "auth_required": True, "max_request_bytes": 65536, "sentiment_max_input_chars": 2000,
            "generation_max_input_chars": 2000, "generation_max_output_tokens": 256,
            "speech_max_input_chars": 1000, "transcription_max_bytes": 10485760,
        }
        baseline = [{"id": "sentiment", "ready": True, "max_input_chars": 2000, "revision": "fixture"}]
        audio = wav_fixture()

        def route_fixture(route) -> None:
            request = route.request
            path = urllib.parse.urlsplit(request.url).path
            if path == "/studio/config":
                route.fulfill(json=config)
                return
            if not path.startswith("/api/"):
                route.continue_()
                return
            if request.headers.get("authorization") != f"Bearer {key}":
                route.fulfill(status=401, json={"detail": "Unauthorized"})
                return
            if path == "/api/models":
                route.fulfill(json=baseline)
                return
            if path == "/api/capabilities":
                route.fulfill(json=caps)
                return
            capture = {"path": path, "headers": request.headers, "bytes": request.post_data_buffer}
            captures.append(capture)
            if mode["status"] != 200:
                route.fulfill(status=mode["status"], json={"detail": "private input and exception must not appear"})
                return
            if mode["slow"]:
                # Retain the route to complete it explicitly after UI cancellation.
                pending_routes.append(route)
                return
            if path == "/api/generate":
                payload = request.post_data_json
                assert set(payload) == {"model", "input", "max_output_tokens"}
                route.fulfill(json={"status": "success", "model": payload["model"], "provider": "groq" if payload["model"].startswith("groq-") else "local",
                    "output": '<img src=x onerror="window.__injected=true">\nA fixture response, not real optional inference.',
                    "input_tokens": 10, "output_tokens": 18, "truncated_input": False, "processing_time_ms": 12.5})
            elif path == "/api/speech":
                assert set(request.post_data_json) <= {"input", "voice"}
                route.fulfill(body=audio, content_type="audio/wav", headers={"X-Sample-Rate": "8000", "X-Processing-Time-Ms": "6.8"})
            elif path == "/api/transcriptions":
                assert request.post_data_buffer == audio, "Upload must be raw audio, not multipart"
                assert request.headers["content-type"] == "audio/wav"
                route.fulfill(json={"status": "success", "model": "whisper-large-v3-turbo", "text": "A mocked transcription for client-contract verification.", "duration_seconds": 0.1, "processing_time_ms": 9.0})
            else:
                route.fulfill(json={"status": "success", "model": "sentiment", "prediction": "positive", "confidence": 0.95,
                    "processing_time_ms": 5.2, "tokens_processed": 8, "truncated": False})

        mock.route("**/*", route_fixture)
        mock_page.goto(base, wait_until="networkidle")
        expect(mock_page.locator("#service-status-label")).to_have_text("Session access needed")
        mock_page.locator("#examples button").first.click()
        expect(mock_page.locator("#run-button")).to_be_disabled()
        mock_page.locator("#auth-open").click()
        mock_page.locator("#api-key").fill("incorrect-fixture-key")
        mock_page.locator("#auth-save").click()
        expect(mock_page.locator("#auth-error")).to_be_visible()
        mock_page.locator("#api-key").fill(key)
        mock_page.locator("#auth-save").click()
        expect(mock_page.locator("#auth-dialog")).not_to_be_visible()
        expect(mock_page.locator("#auth-state")).to_have_text("Authenticated")
        assert mock_page.evaluate("localStorage.length === 0 && sessionStorage.length === 0")
        assert key not in mock_page.content()
        passed("MOCK auth: rejected key, successful key and no DOM/storage leakage")

        mock_page.locator('[data-task="generate"]').click()
        mock_page.locator("#examples button").first.click()
        mock_page.locator("#run-button").click()
        expect(mock_page.locator("#result-text")).to_contain_text("A fixture response")
        assert mock_page.locator('img[src="x"]').count() == 0
        assert mock_page.evaluate("window.__injected !== true")
        passed("MOCK local generation schema and output XSS resistance")
        mock_page.locator("#model-select").select_option("groq-gpt-oss-120b")
        expect(mock_page.locator("#run-button")).to_be_disabled()
        mock_page.locator("#provider-consent").check()
        mock_page.locator("#run-button").click()
        expect(mock_page.locator("#session-count")).to_have_text("2")
        passed("MOCK hosted generation requires explicit per-model consent")
        mock_page.locator('[data-task="speech"]').click()
        mock_page.locator("#voice-select").select_option("Bella")
        mock_page.locator("#run-button").click()
        expect(mock_page.locator("#audio-result")).to_be_visible()
        expect(mock_page.locator("#metric-latency")).to_have_text("6.80 ms")
        with mock_page.expect_download() as speech_download:
            mock_page.locator("#audio-download").click()
        assert speech_download.value.suggested_filename == "inference-studio-speech.wav"
        passed("MOCK speech voice, WAV playback surface and audio download")
        mock_page.locator('[data-task="transcribe"]').click()
        mock_page.locator("#audio-file").set_input_files({"name": "browser-fixture.wav", "mimeType": "audio/wav", "buffer": audio})
        expect(mock_page.locator("#run-button")).to_be_disabled()
        mock_page.locator("#provider-consent").check()
        mock_page.locator("#run-button").click()
        expect(mock_page.locator("#result-text")).to_have_text("A mocked transcription for client-contract verification.")
        passed("MOCK transcription sends exact raw bytes and supported Content-Type")
        mock_page.locator("#audio-file").set_input_files({"name": "too-large.wav", "mimeType": "audio/wav", "buffer": b"0" * 65537})
        expect(mock_page.locator("#run-button")).to_be_disabled()
        passed("MOCK audio respects the smaller global request-byte limit")

        mock_page.locator('[data-task="sentiment"]').click()
        mock_page.locator("#examples button").first.click()
        for status in (429, 503, 504):
            mode["status"] = status
            mock_page.locator("#run-button").click()
            expect(mock_page.locator("#error-banner")).to_be_visible()
            expect(mock_page.locator("#run-button")).to_be_enabled()
            assert "private input" not in mock_page.locator("#error-message").text_content()
        passed("MOCK 429/503/504 errors are safe and never add successful runs")
        assert mock_page.locator("#session-count").text_content() == "4"
        mode["status"] = 200
        mode["slow"] = True
        mock_page.locator("#run-button").click()
        expect(mock_page.locator("#cancel-run")).to_be_visible()
        mock_page.locator("#cancel-run").click()
        for pending in pending_routes:
            pending.abort("aborted")
        pending_routes.clear()
        expect(mock_page.locator("#error-message")).to_contain_text("Server work may continue")
        expect(mock_page.locator("#run-button")).to_be_enabled()
        passed("MOCK cancellation accurately stops browser waiting, not server work")
        mock_page.locator("#auth-open").click()
        mock_page.locator("#auth-clear").click()
        expect(mock_page.locator("#run-button")).to_be_disabled()
        mock_page.reload(wait_until="networkidle")
        expect(mock_page.locator("#service-status-label")).to_have_text("Session access needed")
        expect(mock_page.locator("#session-count")).to_have_text("0")
        passed("MOCK clearing/reloading forgets session credentials and runs")
        mock.unroute_all(behavior="wait")
        mock.close()
        browser.close()

    if errors:
        raise AssertionError("Browser errors: " + "; ".join(errors))
    passed("no JavaScript runtime errors or CSP violations")
    report = {"passed": len(checks), "checks": checks, "real_model_scope": "cached local DistilBERT positive, negative, truncation and mobile inference",
        "mocked_scope": "optional generation/speech/transcription, auth, capacity/deadline/cancel; NOT real optional-provider acceptance",
        "base_url": base, "browser_channel": args.browser_channel}
    (args.artifacts / "browser-checks.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(checks)} browser checks passed. Optional/provider cases are HTTP fixtures; no paid calls.", flush=True)


if __name__ == "__main__":
    main()
