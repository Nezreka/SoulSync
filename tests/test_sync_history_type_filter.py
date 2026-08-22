"""The dashboard sync band's history feed must filter by type SERVER-side.

Boulder, Aug 2026: three playlists (Discover Weekly, ListenBrainz Weekly Jams,
ListenBrainz Top Discoveries) showed "no runs yet" on the dashboard and would
not open, while their pipelines were completing fine — 58, 21 and 22 completed
runs respectively, the latest that same afternoon.

The band asks the history feed for N rows and matches each schedule to its own
latest run by name. The feed fetched 10 rows and then dropped the album ones in
javascript, so five recent album downloads left five playlist runs for eight
schedules to match against. Three lost, and a row with no run was also not
clickable, which is why those playlists would not open.

A filter after LIMIT is not a filter.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


_seq = [0]


def _add(db, name, sync_type, started_at):
    _seq[0] += 1
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO sync_history (batch_id, playlist_name, sync_type, source, "
        "profile_id, started_at, tracks_json) "
        "VALUES (?, ?, ?, 'mirrored', 1, ?, '[]')",
        (f"b{_seq[0]}", name, sync_type, started_at))
    conn.commit()
    conn.close()


class TestTheFilterRunsBeforeTheLimit:
    def test_albums_do_not_eat_the_playlist_budget(self, db):
        """Boulder's exact shape: albums interleaved with playlist runs."""
        _add(db, "Release Radar", "playlist", "2026-08-22 10:00:00")
        for i in range(5):
            _add(db, f"Some Album {i}", "album", f"2026-08-22 09:5{i}:00")
        _add(db, "Discover Weekly", "playlist", "2026-08-22 09:00:00")

        entries, _ = db.get_sync_history(limit=3, profile_id=1, sync_type='playlist')
        names = [e["playlist_name"] for e in entries]
        assert "Discover Weekly" in names, "albums pushed a playlist out of the window"
        assert not any(n.startswith("Some Album") for n in names)

    def test_rows_with_a_blank_sync_type_count_as_playlists(self, db):
        """The old client-side filter was `=== 'playlist' || !sync_type`, so a
        blank counted. The SQL keeps that, and also allows NULL for databases
        that predate the NOT NULL on this column — untestable on a fresh schema,
        which is exactly why it is written defensively rather than dropped."""
        _add(db, "Old Run", "", "2026-08-22 10:00:00")
        entries, _ = db.get_sync_history(limit=10, profile_id=1, sync_type='playlist')
        assert [e["playlist_name"] for e in entries] == ["Old Run"]

    def test_asking_for_albums_gives_only_albums(self, db):
        _add(db, "A Playlist", "playlist", "2026-08-22 10:00:00")
        _add(db, "An Album", "album", "2026-08-22 09:00:00")
        entries, _ = db.get_sync_history(limit=10, profile_id=1, sync_type='album')
        assert [e["playlist_name"] for e in entries] == ["An Album"]

    def test_no_filter_still_returns_everything(self, db):
        _add(db, "A Playlist", "playlist", "2026-08-22 10:00:00")
        _add(db, "An Album", "album", "2026-08-22 09:00:00")
        entries, _ = db.get_sync_history(limit=10, profile_id=1)
        assert len(entries) == 2


class TestWiring:
    def test_the_endpoint_passes_sync_type_through(self):
        import ast
        with open("web_server.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "get_sync_history")
        # it must READ the arg and HAND it to the db call
        src = ast.unparse(fn)
        assert "sync_type" in src
        call = next(c for c in ast.walk(fn) if isinstance(c, ast.Call)
                    and getattr(c.func, "attr", "") == "get_sync_history")
        assert any(kw.arg == "sync_type" for kw in call.keywords)

    def test_the_dashboard_asks_for_playlists_only(self):
        with open("webui/src/routes/dashboard/-dash.api.ts", encoding="utf-8") as f:
            src = f.read()
        assert "sync_type=playlist" in src, "the band must filter server-side"
        assert "limit=10'" not in src, "the 10-row window is what starved it"
