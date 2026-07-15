"""Web integration tests for strict discography provider failures."""

from __future__ import annotations

import ast
from pathlib import Path

from core.artist_source_detail import build_source_only_artist_detail
from core.metadata import artist_image as metadata_artist_image


def _web_function_source(function_name: str) -> str:
    source = Path("web_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"Function {function_name!r} was not found in web_server.py")


def test_source_only_artist_detail_preserves_provider_access_error(monkeypatch):
    monkeypatch.setattr(
        metadata_artist_image,
        "get_artist_image_url",
        lambda *_args, **_kwargs: None,
    )

    def provider_failure(*_args, **_kwargs):
        return {
            "success": False,
            "state": "error",
            "albums": [],
            "eps": [],
            "singles": [],
            "source": "deezer",
            "error": "Could not access deezer: connection timed out",
            "status_code": 504,
        }

    payload, status = build_source_only_artist_detail(
        "123",
        "Example Artist",
        "deezer",
        discography_loader=provider_failure,
    )

    assert status == 504
    assert payload == {
        "success": False,
        "state": "error",
        "error": "Could not access deezer: connection timed out",
        "source": "deezer",
        "status_code": 504,
    }


def test_library_artist_endpoint_does_not_hide_provider_access_error():
    source = _web_function_source("get_artist_detail")

    assert "artist_detail_discography.get('state') == 'error'" in source
    assert '"state": "error"' in source
    assert 'artist_detail_discography.get("status_code") or 502' in source
    assert "merged_discography = owned_releases" in source


def test_download_discography_endpoint_uses_strict_facade():
    source = _web_function_source("get_artist_discography")

    assert (
        "from core.metadata_service import "
        "get_artist_detail_discography as _get_artist_discography"
    ) in source
    assert "discography.get('state') == 'error'" in source
    assert 'discography.get("status_code") or 502' in source


def test_artist_detail_frontend_surfaces_api_error_message():
    source = Path("webui/static/library.js").read_text(encoding="utf-8")

    assert "if (!response.ok || !data.success)" in source
    assert "data.error || `Failed to load artist data:" in source
