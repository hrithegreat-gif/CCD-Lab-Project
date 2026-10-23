"""Check the real shipped UI contract, independent of mocked route assets."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


@pytest.fixture
def document():
    parser = AssetParser()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    return parser.nodes


@pytest.mark.parametrize("name", ["index.html", "studio.css", "studio.mjs", "studio-core.mjs", "favicon.svg"])
def test_shipped_assets_exist_and_are_utf8(name):
    assert (STATIC / name).read_text(encoding="utf-8").strip()


def test_controller_literal_dom_ids_exist_exactly_once(document):
    ids = Counter(attrs["id"] for _, attrs in document if "id" in attrs)
    assert all(count == 1 for count in ids.values())
    controller = (STATIC / "studio.mjs").read_text(encoding="utf-8")
    references = set(re.findall(r"\$\('([a-z][a-z0-9_-]*)'\)", controller))
    references.update(f"{prefix}-{view}" for prefix in ("view", "nav") for view in ("playground", "models", "runs"))
    assert references <= ids.keys(), f"Missing UI IDs: {references - ids.keys()}"


def test_html_has_no_inline_scripts_handlers_or_styles(document):
    scripts = [(tag, attrs) for tag, attrs in document if tag == "script"]
    assert scripts == [("script", {"type": "module", "src": "/studio/assets/studio.mjs"})]
    for _, attrs in document:
        assert "style" not in attrs
        assert not any(key.lower().startswith("on") for key in attrs)
    links = {attrs.get("href") for tag, attrs in document if tag == "link"}
    assert links == {"/studio/assets/studio.css", "/studio/assets/favicon.svg"}


def test_key_field_never_submits_via_native_get_or_has_a_default(document):
    key = next(attrs for _, attrs in document if attrs.get("id") == "api-key")
    assert key["type"] == "password"
    assert key["autocomplete"] == "off"
    assert "name" not in key and "value" not in key
    assert "localStorage" not in (STATIC / "studio.mjs").read_text(encoding="utf-8")
    assert "sessionStorage" not in (STATIC / "studio.mjs").read_text(encoding="utf-8")


def test_file_picker_types_and_accessible_overlay(document):
    picker=next(attrs for tag,attrs in document if attrs.get('id')=='image-file')
    assert picker['type']=='file' and picker['accept']=='image/jpeg,image/png,image/webp'
    assert any(tag=='canvas' and attrs.get('id')=='overlay' for tag,attrs in document)
    assert any(tag=='tbody' and attrs.get('id')=='detections' for tag,attrs in document)
