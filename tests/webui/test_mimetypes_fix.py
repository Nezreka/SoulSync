"""#979: the React pages (module-loaded) go black when the OS registry serves .js
as text/plain, because browsers refuse a <script type="module"> with a non-JS
MIME. ensure_web_mimetypes() must override any such mapping."""

from __future__ import annotations

import mimetypes

from core.webui.mimetypes_fix import ensure_web_mimetypes


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
