"""#1550: the artist-page completion stream labels "missing" releases the
watchlist scan would deliberately skip.

The scan honors the user's own filters (content-type and release-type);
the artist page used to show those releases as a bare "Missing", which
reads as a broken scanner. These tests pin the stream wiring in
``core.metadata.completion.iter_artist_discography_completion_events``:
settings are resolved once per stream (not per release), missing singles
get the exclusion reason, owned releases are untouched, and a resolver
failure degrades to unlabeled events rather than breaking the stream.
"""

import sys
import types
from types import SimpleNamespace


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "unidecode" not in sys.modules:
    unidecode_mod = types.ModuleType("unidecode")
    unidecode_mod.unidecode = lambda s: s
    sys.modules["unidecode"] = unidecode_mod

if "core.settings" not in sys.modules:
    settings_mod = types.ModuleType("core.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return {
                "watchlist.global_override_enabled": False,
                "watchlist.exclude_terms": "",
            }.get(key, default)

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    sys.modules["core.settings"] = settings_mod

from core.metadata import completion as metadata_completion  # noqa: E402


def _watch_artist(name, **prefs):
    defaults = dict(
        include_live=False,
        include_remixes=False,
        include_acoustic=False,
        include_instrumentals=False,
        include_compilations=False,
        include_albums=True,
        include_eps=True,
        include_singles=True,
    )
    defaults.update(prefs)
    return SimpleNamespace(artist_name=name, **defaults)


class _StreamDB:
    """Enough of the DB surface for the completion generator."""

    def __init__(self, watchlist_rows=()):
        self._rows = list(watchlist_rows)
        self.watchlist_calls = 0

    def get_watchlist_artists(self, profile_id=1):
        self.watchlist_calls += 1
        return self._rows

    def get_candidate_albums_for_artist(self, *args, **kwargs):
        return []


def _fake_check_single_factory(owned_names=()):
    def _fake(db, single, artist_name, **kwargs):
        name = single.get("name", "")
        if name in owned_names:
            return {
                "id": single.get("id"),
                "name": name,
                "status": "completed",
                "owned_tracks": 1,
                "expected_tracks": 1,
            }
        return {
            "id": single.get("id"),
            "name": name,
            "status": "missing",
            "owned_tracks": 0,
            "expected_tracks": single.get("total_tracks", 1),
        }

    return _fake


def _fake_check_album(db, album, artist_name, **kwargs):
    return {
        "id": album.get("id"),
        "name": album.get("name", ""),
        "status": "missing",
        "owned_tracks": 0,
        "expected_tracks": album.get("total_tracks", 10),
    }


def _run_stream(monkeypatch, db, discography, artist_name="Étienne de Crécy"):
    monkeypatch.setattr(
        metadata_completion, "check_single_completion", _fake_check_single_factory()
    )
    monkeypatch.setattr(metadata_completion, "check_album_completion", _fake_check_album)
    events = list(
        metadata_completion.iter_artist_discography_completion_events(
            discography, artist_name=artist_name, db=db
        )
    )
    return [e for e in events if e.get("type") in ("single_completion", "album_completion")]


class TestCompletionWatchlistExclusionWiring:
    def test_missing_remix_single_is_labeled(self, monkeypatch):
        """The #1550 case end to end: watched artist, default filters, a
        remix-titled single shows 'missing' WITH the reason attached."""
        db = _StreamDB([_watch_artist("Étienne de Crécy")])
        discography = {
            "albums": [],
            "singles": [
                {"id": "s1", "name": "Am I Wrong (Olympic Mix)", "total_tracks": 1},
            ],
        }
        (event,) = _run_stream(monkeypatch, db, discography)
        assert event["status"] == "missing"
        assert event["watchlist_excluded"] == "remix"

    def test_settings_resolved_once_per_stream(self, monkeypatch):
        """N+1 guard: get_watchlist_artists is a full table scan — it must
        run once per artist page load, not once per missing release."""
        db = _StreamDB([_watch_artist("Étienne de Crécy")])
        discography = {
            "albums": [],
            "singles": [
                {"id": f"s{i}", "name": f"Song {i} (Remix)", "total_tracks": 1}
                for i in range(5)
            ],
        }
        events = _run_stream(monkeypatch, db, discography)
        assert len(events) == 5
        assert all(e["watchlist_excluded"] == "remix" for e in events)
        assert db.watchlist_calls == 1

    def test_owned_release_has_no_label_key(self, monkeypatch):
        db = _StreamDB([_watch_artist("Étienne de Crécy")])
        monkeypatch.setattr(
            metadata_completion,
            "check_single_completion",
            _fake_check_single_factory(owned_names={"Owned Song"}),
        )
        monkeypatch.setattr(metadata_completion, "check_album_completion", _fake_check_album)
        discography = {
            "albums": [],
            "singles": [{"id": "s1", "name": "Owned Song", "total_tracks": 1}],
        }
        events = [
            e
            for e in metadata_completion.iter_artist_discography_completion_events(
                discography, artist_name="Étienne de Crécy", db=db
            )
            if e.get("type") == "single_completion"
        ]
        assert events[0]["status"] == "completed"
        assert "watchlist_excluded" not in events[0]

    def test_album_with_albums_off_is_labeled(self, monkeypatch):
        """Release-type gate: the scan skips whole albums when
        include_albums is off — the page must say so."""
        db = _StreamDB([_watch_artist("Artist", include_albums=False)])
        discography = {
            "albums": [{"id": "a1", "name": "Some Album", "total_tracks": 12}],
            "singles": [],
        }
        (event,) = _run_stream(monkeypatch, db, discography, artist_name="Artist")
        assert event["watchlist_excluded"] == "albums"

    def test_resolver_failure_degrades_to_unlabeled_stream(self, monkeypatch):
        """If the watchlist lookup blows up, the stream still yields plain
        completion events — labels degrade to absent, never break the page."""

        class _BrokenDB(_StreamDB):
            def get_watchlist_artists(self, profile_id=1):
                raise RuntimeError("db locked")

        db = _BrokenDB([_watch_artist("Étienne de Crécy")])
        discography = {
            "albums": [],
            "singles": [{"id": "s1", "name": "Am I Wrong (Olympic Mix)", "total_tracks": 1}],
        }
        (event,) = _run_stream(monkeypatch, db, discography)
        assert event["status"] == "missing"
        assert "watchlist_excluded" not in event

    def test_unwatched_artist_has_no_labels(self, monkeypatch):
        """No watchlist row: the scan never runs for this artist, so there
        is no filter mismatch to explain."""
        db = _StreamDB([_watch_artist("Someone Else")])
        discography = {
            "albums": [],
            "singles": [{"id": "s1", "name": "Am I Wrong (Olympic Mix)", "total_tracks": 1}],
        }
        (event,) = _run_stream(monkeypatch, db, discography)
        assert event["status"] == "missing"
        assert "watchlist_excluded" not in event
