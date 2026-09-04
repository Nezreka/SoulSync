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


# ---------------------------------------------------------------------------
# Resolving a library track's own profile
# ---------------------------------------------------------------------------

import sqlite3

from core.quality.selection import profile_id_for_library_track


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def get_connection(self):
        return self._conn


def _library_db(rows):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, quality_profile_id INTEGER)")
    conn.executemany("INSERT INTO tracks VALUES (?, ?)", rows)
    return _Db(conn)


def test_a_library_track_answers_with_its_own_profile():
    """The redownload lane deletes the file it replaces.

    It only ever read a profile out of the request body, and the shipped UI
    sends none, so search, download and import all ran on the app default. A
    file the default accepts could then replace a track on a stricter profile,
    and the original was deleted afterwards.
    """
    database = _library_db([('t1', 7), ('t2', None)])

    assert profile_id_for_library_track(database, 't1') == 7


def test_a_track_without_its_own_profile_falls_back_to_the_default():
    database = _library_db([('t2', None)])

    assert profile_id_for_library_track(database, 't2') is None


def test_an_explicit_choice_outranks_the_stored_one():
    database = _library_db([('t1', 7)])

    assert profile_id_for_library_track(database, 't1', explicit=9) == 9


def test_an_unknown_track_or_broken_db_is_not_an_error():
    database = _library_db([('t1', 7)])

    assert profile_id_for_library_track(database, 'nope') is None
    assert profile_id_for_library_track(None, 't1') is None
