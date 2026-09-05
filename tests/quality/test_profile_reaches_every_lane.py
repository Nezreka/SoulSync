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


class _KeepOpen:
    """The real ``_get_connection`` hands out a FRESH connection per call and
    the caller closes it. This double reuses one in-memory db across calls, so
    swallow the close instead of tearing the fixture down mid-test."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def close(self):
        pass


class _Db:
    """Mirrors MusicDatabase's real accessor name, which is the private one.

    A double that spells the method the way the CALLER wants proves nothing.
    See test_the_lookup_calls_a_method_the_real_database_has.
    """

    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return _KeepOpen(self._conn)


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


def test_the_lookup_calls_a_method_the_real_database_has():
    """The first cut called ``database.get_connection()``.

    MusicDatabase has no such method, only ``_get_connection``, so every call
    raised AttributeError into the catch-all and answered None. The redownload
    lane therefore ran on the app default exactly as before the fix, on the one
    path that deletes the file it replaces. The stub above hid it by defining
    whichever name the code asked for, so this one is spec'd to the real class:
    a method MusicDatabase does not expose raises here instead of passing.
    """
    from unittest.mock import MagicMock

    from database.music_database import MusicDatabase

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, quality_profile_id INTEGER)")
    conn.execute("INSERT INTO tracks VALUES ('t1', 7)")

    database = MagicMock(spec=MusicDatabase)
    database._get_connection.return_value = conn

    assert profile_id_for_library_track(database, 't1') == 7


def test_the_lookup_closes_the_connection_it_opened():
    """``_get_connection`` hands back a NEW sqlite connection every call.

    Redownload search runs this per request; leaking one connection each time
    is a file handle that never comes back.
    """
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tracks (id TEXT PRIMARY KEY, quality_profile_id INTEGER)")
    conn.execute("INSERT INTO tracks VALUES ('t1', 7)")
    closed = []

    class _CountsCloses(_KeepOpen):
        def close(self):
            closed.append(True)

    class _Database:
        def _get_connection(self):
            return _CountsCloses(conn)

    assert profile_id_for_library_track(_Database(), 't1') == 7
    assert closed, "the connection was opened and never closed"
