"""Tests for `core.library_reorganize.reorganize_album` — the new
post-processing-pipeline approach (the orchestrator that copies files
to staging and routes them through the same code that handles fresh
downloads, instead of doing per-album template work in web_server).

Contract this test file pins:

1. Albums without ANY metadata-source ID return ``status='no_source_id'``
   without staging anything, copying anything, or calling post-process.
   Silent degradation to file tags is the failure mode the previous
   implementation had; the new contract is "we have the source of
   truth or we don't touch the album."
2. Source resolution honors the configured primary first, then walks
   ``get_source_priority`` until something returns a tracklist.
3. Each library track is matched to the API tracklist by
   ``track_number``. Tracks not in the API response (bonus tracks on a
   deluxe edition, etc.) are reported as skipped and left in place —
   they are NOT force-fed wrong context to post-process.
4. Files that don't resolve on disk are surfaced as skipped errors
   with the offending DB path, not silently dropped.
5. After a successful post-process the original file is removed and
   the DB row is updated to the new path. A failed post-process leaves
   the original alone so the user doesn't lose data.
6. Staging directory is cleaned up regardless of how the run ends.
"""

import os
import shutil
import sqlite3
import sys
import types

import pytest


# --- module stubs (same shape used elsewhere in the test suite) -----------
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

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod


from core import library_reorganize  # noqa: E402


# --- helpers --------------------------------------------------------------

class _FakeDB:
    """Wraps a sqlite3 in-memory connection that survives `close()` calls
    so the tests can reuse it for assertions after the orchestrator runs."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def _get_connection(self):
        return _NonClosingConnWrapper(self._conn)


class _NonClosingConnWrapper:
    def __init__(self, real):
        self._real = real

    def cursor(self):
        return self._real.cursor()

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def commit(self):
        return self._real.commit()

    def close(self):
        # Underlying connection survives — tests reuse it.
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _setup_album(db, *, album_id='alb-1', spotify_id='', deezer_id='',
                 itunes_id='', discogs_id='', soul_id='', tracks=()):
    """Build a minimal artists/albums/tracks schema and seed one album.

    `tracks` is a list of `(track_id, track_number, title, file_path)`.
    """
    cur = db._conn.cursor()
    cur.execute("CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT)")
    cur.execute("""
        CREATE TABLE albums (
            id TEXT PRIMARY KEY,
            artist_id TEXT,
            title TEXT,
            spotify_album_id TEXT,
            deezer_id TEXT,
            itunes_album_id TEXT,
            discogs_id TEXT,
            soul_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE tracks (
            id TEXT PRIMARY KEY,
            album_id TEXT,
            artist_id TEXT,
            title TEXT,
            track_number INTEGER,
            file_path TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("INSERT INTO artists VALUES (?, ?)", ('artist-1', 'Aerosmith'))
    cur.execute(
        "INSERT INTO albums (id, artist_id, title, spotify_album_id, deezer_id, "
        "itunes_album_id, discogs_id, soul_id) VALUES (?,?,?,?,?,?,?,?)",
        (album_id, 'artist-1', 'Aerosmith (1973)', spotify_id, deezer_id,
         itunes_id, discogs_id, soul_id),
    )
    for tid, tn, title, fp in tracks:
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) "
            "VALUES (?,?,?,?,?,?)",
            (tid, album_id, 'artist-1', title, tn, fp),
        )
    db._conn.commit()


@pytest.fixture
def tmpdirs(tmp_path):
    """Three working directories: original library files, staging root,
    transfer destination."""
    library = tmp_path / "library"
    staging = tmp_path / "staging"
    transfer = tmp_path / "transfer"
    library.mkdir()
    staging.mkdir()
    transfer.mkdir()
    return library, staging, transfer


def _make_audio_file(library_dir, name='song.flac', content=b'fakeflacdata'):
    p = library_dir / name
    p.write_bytes(content)
    return str(p)


# --- tests: source resolution ---------------------------------------------

def test_returns_no_source_id_when_album_has_none(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, tracks=[
        ('t1', 1, 'Same Old Song And Dance', _make_audio_file(library)),
    ])

    pp_calls = []

    def pp(key, ctx, fp):
        pp_calls.append(key)

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer', 'itunes', 'discogs', 'hydrabase'])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source', lambda *a: None)
    monkeypatch.setattr(library_reorganize, 'get_album_tracks_for_source', lambda *a: None)

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert summary['status'] == 'no_source_id'
    assert summary['moved'] == 0
    assert pp_calls == []


def test_falls_through_to_next_source_when_primary_returns_nothing(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, spotify_id='sp-1', deezer_id='dz-1', tracks=[
        ('t1', 1, 'Same Old Song And Dance', _make_audio_file(library)),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer'])

    def fake_album(src, sid):
        return {'id': sid, 'name': 'Aerosmith', 'release_date': '1973-01-01'} \
            if src == 'deezer' else None

    def fake_tracks(src, sid):
        return {'items': [{'id': 'dz-t1', 'name': 'Same Old Song And Dance',
                           'track_number': 1, 'disc_number': 1}]} \
            if src == 'deezer' else None

    monkeypatch.setattr(library_reorganize, 'get_album_for_source', fake_album)
    monkeypatch.setattr(library_reorganize, 'get_album_tracks_for_source', fake_tracks)

    def pp(key, ctx, fp):
        ctx['_final_processed_path'] = str(library / 'final.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert summary['source'] == 'deezer'
    assert summary['moved'] == 1


# --- tests: per-track behavior --------------------------------------------

def test_multi_disc_album_disambiguates_by_title(monkeypatch, tmpdirs):
    """The whole point of moving from track_number-only to title-based
    matching: a 2-disc album has track_number=1 on BOTH discs, but the
    titles differ. Each library track must end up routed to the API
    entry with the matching title — and therefore to the correct
    disc_number in the post-process context."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        # Disc 1 track 1: 'Same Old Song And Dance'
        ('t1d1', 1, 'Same Old Song And Dance', _make_audio_file(library, 'd1t1.flac')),
        # Disc 2 track 1: 'Dream On'
        ('t1d2', 1, 'Dream On', _make_audio_file(library, 'd2t1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'Aerosmith'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'd1t1', 'name': 'Same Old Song And Dance', 'track_number': 1, 'disc_number': 1},
            {'id': 'd2t1', 'name': 'Dream On', 'track_number': 1, 'disc_number': 2},
        ]},
    )

    title_to_disc = {}

    def pp(key, ctx, fp):
        # Capture which disc_number landed in the per-track context
        title_to_disc[ctx['track_info']['name']] = ctx['track_info']['disc_number']
        # Also record total_discs so we can assert it's correct
        title_to_disc.setdefault('_total_discs', ctx['spotify_album']['total_discs'])
        ctx['_final_processed_path'] = str(library / f"out_{ctx['track_info']['disc_number']}_{ctx['track_info']['track_number']}.flac")
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert summary['moved'] == 2
    # The crucial assertion: each track must get the disc_number of
    # its title-matched API entry, NOT a collapsed last-write-wins value.
    assert title_to_disc['Same Old Song And Dance'] == 1
    assert title_to_disc['Dream On'] == 2
    # And the album-level total_discs must be 2 so post-process inserts the subfolder
    assert title_to_disc['_total_discs'] == 2


def test_title_match_tolerates_smart_quotes_and_punctuation(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, "Don't Stop Believin'", _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        # API uses smart quotes — historically a common mismatch source
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Don’t Stop Believin’', 'track_number': 1, 'disc_number': 1},
        ]},
    )

    pp_calls = []

    def pp(key, ctx, fp):
        pp_calls.append(ctx['track_info']['name'])
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert summary['moved'] == 1
    assert len(pp_calls) == 1


def test_bonus_track_routes_to_correct_disc_via_substring_match(monkeypatch, tmpdirs):
    """Real-world scenario from winecountrygames's Kendrick Lamar deluxe:
    user has ``The Recipe - Bonus Track`` (track 1, disc 2 in his library)
    AND ``Sherane`` (track 1, disc 1). The API returns the bonus track as
    plain ``The Recipe`` (no suffix). Without substring matching, the
    bonus track falls through to track-number-only and lands on disc 1.
    With substring matching (gated on track_number), it correctly routes
    to disc 2."""
    library, _staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        # Disc 1, track 1
        ('t1d1', 1, 'Sherane', _make_audio_file(library, 'd1t1.flac')),
        # Disc 2, track 1 — local title has " - Bonus Track" suffix
        ('t1d2', 1, 'The Recipe - Bonus Track', _make_audio_file(library, 'd2t1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'good kid m.A.A.d city (Deluxe)'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Sherane', 'track_number': 1, 'disc_number': 1},
            {'id': 'a2', 'name': 'The Recipe', 'track_number': 1, 'disc_number': 2},
        ]},
    )

    title_to_disc = {}

    def pp(key, ctx, fp):
        title_to_disc[ctx['track_info']['name']] = ctx['track_info']['disc_number']
        ctx['_final_processed_path'] = str(library / f"out_{ctx['track_info']['disc_number']}.flac")
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(_staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    # The local "The Recipe - Bonus Track" must route to the API's
    # disc-2 entry (which is named just "The Recipe"), via substring
    # match + track_number tiebreaker.
    assert title_to_disc['Sherane'] == 1
    assert title_to_disc['The Recipe'] == 2


def test_dash_vs_parens_normalize_equally_for_remix_versions(monkeypatch, tmpdirs):
    """Local file has ``Bitch, Don't Kill My Vibe - Remix`` (dash style),
    API has the same track as ``Bitch, Don't Kill My Vibe (Remix)``
    (parens style). Both must normalize to the same string so tier 1
    matches without falling to substring or track_number fallbacks."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 5, "Bitch, Don't Kill My Vibe - Remix", _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': "Bitch, Don't Kill My Vibe (Remix)",
             'track_number': 5, 'disc_number': 2},
        ]},
    )

    matched = []

    def pp(key, ctx, fp):
        matched.append((ctx['track_info']['name'], ctx['track_info']['disc_number']))
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert matched == [("Bitch, Don't Kill My Vibe (Remix)", 2)]


def test_substring_match_handles_track_number_disagreement(monkeypatch, tmpdirs):
    """Real-world Kendrick Lamar deluxe case: the user's library has
    ``The Recipe (Black Hippy Remix) - Bonus Track`` numbered as track
    4 of disc 2, but Deezer has the same track at disc 2 track 5 (and
    has ``Bitch... (Remix)`` at disc 2 track 4). Track_number-gated
    containment misses; length-ratio containment must pick the right
    one without false-positive risk."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t4', 4, 'The Recipe (Black Hippy Remix) - Bonus Track',
         _make_audio_file(library, 't4.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            # API has the bonus tracks in a different order than the user
            {'id': 'd1t4', 'name': 'The Art of Peer Pressure',
             'track_number': 4, 'disc_number': 1},
            {'id': 'd2t4', 'name': "Bitch, Don't Kill My Vibe (Remix)",
             'track_number': 4, 'disc_number': 2},
            {'id': 'd2t5', 'name': 'The Recipe (Black Hippy Remix)',
             'track_number': 5, 'disc_number': 2},
        ]},
    )

    matched = []

    def pp(key, ctx, fp):
        matched.append((ctx['track_info']['name'], ctx['track_info']['disc_number']))
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    # The local Black Hippy Remix Bonus Track must end up in disc 2,
    # NOT collide with disc 1's "Art of Peer Pressure" via track_number.
    assert matched == [('The Recipe (Black Hippy Remix)', 2)]


def test_remix_does_not_substring_match_to_original_recording(monkeypatch, tmpdirs):
    """winecountrygames's iTunes case: iTunes doesn't have the remix,
    just the original ``Bitch Don't Kill My Vibe``. Substring + ratio
    alone would merge the local remix bonus track into the original
    via tier 4 (ratio 0.78). Reject because they have different version
    differentiators ('remix' vs none) — they're different recordings."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, itunes_id='it-1', tracks=[
        # Original — should match cleanly via tier 1 to iTunes' entry
        ('t2', 2, "Bitch, Don't Kill My Vibe", _make_audio_file(library, 't2.flac')),
        # Remix — iTunes doesn't have it; must report unmatched, NOT
        # collide with the original via substring
        ('t5', 5, "Bitch, Don't Kill My Vibe - Remix", _make_audio_file(library, 't5.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'itunes')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'it-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'it2', 'name': "Bitch, Don't Kill My Vibe",
             'track_number': 2, 'disc_number': 1},
        ]},
    )

    matched_titles = []
    skipped_titles = []

    def pp(key, ctx, fp):
        matched_titles.append(ctx['track_info']['name'])
        ctx['_final_processed_path'] = str(library / f'out_{ctx["track_info"]["name"]}.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    skipped_titles = [e['title'] for e in summary['errors']]
    # Only the original should have been processed
    assert matched_titles == ["Bitch, Don't Kill My Vibe"]
    # The remix should be reported as unmatched, NOT merged with the original
    assert "Bitch, Don't Kill My Vibe - Remix" in skipped_titles
    assert summary['moved'] == 1
    assert summary['skipped'] == 1


def test_substring_match_does_not_false_positive_across_discs(monkeypatch, tmpdirs):
    """Safety: ``Real`` (substring) must not silently map to a longer
    track like ``Real Real Real`` on a different disc. Substring match
    is gated on matching track_number; if the only API entry whose
    title contains the local one has a different track_number, the
    matcher must fall through to last-resort track_number-only."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t11', 11, 'Real', _make_audio_file(library, 't11.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            # Real-the-track on disc 1, position 11 — the right answer
            {'id': 'a1', 'name': 'Real', 'track_number': 11, 'disc_number': 1},
            # A nearby longer title on disc 2 that contains "real" — must NOT win
            {'id': 'a2', 'name': 'Real Real Real', 'track_number': 1, 'disc_number': 2},
        ]},
    )

    matched = []

    def pp(key, ctx, fp):
        matched.append((ctx['track_info']['name'], ctx['track_info']['disc_number']))
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    # Tier 1 (exact + track_number) wins for the legitimate disc 1 entry
    assert matched == [('Real', 1)]


def test_skips_track_when_source_tracklist_doesnt_contain_it(monkeypatch, tmpdirs):
    """winecountrygames's actual scenario: Deezer's response for the
    Kendrick deluxe was missing 'The Recipe (Black Hippy Remix)' — the
    user has 17 local tracks, Deezer knows 16. The 17th local track
    has no title-based match anywhere in the API tracklist. Per the
    design policy 'trust the source', we must NOT fall back to
    track_number-only matching (which would falsely route the missing
    bonus track to whatever disc-1 entry shares its track_number,
    causing a collision with a totally unrelated song)."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        # Local tn=4 — but API doesn't have this track at all; the only
        # API entry with track_number=4 is "The Art of Peer Pressure"
        # (a completely different song). Old tier-5 fallback would have
        # silently routed our bonus track to that entry → collision.
        ('t4', 4, 'The Recipe (Black Hippy Remix) - Bonus Track',
         _make_audio_file(library, 't4.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            # Same tn=4, completely different title — must NOT capture
            # the local track via track_number fallback.
            {'id': 'd1t4', 'name': 'The Art of Peer Pressure',
             'track_number': 4, 'disc_number': 1},
        ]},
    )

    pp_calls = []

    def pp(*a, **k):
        pp_calls.append(a)

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    # Track must be skipped, NOT routed to The Art of Peer Pressure.
    assert summary['moved'] == 0
    assert summary['skipped'] == 1
    assert pp_calls == []
    assert 'not in' in summary['errors'][0]['error'].lower() \
        or 'bonus' in summary['errors'][0]['error'].lower() \
        or 'non-canonical' in summary['errors'][0]['error'].lower()


def test_skips_track_not_in_api_tracklist(monkeypatch, tmpdirs):
    """Bonus track scenario: user has 12 tracks, source's catalog version
    only has 10. Tracks not in the API response must be skipped, NOT
    force-fed wrong context to post-process."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Track 2', _make_audio_file(library, 't2.flac')),
        ('t11', 11, 'Bonus Track', _make_audio_file(library, 't11.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Track 1', 'track_number': 1, 'disc_number': 1},
            {'id': 'a2', 'name': 'Track 2', 'track_number': 2, 'disc_number': 1},
        ]},
    )

    pp_for = []

    def pp(key, ctx, fp):
        pp_for.append(ctx['track_info']['track_number'])
        ctx['_final_processed_path'] = str(library / f"out_{ctx['track_info']['track_number']}.flac")
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert sorted(pp_for) == [1, 2]
    assert summary['moved'] == 2
    assert summary['skipped'] == 1
    assert any('Bonus Track' in e['title'] for e in summary['errors'])


def test_surfaces_unresolved_file_path(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', '/nonexistent/file.flac'),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    pp_calls = []

    def pp(*a, **k):
        pp_calls.append(a)

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: None,  # nothing resolves
        post_process_fn=pp,
    )

    assert summary['skipped'] == 1
    assert summary['moved'] == 0
    assert pp_calls == []
    assert '/nonexistent/file.flac' in summary['errors'][0]['error']


def test_failed_post_process_leaves_original_in_place(monkeypatch, tmpdirs):
    """If post-process fails (AcoustID rejection, exception, anything),
    the original file must remain at its location and the DB must NOT
    be updated. Worst-case the user retries; we don't lose data."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    src_file = _make_audio_file(library, 't1.flac')
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', src_file),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    def pp(key, ctx, fp):
        # Simulate AcoustID rejection: don't set _final_processed_path
        return

    db_updates = []

    def update_path(track_id, new_path):
        db_updates.append((track_id, new_path))

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        update_track_path_fn=update_path,
    )

    assert summary['failed'] == 1
    assert summary['moved'] == 0
    assert os.path.exists(src_file)
    assert db_updates == []


def test_post_process_exception_is_caught_and_original_preserved(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    src_file = _make_audio_file(library, 't1.flac')
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', src_file),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    def pp(*a, **k):
        raise RuntimeError("boom")

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert summary['failed'] == 1
    assert os.path.exists(src_file)


def test_recreates_staging_dir_when_post_process_cleans_it(monkeypatch, tmpdirs):
    """Regression test for the "1 moved, 15 failed (path not found)" bug
    winecountrygames hit on his first reorganize run.

    Post-processing calls `_cleanup_empty_directories` after each move.
    That walks up from the source file removing empties — and since the
    only thing in our staging_album_dir is the staged file we just had
    post-process consume, the dir is empty after the move and gets
    nuked. The next track's `shutil.copy2` then failed with WinError 3
    because the destination directory no longer existed.

    The orchestrator must recreate staging_album_dir before each copy."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Track 2', _make_audio_file(library, 't2.flac')),
        ('t3', 3, 'Track 3', _make_audio_file(library, 't3.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Track 1', 'track_number': 1},
            {'id': 'a2', 'name': 'Track 2', 'track_number': 2},
            {'id': 'a3', 'name': 'Track 3', 'track_number': 3},
        ]},
    )

    final_dir = library / 'final'
    final_dir.mkdir()
    pp_count = [0]

    def pp_with_aggressive_cleanup(key, ctx, fp):
        """Mimic real post-process: move the file, then walk up from
        the source directory removing empties (which includes our
        staging_album_dir)."""
        pp_count[0] += 1
        final = str(final_dir / f"final_{pp_count[0]}.flac")
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

        # Walk up from the staged file's old directory, deleting
        # any empty dir until we hit the staging root.
        dir_to_check = os.path.dirname(fp)
        while os.path.normpath(dir_to_check) != os.path.normpath(str(staging)):
            try:
                os.rmdir(dir_to_check)
            except OSError:
                break
            dir_to_check = os.path.dirname(dir_to_check)

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p,
        post_process_fn=pp_with_aggressive_cleanup,
    )

    # All three tracks must succeed despite the staging dir being
    # nuked between each one.
    assert summary['moved'] == 3
    assert summary['failed'] == 0


def test_db_update_failure_leaves_original_in_place(monkeypatch, tmpdirs):
    """Safety property: a failing DB write must NOT trigger the original
    file's deletion. Otherwise we'd have a library row pointing at a
    now-deleted path with no easy recovery. Better: leave the file at
    BOTH locations (original + new) so the next library scan re-indexes
    from the new path and the user doesn't lose data."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    src = _make_audio_file(library, 't1.flac')
    final_dir = library / 'final'
    final_dir.mkdir()

    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', src),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    final_path = str(final_dir / 't1.flac')

    def pp(key, ctx, fp):
        shutil.move(fp, final_path)
        ctx['_final_processed_path'] = final_path

    def update_path_explodes(track_id, new_path):
        raise RuntimeError("simulated DB failure")

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        update_track_path_fn=update_path_explodes,
    )

    assert os.path.exists(src), "Original must still exist when DB update failed"
    assert os.path.exists(final_path), "New path file should also exist (post-process succeeded)"


def test_successful_run_removes_original_and_updates_db(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    src = _make_audio_file(library, 't1.flac')
    final_dir = library / 'final'
    final_dir.mkdir()

    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', src),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    def pp(key, ctx, fp):
        # Pretend post-processing moved the staged file to a final location
        final = str(final_dir / 't1.flac')
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    db_updates = []

    def update_path(track_id, new_path):
        db_updates.append((track_id, new_path))

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        update_track_path_fn=update_path,
    )

    assert summary['moved'] == 1
    assert summary['failed'] == 0
    assert not os.path.exists(src)
    assert os.path.exists(str(final_dir / 't1.flac'))
    assert db_updates == [('t1', str(final_dir / 't1.flac'))]


# --- tests: cleanup -------------------------------------------------------

def test_staging_dir_cleaned_up_on_success(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    def pp(key, ctx, fp):
        final = str(library / 'final.flac')
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert os.listdir(str(staging)) == []


def test_staging_dir_cleaned_up_even_on_failure(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    def pp(key, ctx, fp):
        raise RuntimeError("simulated post-process explosion")

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert os.listdir(str(staging)) == []


# --- tests: misc ----------------------------------------------------------

def test_deletes_per_track_sidecars_after_successful_move(monkeypatch, tmpdirs):
    """Real-world Kendrick-Lamar-deluxe shape: each FLAC has a same-stem
    `.lrc` sidecar in the source folder. After the audio is moved to its
    new location, the original `.lrc` should be removed too — post-process
    handles whatever sidecar policy exists at the new destination."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    audio = _make_audio_file(library, '01 - Sherane.flac')
    lrc_path = library / '01 - Sherane.lrc'
    lrc_path.write_text('lyrics')
    nfo_path = library / '01 - Sherane.nfo'
    nfo_path.write_text('metadata')
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Sherane', audio),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Sherane', 'track_number': 1}]},
    )

    final_dir = library / 'final'
    final_dir.mkdir()

    def pp(key, ctx, fp):
        final = str(final_dir / 'out.flac')
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert not os.path.exists(audio)
    assert not lrc_path.exists()
    assert not nfo_path.exists()


def test_keeps_track_sidecars_when_track_fails_to_move(monkeypatch, tmpdirs):
    """If post-process fails (AcoustID rejection), the original audio is
    preserved — and so is its sidecar, because the user might want to
    investigate or recover the track."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    audio = _make_audio_file(library, '01 - Sherane.flac')
    lrc_path = library / '01 - Sherane.lrc'
    lrc_path.write_text('lyrics')
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Sherane', audio),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Sherane', 'track_number': 1}]},
    )

    def pp_rejects(key, ctx, fp):
        return  # don't set _final_processed_path = AcoustID-style rejection

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp_rejects,
    )

    assert os.path.exists(audio)
    assert lrc_path.exists()


def test_deletes_album_level_sidecars_when_directory_emptied(monkeypatch, tmpdirs):
    """After every track in a source dir is successfully moved out, the
    leftover album-level sidecars (cover.jpg, folder.jpg, etc.) should be
    removed too so the empty-dir pruner can take the dir. If even one
    track failed to move, leave them — the user might want the cover."""
    library, staging, _transfer = tmpdirs
    disc1_dir = library / 'Disc 1'
    disc1_dir.mkdir()
    a1 = _make_audio_file(disc1_dir, '01.flac')
    a2 = _make_audio_file(disc1_dir, '02.flac')
    cover = disc1_dir / 'cover.jpg'
    cover.write_bytes(b'JPEGdata')
    folder = disc1_dir / 'folder.jpg'
    folder.write_bytes(b'JPEGdata')

    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', a1),
        ('t2', 2, 'Track 2', a2),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Track 1', 'track_number': 1},
            {'id': 'a2', 'name': 'Track 2', 'track_number': 2},
        ]},
    )

    final_dir = library / 'final'
    final_dir.mkdir()

    def pp(key, ctx, fp):
        final = str(final_dir / f"{ctx['track_info']['track_number']}.flac")
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    assert not cover.exists()
    assert not folder.exists()


def test_keeps_album_sidecars_when_a_track_failed_to_move(monkeypatch, tmpdirs):
    """If even one track in the dir failed to move out, leave the album
    art alone — user might still want to look at / recover the album."""
    library, staging, _transfer = tmpdirs
    a1 = _make_audio_file(library, '01.flac')
    a2 = _make_audio_file(library, '02.flac')
    cover = library / 'cover.jpg'
    cover.write_bytes(b'JPEGdata')

    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', a1),
        ('t2', 2, 'Track 2', a2),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Track 1', 'track_number': 1},
            {'id': 'a2', 'name': 'Track 2', 'track_number': 2},
        ]},
    )

    final_dir = library / 'final'
    final_dir.mkdir()

    def pp(key, ctx, fp):
        # Track 1 succeeds, track 2 fails (no _final_processed_path set)
        if ctx['track_info']['track_number'] == 1:
            final = str(final_dir / '1.flac')
            shutil.move(fp, final)
            ctx['_final_processed_path'] = final

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    # Track 2 still in place → cover preserved
    assert os.path.exists(a2)
    assert cover.exists()


# --- preview function (shared planning with the orchestrator) -----------

def _fake_path_builder(context, spotify_artist, _album_info, file_ext):
    """Stand-in for `_build_final_path_for_track`. Inserts Disc N/ when
    total_discs > 1 — same convention the real builder uses."""
    album = context['spotify_album']['name']
    artist = spotify_artist['name']
    track_info = context['track_info']
    title = track_info['name']
    tn = track_info['track_number']
    dn = track_info['disc_number']
    total = context['spotify_album']['total_discs']
    parts = ['/transfer', artist, album]
    if total > 1:
        parts.append(f'Disc {dn}')
    parts.append(f"{tn:02d} - {title}{file_ext}")
    return '/'.join(parts), True


def _path_builder_album_vs_single(context, spotify_artist, album_info, file_ext):
    """Stand-in that emulates the real `_build_final_path_for_track`
    branch on `album_info.get('is_album')`. ALBUM mode produces an
    album folder with disc subfolder + numbered file; SINGLE mode
    produces a per-track folder named after the title (the bug
    output)."""
    artist = spotify_artist['name']
    if album_info and album_info.get('is_album'):
        album = album_info['album_name']
        title = album_info['clean_track_name']
        tn = album_info['track_number']
        dn = album_info['disc_number']
        total = context['spotify_album']['total_discs']
        if total > 1:
            return (f'/transfer/{artist}/{artist} - {album}/Disc {dn}/{tn:02d} - {title}{file_ext}', True)
        return (f'/transfer/{artist}/{artist} - {album}/{tn:02d} - {title}{file_ext}', True)
    title = context['track_info']['name']
    return (f'/transfer/{artist}/{artist} - {title}/{title}{file_ext}', True)


def test_preview_uses_album_mode_not_single_mode(monkeypatch, tmpdirs):
    """Regression for the bug where every track ended up in its own
    track-named folder (SINGLE MODE) because we passed None for
    album_info to the path builder. Multi-disc deluxe must produce
    one shared album folder, not N single folders."""
    library, _staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Sherane', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Bitch Dont Kill My Vibe', _make_audio_file(library, 't2.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'good kid, m.A.A.d city'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Sherane', 'track_number': 1, 'disc_number': 1},
            {'id': 'a2', 'name': 'Bitch Dont Kill My Vibe', 'track_number': 2, 'disc_number': 1},
        ]},
    )

    result = library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir='/transfer',
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_path_builder_album_vs_single,
    )

    paths = [it['new_path'] for it in result['tracks']]
    # Both tracks land under the SAME album folder, not per-track folders
    assert all('good kid, m.A.A.d city' in p for p in paths)
    # Files use track-number prefix (album mode), not bare title (single mode)
    assert any('01 - Sherane' in p for p in paths)
    assert any('02 - Bitch Dont Kill My Vibe' in p for p in paths)
    # Reject the single-mode shape explicitly
    assert not any(p.endswith('/Sherane.flac') for p in paths)


def test_preview_emits_disc_subfolders_for_multi_disc_albums(monkeypatch, tmpdirs):
    """The bug winecountrygames hit: preview showed all tracks at the
    album root with no Disc N/ subfolders, even on a deluxe edition.
    Verify the new planner-backed preview produces disc folders."""
    library, _staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1d1', 1, 'Sherane', _make_audio_file(library, 'd1t1.flac')),
        ('t1d2', 1, 'The Recipe', _make_audio_file(library, 'd2t1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'good kid, m.A.A.d city (Deluxe)'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Sherane', 'track_number': 1, 'disc_number': 1},
            {'id': 'a2', 'name': 'The Recipe', 'track_number': 1, 'disc_number': 2},
        ]},
    )

    result = library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir='/transfer',
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_fake_path_builder,
    )

    assert result['success'] is True
    assert result['status'] == 'planned'

    by_title = {it['title']: it for it in result['tracks']}
    assert 'Disc 1' in by_title['Sherane']['new_path']
    assert 'Disc 2' in by_title['The Recipe']['new_path']
    # And per-track disc_number is propagated for UI display
    assert by_title['Sherane']['disc_number'] == 1
    assert by_title['The Recipe']['disc_number'] == 2


def test_preview_status_no_source_id_when_album_lacks_ids(monkeypatch, tmpdirs):
    library, _staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'spotify', 'itunes', 'discogs', 'hydrabase'])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source', lambda *a: None)
    monkeypatch.setattr(library_reorganize, 'get_album_tracks_for_source', lambda *a: None)

    result = library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir='/transfer',
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_fake_path_builder,
    )

    assert result['status'] == 'no_source_id'
    assert result['success'] is False


def test_preview_marks_unmatched_tracks(monkeypatch, tmpdirs):
    """Tracks with no plausible API match (no exact title, no substring,
    no track_number) get reported as unmatched with a reason."""
    library, _staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'A Real Track', _make_audio_file(library, 't1.flac')),
        # Use a track_number with no API counterpart and a title that
        # has no substring overlap with anything in the API list — so
        # no tier matches.
        ('t99', 99, 'Completely Unrelated Side Quest',
         _make_audio_file(library, 't99.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'A Real Track', 'track_number': 1}]},
    )

    result = library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir='/transfer',
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_fake_path_builder,
    )

    by_title = {it['title']: it for it in result['tracks']}
    assert by_title['A Real Track']['matched'] is True
    assert by_title['A Real Track']['new_path']
    assert by_title['Completely Unrelated Side Quest']['matched'] is False
    assert by_title['Completely Unrelated Side Quest']['reason']
    assert by_title['Completely Unrelated Side Quest']['new_path'] == ''


def test_preview_uses_same_logic_as_apply(monkeypatch, tmpdirs):
    """Sanity check: a multi-disc album previewed and then applied
    should show the same destinations. If preview drift creeps in
    again, this fails."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1d1', 1, 'D1T1', _make_audio_file(library, 'd1t1.flac')),
        ('t1d2', 1, 'D2T1', _make_audio_file(library, 'd2t1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'Test Album'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'D1T1', 'track_number': 1, 'disc_number': 1},
            {'id': 'a2', 'name': 'D2T1', 'track_number': 1, 'disc_number': 2},
        ]},
    )

    preview = library_reorganize.preview_album_reorganize(
        album_id='alb-1', db=db, transfer_dir='/transfer',
        resolve_file_path_fn=lambda p: p,
        build_final_path_fn=_fake_path_builder,
    )

    # Now apply with the same matching logic; assert apply uses the
    # same disc_number per track that the preview reported.
    apply_disc_per_title = {}

    def pp(key, ctx, fp):
        apply_disc_per_title[ctx['track_info']['name']] = ctx['track_info']['disc_number']
        ctx['_final_processed_path'] = fp
        with open(fp, 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
    )

    preview_disc_per_title = {it['title']: it['disc_number'] for it in preview['tracks']}
    assert preview_disc_per_title == apply_disc_per_title


def test_available_sources_only_lists_authed_sources_with_stored_ids(monkeypatch):
    """The reorganize modal needs to know which sources the user can
    actually pick. A source is pickable iff: (a) we have an album ID
    for that source on the local row, AND (b) the user has the source
    authed/configured. Empty-ID sources and unauthed sources are
    omitted."""
    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'spotify', 'itunes', 'discogs', 'hydrabase'])

    # Authed: deezer + spotify only.
    auth = {'deezer': object(), 'spotify': object()}
    monkeypatch.setattr(library_reorganize, 'get_client_for_source',
                        lambda src: auth.get(src))

    album = {
        'spotify_album_id': 'sp-1',
        'deezer_id': 'dz-1',
        'itunes_album_id': 'it-1',  # has ID but user not authed
        'discogs_id': '',           # no ID
        'soul_id': '',              # no ID
    }

    sources = library_reorganize.available_sources_for_album(album)
    names = [s['source'] for s in sources]

    assert names == ['deezer', 'spotify']
    assert all('label' in s for s in sources)


def test_authed_sources_lists_all_authed_regardless_of_album_ids(monkeypatch):
    """Bulk reorganize uses this — needs the authed sources without
    requiring per-album ID coverage. Each album in the bulk run will
    do its own per-album ID check at apply time."""
    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer', 'itunes', 'discogs', 'hydrabase'])

    # Authed: spotify + deezer + itunes; discogs + hydrabase NOT authed.
    auth = {'spotify': object(), 'deezer': object(), 'itunes': object()}
    monkeypatch.setattr(library_reorganize, 'get_client_for_source',
                        lambda src: auth.get(src))

    sources = library_reorganize.authed_sources()
    names = [s['source'] for s in sources]

    # Primary first, then rest of priority chain — only authed ones
    assert names == ['spotify', 'deezer', 'itunes']
    assert all('label' in s for s in sources)


def test_strict_source_does_not_fall_back(monkeypatch, tmpdirs):
    """When the user picks a specific source in the modal, we must NOT
    silently fall back to another source if their pick fails. Picking
    Spotify means 'use Spotify or fail' — falling back would defeat
    the picker's purpose."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', spotify_id='sp-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer', 'itunes'])

    fetched = []

    def fake_album(src, sid):
        fetched.append(('album', src))
        if src == 'deezer':
            return {'id': 'dz-1', 'name': 'Album'}
        return None  # spotify "fails"

    def fake_tracks(src, sid):
        fetched.append(('tracks', src))
        if src == 'deezer':
            return {'items': [{'id': 'd1', 'name': 'Track 1', 'track_number': 1}]}
        return None

    monkeypatch.setattr(library_reorganize, 'get_album_for_source', fake_album)
    monkeypatch.setattr(library_reorganize, 'get_album_tracks_for_source', fake_tracks)

    pp_calls = []

    def pp(*a, **k):
        pp_calls.append(a)

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        primary_source='spotify', strict_source=True,
    )

    # Spotify failed; with strict_source we must NOT have queried Deezer.
    assert summary['status'] == 'no_source_id'
    assert summary['moved'] == 0
    assert pp_calls == []
    assert all(src == 'spotify' for _kind, src in fetched)


def test_non_strict_falls_back_when_primary_returns_nothing(monkeypatch, tmpdirs):
    """When the user did NOT pick a specific source (default behavior),
    the orchestrator walks the priority chain as before."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', spotify_id='sp-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(library_reorganize, 'get_source_priority',
                        lambda p: [p, 'deezer', 'itunes'])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda src, sid: ({'id': sid, 'name': 'A'} if src == 'deezer' else None))
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda src, sid: ({'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]}
                          if src == 'deezer' else None),
    )

    def pp(key, ctx, fp):
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        # No strict_source → uses default fallback chain
    )
    assert summary['source'] == 'deezer'
    assert summary['moved'] == 1


def test_returns_no_album_when_id_does_not_exist(tmpdirs):
    _library, staging, _transfer = tmpdirs
    db = _FakeDB()
    cur = db._conn.cursor()
    cur.execute("CREATE TABLE artists (id TEXT, name TEXT)")
    cur.execute(
        "CREATE TABLE albums (id TEXT, artist_id TEXT, title TEXT, "
        "spotify_album_id TEXT, deezer_id TEXT, itunes_album_id TEXT, "
        "discogs_id TEXT, soul_id TEXT)"
    )
    cur.execute(
        "CREATE TABLE tracks (id TEXT, album_id TEXT, artist_id TEXT, "
        "title TEXT, track_number INTEGER, file_path TEXT, updated_at TEXT)"
    )
    db._conn.commit()

    summary = library_reorganize.reorganize_album(
        album_id='does-not-exist', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=lambda *a: None,
    )

    assert summary['status'] == 'no_album'


def test_returns_no_tracks_when_album_has_none(tmpdirs):
    _library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[])

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=lambda *a: None,
    )

    assert summary['status'] == 'no_tracks'


def test_processes_tracks_concurrently_with_consistent_state(monkeypatch, tmpdirs):
    """Reorganize should run multiple tracks in parallel (matching the
    download-side worker count). Verify both the parallelism (we observe
    overlapping post-process calls) AND the state consistency (all
    tracks are accounted for, no double-counting from races)."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    track_count = 6
    rows = []
    for i in range(1, track_count + 1):
        rows.append((f't{i}', i, f'Track {i}', _make_audio_file(library, f't{i}.flac')))
    _setup_album(db, deezer_id='dz-1', tracks=rows)

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': f'a{i}', 'name': f'Track {i}', 'track_number': i}
            for i in range(1, track_count + 1)
        ]},
    )

    import threading
    import time

    in_flight = 0
    max_in_flight = 0
    in_flight_lock = threading.Lock()
    final_dir = library / 'final'
    final_dir.mkdir()

    def slow_pp(key, ctx, fp):
        nonlocal in_flight, max_in_flight
        with in_flight_lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        # Hold the worker briefly so concurrency is observable
        time.sleep(0.05)
        with in_flight_lock:
            in_flight -= 1
        out = str(final_dir / f"out_{ctx['track_info']['track_number']}.flac")
        shutil.move(fp, out)
        ctx['_final_processed_path'] = out

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=slow_pp,
    )

    # All 6 tracks processed; counts are consistent (no race-induced duplicates)
    assert summary['moved'] == track_count
    assert summary['skipped'] == 0
    assert summary['failed'] == 0
    # Should have observed at least 2 workers in flight at once
    # (3 is the configured cap; some overlap should always occur with 6 slow tracks)
    assert max_in_flight >= 2, f"Expected concurrent workers, only saw {max_in_flight} in flight"


def test_prunes_empty_destination_album_dirs(monkeypatch, tmpdirs):
    """When transfer_dir is provided, the orchestrator must clean up
    empty sibling album folders in the artist directory after the run.
    Catches both (a) leftovers from previous failed reorganize attempts
    that created standalone single-track folders, and (b) dirs created
    by `_build_final_path_for_track` that ended up empty when post-
    process failed AcoustID. Uses a single-level prune scoped to the
    artist folder — won't touch unrelated user dirs."""
    library, staging, transfer = tmpdirs
    db = _FakeDB()
    src = _make_audio_file(library, 't1.flac')
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', src),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Track 1', 'track_number': 1}]},
    )

    # Simulate the user's actual situation: transfer dir already has
    # an artist folder with leftover empty single-track album folders
    # from previous failed runs, plus an empty Disc-N subfolder.
    artist_dir = transfer / 'Artist'
    artist_dir.mkdir()
    (artist_dir / 'Artist - 2013 Backseat Freestyle').mkdir()
    (artist_dir / 'Artist - 2013 Compton').mkdir()
    leftover_with_disc = artist_dir / 'Artist - 2012 Old Single-Disc'
    leftover_with_disc.mkdir()
    (leftover_with_disc / 'Disc 1').mkdir()  # empty disc subfolder

    # Successful track lands in the real album folder
    real_album = artist_dir / 'Artist - 2013 Real Album'
    real_album.mkdir()

    def pp(key, ctx, fp):
        final = str(real_album / 'Disc 1' / '01 - Track 1.flac')
        os.makedirs(os.path.dirname(final), exist_ok=True)
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        transfer_dir=str(transfer),
    )

    # Empty leftover single-track album folders should be gone
    assert not (artist_dir / 'Artist - 2013 Backseat Freestyle').exists()
    assert not (artist_dir / 'Artist - 2013 Compton').exists()
    # The album with an empty Disc subfolder should also be cleaned
    # (Disc 1/ is empty → pruned, then Old Single-Disc/ is empty → pruned)
    assert not leftover_with_disc.exists()
    # Real album with successful track must still exist
    assert real_album.exists()
    assert (real_album / 'Disc 1' / '01 - Track 1.flac').exists()
    # Artist folder itself (still has the real album) untouched
    assert artist_dir.exists()


def test_context_dict_satisfies_post_process_contract(monkeypatch, tmpdirs):
    """Integration-style test: assert the per-track context dict the
    orchestrator hands to post-process contains every key
    `_post_process_matched_download` and `_build_final_path_for_track`
    actually read in production. If the real post-process starts
    requiring a new key in a future refactor, this test catches it
    BEFORE the user does — unit-mock tests would not.

    Keys verified are taken from a grep of the real functions in
    web_server.py at the time this test was written. The list is the
    contract; if it grows, the orchestrator's `_build_post_process_context`
    needs to grow too."""
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {
                            'id': 'dz-1',
                            'name': 'Test Album',
                            'release_date': '2024-03-15',
                            'total_tracks': 12,
                            'image_url': 'https://example.com/cover.jpg',
                        })
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{
            'id': 'a1', 'name': 'Track 1',
            'track_number': 1, 'disc_number': 1,
            'duration_ms': 240000,
            'artists': [{'name': 'Aerosmith'}],
            'uri': 'spotify:track:abc',
        }]},
    )

    captured_context = {}

    def assert_contract(key, ctx, fp):
        captured_context.update(ctx)
        # Mimic the bits of real post-process this test cares about
        ctx['_final_processed_path'] = str(library / 'out.flac')
        with open(ctx['_final_processed_path'], 'wb') as f:
            f.write(b'final')

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=assert_contract,
    )

    # Top-level keys the real post-process reads
    assert captured_context.get('is_album_download') is True
    assert captured_context.get('has_clean_spotify_data') is True
    assert captured_context.get('has_full_spotify_metadata') is True

    # spotify_artist (album-level artist context — not per-track)
    spotify_artist = captured_context.get('spotify_artist')
    assert isinstance(spotify_artist, dict)
    assert 'name' in spotify_artist
    assert 'id' in spotify_artist
    assert 'genres' in spotify_artist

    # spotify_album (used by `_build_final_path_for_track`)
    spotify_album = captured_context.get('spotify_album')
    assert isinstance(spotify_album, dict)
    assert spotify_album.get('id') == 'dz-1'
    assert spotify_album.get('name') == 'Test Album'
    assert 'release_date' in spotify_album        # year extraction
    assert 'total_tracks' in spotify_album        # ALBUM/EP/Single inference
    assert 'total_discs' in spotify_album         # Disc N/ subfolder gate
    assert 'image_url' in spotify_album           # album art

    # track_info (per-track signal — populates filename, tags, disc subfolder)
    track_info = captured_context.get('track_info')
    assert isinstance(track_info, dict)
    assert 'name' in track_info                   # filename
    assert 'id' in track_info                     # source track id
    assert 'track_number' in track_info           # filename + tag
    assert 'disc_number' in track_info            # disc subfolder + tag
    assert 'duration_ms' in track_info            # tag
    assert isinstance(track_info.get('artists'), list)  # tag — must be list
    assert all(isinstance(a, dict) and 'name' in a for a in track_info['artists'])

    # original_search_result (post-process reads this for fallbacks)
    osr = captured_context.get('original_search_result')
    assert isinstance(osr, dict)
    assert 'title' in osr
    assert 'spotify_clean_title' in osr           # `_build_final_path_for_track` reads this
    assert 'spotify_clean_album' in osr           # ditto
    assert 'track_number' in osr
    assert 'disc_number' in osr
    assert 'artists' in osr


def test_progress_callback_receives_updates(monkeypatch, tmpdirs):
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Track 1', _make_audio_file(library, 't1.flac')),
        ('t2', 2, 'Track 2', _make_audio_file(library, 't2.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': 'a1', 'name': 'Track 1', 'track_number': 1},
            {'id': 'a2', 'name': 'Track 2', 'track_number': 2},
        ]},
    )

    def pp(key, ctx, fp):
        final = str(library / f"final_{ctx['track_info']['track_number']}.flac")
        shutil.move(fp, final)
        ctx['_final_processed_path'] = final

    progress_log = []

    def on_progress(updates):
        progress_log.append(dict(updates))

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        on_progress=on_progress,
    )

    assert any('total' in u for u in progress_log)
    assert any('current_track' in u for u in progress_log)
    assert any(u.get('moved') == 2 for u in progress_log)


def test_watchdog_warns_about_stuck_workers(monkeypatch, tmpdirs, caplog):
    """When a worker exceeds the hung-threshold, the orchestrator must
    log a warning naming the stuck track. Real threshold is 5 minutes;
    we monkeypatch it down to ~50ms so the test runs in well under a
    second. Watchdog is passive (doesn't kill threads), so the worker
    should still complete normally after the warning."""
    import threading
    library, staging, _transfer = tmpdirs

    # Tiny watchdog so the test is fast. Interval shorter than threshold
    # so the loop checks at least once before the threshold trips.
    monkeypatch.setattr(library_reorganize, '_WATCHDOG_INTERVAL_SECONDS', 0.02)
    monkeypatch.setattr(library_reorganize, '_HUNG_WORKER_THRESHOLD_SECONDS', 0.05)

    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        ('t1', 1, 'Stuck Track', _make_audio_file(library, 't1.flac')),
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [{'id': 'a1', 'name': 'Stuck Track', 'track_number': 1}]},
    )

    release = threading.Event()

    def slow_pp(key, ctx, fp):
        # Hold long enough for the watchdog to trip the threshold + emit.
        # 0.2s vs 0.05s threshold + 0.02s interval = at least one warn pass.
        release.wait(timeout=0.25)
        ctx['_final_processed_path'] = fp
        with open(fp, 'wb') as f:
            f.write(b'final')

    caplog.set_level('WARNING', logger='library_reorganize')

    summary = library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=slow_pp,
    )
    release.set()

    # Track still completed (watchdog is passive — it doesn't abort)
    assert summary['moved'] == 1

    # And the watchdog warning was logged with the stuck track's title
    warnings = [
        r.getMessage() for r in caplog.records
        if r.levelname == 'WARNING' and 'Worker stuck' in r.getMessage()
    ]
    assert any('Stuck Track' in msg for msg in warnings), (
        f"Expected a 'Worker stuck' warning naming the track; got: {warnings}"
    )


def test_stop_check_aborts_remaining_tracks(monkeypatch, tmpdirs):
    """With concurrent workers, stop_check can't cancel a task that's
    already running — but it MUST prevent tasks that haven't started
    yet from running. Use enough tracks that the worker pool can't
    drain them all before stop_check trips."""
    import threading
    library, staging, _transfer = tmpdirs
    db = _FakeDB()
    _setup_album(db, deezer_id='dz-1', tracks=[
        (f't{i}', i, f'Track {i}', _make_audio_file(library, f't{i}.flac'))
        for i in range(1, 11)
    ])

    monkeypatch.setattr(library_reorganize, 'get_primary_source', lambda: 'deezer')
    monkeypatch.setattr(library_reorganize, 'get_source_priority', lambda p: [p])
    monkeypatch.setattr(library_reorganize, 'get_album_for_source',
                        lambda *a: {'id': 'dz-1', 'name': 'A'})
    monkeypatch.setattr(
        library_reorganize, 'get_album_tracks_for_source',
        lambda *a: {'items': [
            {'id': f'a{i}', 'name': f'Track {i}', 'track_number': i}
            for i in range(1, 11)
        ]},
    )

    pp_count = [0]
    pp_lock = threading.Lock()

    def pp(key, ctx, fp):
        with pp_lock:
            pp_count[0] += 1
        ctx['_final_processed_path'] = fp
        with open(fp, 'wb') as f:
            f.write(b'fake-final')

    stop = [False]
    def check_stop():
        with pp_lock:
            if pp_count[0] >= 2:
                stop[0] = True
        return stop[0]

    library_reorganize.reorganize_album(
        album_id='alb-1', db=db, staging_root=str(staging),
        resolve_file_path_fn=lambda p: p, post_process_fn=pp,
        stop_check=check_stop,
    )

    # Some tracks ran (the ones already in flight when stop tripped),
    # but not ALL 10 — the stop_check cut off the unstarted ones.
    assert pp_count[0] < 10
    assert pp_count[0] >= 2
