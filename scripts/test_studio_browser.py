"""Real detection browser acceptance. Install playwright==1.58.0 separately."""
import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
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
    raise RuntimeError("Real local detection model did not become ready within 90 seconds")



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8003')
    parser.add_argument('--channel', default='msedge')
    parser.add_argument('--output', type=Path, default=Path('.validation/detection-browser'))
    args = parser.parse_args()
    base = validate_base_url(args.base_url)
    wait_ready(base)
    args.output.mkdir(parents=True, exist_ok=True)
    key = os.environ.get('API_KEY', '').strip()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=args.channel, headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base)
        if key:
            page.locator('#auth-open').click()
            page.locator('#api-key').fill(key)
            page.locator('#auth-save').click()
        expect(page.locator('#model-state')).to_have_text('Ready')
        for sample, label in [('cat', 'cat'), ('coffee', 'cup'), ('person', 'person')]:
            page.locator(f'[data-sample="{sample}"]').click()
            expect(page.locator('#run')).to_be_enabled()
            page.locator('#run').click()
            expect(page.locator('#message')).to_have_text('Detection complete.')
            expect(page.locator('#detections')).to_contain_text(label)
            assert page.locator('#overlay').evaluate('(c) => c.getContext("2d").getImageData(0,0,c.width,c.height).data.some(v => v !== 0)')
        page.screenshot(path=str(args.output / 'desktop.png'), full_page=True)
        with page.expect_download() as download:
            page.locator('#download').click()
        download.value.save_as(str(args.output / 'annotated.png'))
        assert (args.output / 'annotated.png').read_bytes().startswith(b'\x89PNG')
        page.locator('#json-tab').click()
        result = json.loads(page.locator('#result-json').inner_text())
        assert result['count'] > 0
        page.locator('#json-tab').press('ArrowLeft')
        expect(page.locator('#overview-tab')).to_be_focused()
        page.locator('#clear').click()
        expect(page.locator('#preview')).to_be_hidden()
        expect(page.locator('#download')).to_be_disabled()
        page.locator('#image-file').set_input_files(str(Path('loadtest/sample.jpg').resolve()))
        expect(page.locator('#run')).to_be_enabled()
        page.keyboard.press('Control+Enter')
        expect(page.locator('#message')).to_have_text('Detection complete.')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(args.output / 'mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        assert page.evaluate('localStorage.length === 0 && sessionStorage.length === 0')
        assert key not in page.locator('#history').inner_text() if key else True
        page.reload()
        expect(page.locator('#session-count')).to_have_text('0')
        if key:
            expect(page.locator('#model-state')).to_have_text('Unavailable')
        assert not errors, errors
        context.close()
        browser.close()
    print('PASS: real browser samples, upload, overlay, keyboard tabs, PNG, mobile and session privacy.')


if __name__ == '__main__':
    main()
