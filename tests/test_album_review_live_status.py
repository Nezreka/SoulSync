"""The socket status must carry the same opt-in navigation state as HTTP."""
import ast
from pathlib import Path
from types import SimpleNamespace
from threading import Lock
import pytest


@pytest.mark.parametrize('enabled', [False, True])
def test_socket_status_includes_album_review_opt_in(enabled):
    tree = ast.parse(Path('web_server.py').read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_build_status_payload')
    namespace = {
        'config_manager': SimpleNamespace(get=lambda key, default=None: enabled if key == 'experimental.album_review_enabled' else default, get_active_media_server=lambda: 'test'),
        '_status_cache': {}, 'tasks_lock': Lock(), 'download_tasks': {},
        'spotify_client': None,
        'get_metadata_status_snapshot': lambda **kwargs: {'metadata_source': 'musicbrainz', 'spotify': {}},
        '_spotify_status_with_availability': lambda value: value,
        '_get_enrichment_status': lambda: {},
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'web_server.py', 'exec'), namespace)
    assert namespace['_build_status_payload']()['_experimental']['album_review_enabled'] is enabled
