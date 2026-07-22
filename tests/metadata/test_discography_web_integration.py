"""Web integration tests for strict discography provider failures."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from core.artist_source_detail import build_source_only_artist_detail
from core.metadata import artist_image as metadata_artist_image


def _web_function_node(function_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    source = Path("web_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return node
    raise AssertionError(f"Function {function_name!r} was not found in web_server.py")


def _imports_name(node: ast.AST, module: str, name: str) -> bool:
    return any(
        isinstance(child, ast.ImportFrom)
        and child.module == module
        and any(alias.name == name for alias in child.names)
        for child in ast.walk(node)
    )


def _is_state_error_test(test: ast.AST, variable_name: str) -> bool:
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "error"
        and isinstance(test.left, ast.Call)
        and isinstance(test.left.func, ast.Attribute)
        and test.left.func.attr == "get"
        and isinstance(test.left.func.value, ast.Name)
        and test.left.func.value.id == variable_name
    )


def _state_error_guard(node: ast.AST, variable_name: str) -> ast.If:
    for child in ast.walk(node):
        if isinstance(child, ast.If) and _is_state_error_test(child.test, variable_name):
            return child
    raise AssertionError(f"No state=error guard found for {variable_name}")


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


def test_owned_artist_keeps_library_releases_and_exposes_provider_error():
    node = _web_function_node("get_artist_detail")

    assert _imports_name(
        node,
        "core.metadata.discography_strict",
        "get_artist_detail_discography",
    )
    guard = _state_error_guard(node, "artist_detail_discography")
    assert not any(isinstance(child, ast.Return) for child in ast.walk(guard))
    assert any(
        isinstance(child, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "merged_discography"
            for target in (child.targets if isinstance(child, ast.Assign) else [child.target])
        )
        for child in ast.walk(guard)
    )
    assert any(
        isinstance(child, ast.Subscript)
        and isinstance(child.value, ast.Name)
        and child.value.id == "response_data"
        and isinstance(child.slice, ast.Constant)
        and child.slice.value == "provider_error"
        for child in ast.walk(node)
    )


def test_download_discography_endpoint_uses_strict_facade():
    node = _web_function_node("get_artist_discography")

    assert _imports_name(
        node,
        "core.metadata.discography_strict",
        "get_artist_detail_discography",
    )
    guard = _state_error_guard(node, "discography")
    assert any(isinstance(child, ast.Return) for child in ast.walk(guard))


def test_legacy_metadata_facade_remains_lenient():
    node = ast.parse(Path("core/metadata_service.py").read_text(encoding="utf-8"))

    assert _imports_name(node, "core.metadata.discography", "get_artist_discography")
    assert _imports_name(
        node, "core.metadata.discography", "get_artist_detail_discography"
    )
    assert not any(
        isinstance(child, ast.ImportFrom)
        and child.module == "core.metadata.discography_strict"
        for child in ast.walk(node)
    )


def test_artist_detail_frontend_surfaces_api_and_owned_provider_errors():
    source = Path("webui/static/library.js").read_text(encoding="utf-8")

    assert re.search(
        r"if\s*\(\s*!response\.ok\s*\|\|\s*!data\.success\s*\)",
        source,
    )
    assert re.search(r"throw\s+new\s+Error\([\s\S]*?data\.error", source)
    assert re.search(r"data\.provider_error\?\.error", source)
