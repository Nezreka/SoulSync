"""The item's quality profile has to reach every place that filters candidates.

``get_valid_candidates`` applies the profile's ladder — the YouTube filter and,
since the Prowlarr gate, torrent/Usenet as well. A call site that omits the
profile silently falls back to the app default, which looks identical to
"the profile allowed it".
"""

import ast
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SEARCHED = ('web_server.py', 'core', 'api')


def _call_sites():
    for target in _SEARCHED:
        path = _ROOT / target
        files = [path] if path.is_file() else sorted(path.rglob('*.py'))
        for file in files:
            try:
                tree = ast.parse(file.read_text(encoding='utf-8'))
            except SyntaxError:  # pragma: no cover - not our file to fix
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name)
                    else None
                )
                if name == 'get_valid_candidates':
                    yield file.relative_to(_ROOT), node


def test_every_get_valid_candidates_call_passes_a_profile():
    bare = [
        f"{path}:{node.lineno}"
        for path, node in _call_sites()
        if len(node.args) < 4
        and not any(kw.arg == 'profile_id' for kw in node.keywords)
    ]

    assert not bare, (
        "these call sites drop the item's quality profile and silently use the "
        f"app default: {bare}"
    )
