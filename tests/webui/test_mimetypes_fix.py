"""#979: the React pages (module-loaded) go black when the OS registry serves .js
as text/plain, because browsers refuse a <script type="module"> with a non-JS
MIME. ensure_web_mimetypes() must override any such mapping."""

from __future__ import annotations

import mimetypes

import pytest

from core.webui.mimetypes_fix import corrected_script_content_type, ensure_web_mimetypes


def test_js_is_forced_to_javascript_even_if_registry_says_text_plain():
    mimetypes.add_type("text/plain", ".js")     # simulate a broken Windows registry
    ensure_web_mimetypes()
    assert mimetypes.guess_type("main-Dg73FktO.js")[0] == "text/javascript"
    assert mimetypes.guess_type("chunk.mjs")[0] == "text/javascript"


def test_css_and_manifest_types():
    mimetypes.add_type("text/plain", ".css")
    ensure_web_mimetypes()
    assert mimetypes.guess_type("main.css")[0] == "text/css"
    assert mimetypes.guess_type("app.webmanifest")[0] == "application/manifest+json"


def test_idempotent():
    ensure_web_mimetypes()
    ensure_web_mimetypes()
    assert mimetypes.guess_type("x.js")[0] == "text/javascript"


# ── #986: HTTP-layer enforcement, independent of the OS mimetypes registry ──
@pytest.mark.parametrize("path,current", [
    ("/static/dist/assets/main-Dg73FktO.js", "application/octet-stream"),
    ("/static/dist/assets/main-Dg73FktO.js", "text/plain"),
    ("/static/dist/assets/main.js", None),          # Flask couldn't guess → no header
    ("/static/dist/assets/chunk.mjs", "text/plain"),
    ("/some.JS", "binary/octet-stream"),            # case-insensitive path
])
def test_bad_js_content_type_is_forced(path, current):
    assert corrected_script_content_type(path, current) == "text/javascript; charset=utf-8"


@pytest.mark.parametrize("path,current", [
    ("/static/dist/assets/main.js", "text/javascript"),          # already valid
    ("/static/dist/assets/main.js", "application/javascript"),   # also valid for modules
    ("/sw.js", "text/javascript; charset=utf-8"),                # valid + charset
    ("/api/stats", "application/json"),                          # not a script
    ("/static/main.css", "text/css"),                            # not a script
    ("/report.json", "application/octet-stream"),                # non-.js left alone
])
def test_valid_or_non_js_left_unchanged(path, current):
    assert corrected_script_content_type(path, current) is None
