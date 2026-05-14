"""Tests for the WebUI asset helpers."""

from __future__ import annotations

import json
import os
import time

import pytest

from core.webui import (
    build_webui_vite_assets,
    clear_webui_vite_manifest_cache,
    load_webui_vite_manifest,
    should_serve_webui_spa,
)


def test_build_webui_vite_assets_renders_dev_scripts():
    html = build_webui_vite_assets("body", dev=True, dev_url="http://127.0.0.1:5173")
    assert html == (
        '<script type="module" src="http://127.0.0.1:5173/static/dist/@vite/client"></script>\n'
        '<script type="module" src="http://127.0.0.1:5173/static/dist/src/app/main.tsx"></script>'
    )


def test_build_webui_vite_assets_renders_manifest_assets():
    manifest = {
        "src/app/main.tsx": {
            "css": ["assets/main.css"],
            "file": "assets/main.js",
        }
    }

    html_head = build_webui_vite_assets(
        "head",
        manifest_loader=lambda: manifest,
        static_url_builder=lambda filename: f"/assets/{filename}",
    )
    html_body = build_webui_vite_assets(
        "body",
        manifest_loader=lambda: manifest,
        static_url_builder=lambda filename: f"/assets/{filename}",
    )

    assert html_head == '<link rel="stylesheet" href="/assets/dist/assets/main.css">'
    assert html_body == '<script type="module" src="/assets/dist/assets/main.js"></script>'


@pytest.mark.parametrize(
    "pathname",
    [
        "/api/issues",
        "/auth/spotify",
        "/callback",
        "/callback/extra",
        "/deezer/callback",
        "/socket.io",
        "/static/app.js",
        "/stream/file",
        "/tidal/callback",
        "/status",
    ],
)
def test_should_serve_webui_spa_blocks_reserved_paths(pathname):
    assert should_serve_webui_spa(pathname) is False


@pytest.mark.parametrize(
    "pathname",
    [
        "/",
        "/issues",
        "/issues?issueId=7",
        "/artists/Opeth",
        "/discover",
    ],
)
def test_should_serve_webui_spa_allows_client_routes(pathname):
    assert should_serve_webui_spa(pathname) is True


def test_load_webui_vite_manifest_reloads_when_file_changes(tmp_path):
    clear_webui_vite_manifest_cache()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"src/app/main.tsx": {"file": "assets/one.js"}}))

    first = load_webui_vite_manifest(manifest_path)
    assert first["src/app/main.tsx"]["file"] == "assets/one.js"

    manifest_path.write_text(json.dumps({"src/app/main.tsx": {"file": "assets/two.js"}}))
    future = time.time() + 10
    os.utime(manifest_path, (future, future))

    second = load_webui_vite_manifest(manifest_path)
    assert second["src/app/main.tsx"]["file"] == "assets/two.js"
