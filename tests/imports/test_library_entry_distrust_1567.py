"""Round 4 of #1567 (kevin2xk): record_soulsync_library_entry is the only
INSERT INTO albums in core/ — it wrote library rows from the batch album
context WITHOUT applying the distrust verdict that extract_source_metadata
computes. The library album card showed the stranger's year/track_count/cover
and grouped by the stranger's release id.

Covers:
- distrusted batch ctx: albums.year/track_count/thumb_url and
  artists.thumb_url come from the track's own provider album (the
  _track_own_provider_album ground truth), never the batch's.
- distrusted batch ctx: album grouping + the musicbrainz release-id fill use
  the per-track release id (album_info), not the batch ctx's.
- distrusted with an empty own provider album: NULL/0/empty left for
  enrichment (fill-only writers never clobber).
- round 4b: distrusted batch ctx no longer stamps the batch's provider
  album id (spotify_album_id) or its summed duration (albums.duration)
  — the track's own provider album id / own duration are used instead,
  NULL when the track's own album has no usable id.
- trusted ctx (the common case): byte-identical old behavior — batch values
  written, batch release id used for grouping.
- threading: extract_source_metadata stashes the verdict + own provider
  album on the shared import context dict.
"""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.imports import side_effects


STRANGER_ART = "https://stranger.example/nickelback-art.jpg"
OWN_ART = "https://own.example/ff-art.jpg"
STRANGER_RELEASE_ID = "stranger-mb-release-id"
OWN_RELEASE_ID = "own-mb-release-id"
# Round 4b: the two leftover poison-shaped fields — a bogus-but-plausible
# provider album id and a summed duration living on the batch album ctx.
STRANGER_ALBUM_ID = "sp-stranger-album"
STRANGER_ALBUM_DURATION_MS = 660000


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        return self._conn


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE artists (
            id TEXT PRIMARY KEY, name TEXT, genres TEXT, thumb_url TEXT,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_artist_id TEXT)"""
    )
    conn.execute(
        """CREATE TABLE albums (
            id TEXT PRIMARY KEY, artist_id TEXT, title TEXT, year INTEGER,
            thumb_url TEXT, genres TEXT, track_count INTEGER, duration INTEGER,
            server_source TEXT, created_at TEXT, updated_at TEXT,
            spotify_album_id TEXT, deezer_id TEXT,
            musicbrainz_release_id TEXT,
            musicbrainz_match_status TEXT, record_type TEXT)"""
    )
    conn.execute(
        """CREATE TABLE tracks (
            id TEXT PRIMARY KEY, album_id TEXT, artist_id TEXT, title TEXT,
            track_number INTEGER, duration INTEGER, file_path TEXT,
            bitrate INTEGER, file_size INTEGER, track_artist TEXT,
            musicbrainz_recording_id TEXT, recording_disambiguation TEXT,
            isrc TEXT, quality_profile_id INTEGER, server_source TEXT,
            created_at TEXT, updated_at TEXT, spotify_track_id TEXT)"""
    )
    return conn


def _own_provider_album(**overrides):
    album = {
        "name": "The Colour and the Shape",
        "id": "sp-ff-album",
        "total_tracks": 13,
        "release_date": "1997-05-20",
        "image_url": OWN_ART,
    }
    album.update(overrides)
    return album


def _r2_context(tmp_path, distrusted, own_album, source="spotify"):
    """The R2-blocker call shape: batch album ctx poisoned first-row-wins
    (bogus "Music"/Nickelback/2001, stranger's art + release id); the track
    is Foo Fighters' own with its own provider album. album_info already
    carries the track's own album name (rounds 1-3 fixed the tags/filing)."""
    final_path = tmp_path / "everlong.flac"
    final_path.write_bytes(b"audio")
    context = {
        "source": source,
        "artist": {"id": "sp-ff", "name": "Foo Fighters"},
        "album": {
            "id": "_name_Music",  # name-fallback placeholder: no real provider id
            "name": "Music",
            "release_date": "2001-05-15",
            "total_tracks": 10,
            "image_url": STRANGER_ART,
            "musicbrainz_release_id": STRANGER_RELEASE_ID,
        },
        "track_info": {
            "id": "sp-track-everlong",
            "name": "Everlong",
            "track_number": 1,
            "duration_ms": 250000,
            "artists": [{"name": "Foo Fighters"}],
        },
        "original_search_result": {
            "title": "Everlong",
            "artists": [{"name": "Foo Fighters"}],
        },
        "_final_processed_path": str(final_path),
    }
    if distrusted is not None:
        # Exactly what extract_source_metadata stashes on the shared context
        # dict (verified by test_extract_stashes_distrust_verdict below).
        context["_batch_album_distrusted"] = bool(distrusted)
        context["_track_own_provider_album"] = dict(own_album)
    artist_context = {"name": "Foo Fighters", "genres": []}
    album_info = {
        "album_name": "The Colour and the Shape",
        "musicbrainz_release_id": OWN_RELEASE_ID,
        "is_album": False,
        "track_number": 1,
    }
    return context, artist_context, album_info


def _record(context, artist_context, album_info, conn):
    with patch.object(side_effects, "get_database",
                      return_value=_FakeDB(conn)), \
         patch.object(side_effects, "_get_config_manager",
                      return_value=SimpleNamespace(
                          get_active_media_server=lambda: "soulsync")):
        side_effects.record_soulsync_library_entry(
            context, artist_context, album_info)


def test_distrusted_writes_track_own_album_values(tmp_path):
    """The reviewer's repro: distrusted batch ctx must not stamp the
    stranger's year/count/cover on the library rows."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    artist_row = dict(conn.execute("SELECT * FROM artists").fetchone())
    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())

    assert artist_row["name"] == "Foo Fighters"
    assert artist_row["thumb_url"] == OWN_ART
    assert artist_row["thumb_url"] != STRANGER_ART

    assert album_row["title"] == "The Colour and the Shape"
    assert album_row["year"] == 1997, "stranger's 2001 leaked into albums.year"
    assert album_row["track_count"] == 13, "stranger's 10 leaked into track_count"
    assert album_row["thumb_url"] == OWN_ART
    assert album_row["thumb_url"] != STRANGER_ART


def test_distrusted_fill_path_never_takes_stranger_art(tmp_path):
    """_fill_empty_columns on an EXISTING artist row with empty thumb must
    take the track's own art, not the stranger's (the Foo Fighters row in
    the reviewer's repro)."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO artists (id, name, genres, thumb_url, server_source) "
        "VALUES ('ff-id', 'Foo Fighters', '', '', 'soulsync')"
    )
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    artist_row = dict(
        conn.execute("SELECT * FROM artists WHERE id = 'ff-id'").fetchone())
    assert artist_row["thumb_url"] == OWN_ART
    assert artist_row["thumb_url"] != STRANGER_ART


def test_distrusted_groups_by_per_track_release_id(tmp_path):
    """Grouping + the release-id fill use the per-track album_info id, not
    the batch ctx's stranger id. A row already carrying the stranger's id
    must NOT be joined."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, year, thumb_url, genres,"
        " track_count, duration, server_source, musicbrainz_release_id) "
        "VALUES ('stranger-row', 'x', 'The Colour and the Shape', 2001, ?,"
        " '', 10, 0, 'soulsync', ?)",
        (STRANGER_ART, STRANGER_RELEASE_ID),
    )
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    track_row = dict(conn.execute("SELECT * FROM tracks").fetchone())
    assert track_row["album_id"] != "stranger-row", \
        "track joined the stranger's release-id row"
    album_row = dict(conn.execute(
        "SELECT * FROM albums WHERE id = ?", (track_row["album_id"],)).fetchone())
    assert album_row["musicbrainz_release_id"] == OWN_RELEASE_ID
    assert album_row["musicbrainz_release_id"] != STRANGER_RELEASE_ID


def test_distrusted_joins_existing_per_track_release_row(tmp_path):
    """The flip side: a row already carrying the PER-TRACK release id is the
    canonical grouping target and gets joined."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, year, thumb_url, genres,"
        " track_count, duration, server_source, musicbrainz_release_id) "
        "VALUES ('own-row', 'x', 'The Colour and the Shape', 1997, ?,"
        " '', 13, 0, 'soulsync', ?)",
        (OWN_ART, OWN_RELEASE_ID),
    )
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    track_row = dict(conn.execute("SELECT * FROM tracks").fetchone())
    assert track_row["album_id"] == "own-row"


def test_distrusted_empty_own_album_leaves_nulls_for_enrichment(tmp_path):
    """When the track's own provider album carries no date/count/art, the
    row keeps NULL/0/empty for enrichment to fill — never the batch's."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True,
        own_album=_own_provider_album(
            total_tracks=0, release_date="", image_url=""))
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    artist_row = dict(conn.execute("SELECT * FROM artists").fetchone())
    assert album_row["year"] is None
    assert album_row["track_count"] == 0
    assert album_row["thumb_url"] in (None, "")
    assert artist_row["thumb_url"] in (None, "")


def test_trusted_context_writes_batch_values_unchanged(tmp_path):
    """Control: without the verdict the behavior is byte-identical to before
    — the batch ctx's year/count/art/release id are written as always."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=None, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    artist_row = dict(conn.execute("SELECT * FROM artists").fetchone())
    assert album_row["year"] == 2001
    assert album_row["track_count"] == 10
    assert album_row["thumb_url"] == STRANGER_ART
    assert artist_row["thumb_url"] == STRANGER_ART


def test_trusted_context_groups_by_batch_release_id(tmp_path):
    """Control: trusted ctx still groups by the batch ctx's release id."""
    conn = _make_db()
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, year, thumb_url, genres,"
        " track_count, duration, server_source, musicbrainz_release_id) "
        "VALUES ('batch-row', 'x', 'The Colour and the Shape', 2001, ?,"
        " '', 10, 0, 'soulsync', ?)",
        (STRANGER_ART, STRANGER_RELEASE_ID),
    )
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=False, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    track_row = dict(conn.execute("SELECT * FROM tracks").fetchone())
    assert track_row["album_id"] == "batch-row"


# --- round 4b: album source id + total duration ---

def _r2_context_poisoned_batch(tmp_path, distrusted, own_album):
    """The R2 shape plus the two leftover poison fields on the batch ctx: a
    bogus-but-plausible provider album id and a summed duration."""
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=distrusted, own_album=own_album)
    context["album"]["id"] = STRANGER_ALBUM_ID
    context["album"]["duration_ms"] = STRANGER_ALBUM_DURATION_MS
    return context, artist_context, album_info


def test_distrusted_writes_track_own_album_source_id_and_duration(tmp_path):
    """Round 4b: the batch ctx's bogus provider album id and summed duration
    must not reach the library rows — spotify_album_id is the track's own
    album id, and the album card duration is the track's own."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context_poisoned_batch(
        tmp_path, distrusted=True, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["spotify_album_id"] == "sp-ff-album"
    assert album_row["spotify_album_id"] != STRANGER_ALBUM_ID
    assert album_row["duration"] == 250000
    assert album_row["duration"] != STRANGER_ALBUM_DURATION_MS


def test_distrusted_unusable_own_album_id_leaves_null_for_enrichment(tmp_path):
    """Round 4b: when the track's own provider album has no usable provider
    id (pipeline placeholder), the row keeps NULL for enrichment — never
    the batch's bogus id."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context_poisoned_batch(
        tmp_path, distrusted=True,
        own_album=_own_provider_album(id="_name_Music"))
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["spotify_album_id"] in (None, "")
    assert album_row["spotify_album_id"] != STRANGER_ALBUM_ID


def test_distrusted_empty_own_album_id_leaves_null_for_enrichment(tmp_path):
    """Round 4b: same, with an outright empty own-album id."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context_poisoned_batch(
        tmp_path, distrusted=True, own_album=_own_provider_album(id=""))
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["spotify_album_id"] in (None, "")
    assert album_row["spotify_album_id"] != STRANGER_ALBUM_ID


def test_trusted_context_writes_batch_source_id_and_duration_unchanged(tmp_path):
    """Control: without the verdict the behavior is byte-identical to before
    — the batch ctx's provider album id and summed duration are written."""
    conn = _make_db()
    context, artist_context, album_info = _r2_context_poisoned_batch(
        tmp_path, distrusted=None, own_album=_own_provider_album())
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["spotify_album_id"] == STRANGER_ALBUM_ID
    assert album_row["duration"] == STRANGER_ALBUM_DURATION_MS


# --- threading: extract_source_metadata -> import context -> library writer ---

def _cfg():
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: {
        "metadata_enhancement.enabled": True,
        "metadata_enhancement.tags.write_multi_artist": False,
        "metadata_enhancement.tags.feat_in_title": False,
        "metadata_enhancement.tags.artist_separator": ", ",
        "file_organization.collab_artist_mode": "first",
    }.get(key, default)
    return cfg


def _poisoned_extract_context():
    """The #1567 shape: batch album ctx (name "Music", 10 tracks, 2001,
    Nickelback) poisoned first-row-wins with a name-fallback placeholder id;
    the track's own data names Foo Fighters + their own album."""
    return {
        "original_search_result": {
            "title": "Everlong",
            "artist": "Foo Fighters",
            "artists": [{"name": "Foo Fighters"}],
        },
        "album": {
            "id": "_name_Music",
            "name": "Music",
            "total_tracks": 10,
            "release_date": "2001",
            "artists": [{"name": "Nickelback"}],
        },
        "track_info": {
            "_explicit_artist_context": {"id": "wishlist", "name": "Nickelback",
                                         "genres": []},
            "spotify_data": {
                "artists": [{"name": "Foo Fighters"}],
                "album": {
                    "name": "The Colour and the Shape",
                    "id": "sp-ff-album",
                    "total_tracks": 13,
                    "release_date": "1997-05-20",
                    "image_url": OWN_ART,
                },
            },
        },
        "source": "spotify",
    }


def test_extract_stashes_distrust_verdict_on_shared_context():
    """The verdict must reach the library writer via the shared import
    context dict — no recomputation, no signature changes."""
    from core.metadata import source as src
    context = _poisoned_extract_context()
    with patch.object(src, "get_config_manager", return_value=_cfg()):
        src.extract_source_metadata(
            context, {"id": "wishlist", "name": "Nickelback", "genres": []}, {})
    assert context["_batch_album_distrusted"] is True
    own = context["_track_own_provider_album"]
    assert own["name"] == "The Colour and the Shape"
    assert own["total_tracks"] == 13
    assert own["release_date"] == "1997-05-20"
    assert own["image_url"] == OWN_ART


def test_extract_stashes_trusted_verdict_for_genuine_album():
    """A genuine single-artist album stays trusted — the stash says False so
    the library writer keeps the batch values."""
    from core.metadata import source as src
    context = {
        "original_search_result": {"title": "Song", "artist": "Ryoto",
                                   "artists": [{"name": "Ryoto"}]},
        "album": {"name": "Cha-La Head-Cha-La", "total_tracks": 12,
                  "release_date": "2024-01-01",
                  "artists": [{"name": "Ryoto"}]},
        "track_info": {"_explicit_artist_context": {"id": "wishlist",
                                                   "name": "Ryoto",
                                                   "genres": []}},
        "source": "spotify",
    }
    with patch.object(src, "get_config_manager", return_value=_cfg()):
        src.extract_source_metadata(context, {"name": "Ryoto"}, {})
    assert context["_batch_album_distrusted"] is False


# ---------------------------------------------------------------------------
# Round 3 (reviewer 3) finding 3: cross-source own-album id column routing
# ---------------------------------------------------------------------------


def test_distrusted_spotify_own_album_id_never_lands_in_deezer_column(tmp_path):
    """R3F3: a distrusted Deezer import whose track's own provider album id is
    cross-source (Spotify, via spotify_data) must write it to spotify_album_id
    — never into albums.deezer_id. Pre-fix, the Spotify id landed in deezer_id,
    where the Deezer worker's honor_stored_match fails truthy and the
    name-search fallback is skipped forever."""
    conn = _make_db()
    own = _own_provider_album(id="4aawyAB9vmqN3uQ7FjRGT", id_source="spotify")
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=own, source="deezer")
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["spotify_album_id"] == "4aawyAB9vmqN3uQ7FjRGT"
    assert album_row["deezer_id"] in (None, ""), \
        "cross-source Spotify id leaked into albums.deezer_id"


def test_distrusted_same_family_own_album_id_uses_download_source_column(tmp_path):
    """R3F3 control: a distrusted Deezer import whose own album id came from
    the download source's own payload (id_source='search') still lands in
    deezer_id — only cross-source ids are rerouted."""
    conn = _make_db()
    own = _own_provider_album(id="12345678", id_source="search")
    context, artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=own, source="deezer")
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["deezer_id"] == "12345678"
    assert album_row["spotify_album_id"] in (None, "")


# ---------------------------------------------------------------------------
# R4F1: the library DB album identity (artists/albums rows) must be
# quarantined when the batch album context is distrusted — not just the
# year/count/cover. Production bundle-flow shape: artist_context and
# album_info['album_name'] are the STRANGER's.
# ---------------------------------------------------------------------------


def _r4f1_bundle_context(tmp_path, own_album):
    """Production bundle-flow shape: the batch stamped the stranger
    (Nickelback/'Music') on artist_context and album_info; the track is
    Foo Fighters' own."""
    context, _artist_context, album_info = _r2_context(
        tmp_path, distrusted=True, own_album=own_album)
    # The stranger's batch artist context (what the bundle flow passes).
    artist_context = {"id": "sp-nickelback", "name": "Nickelback",
                      "genres": ["post-grunge", "butt-rock"]}
    # The stranger's batch album name (what the bundle flow passes).
    album_info = dict(album_info)
    album_info["album_name"] = "Music"
    album_info["record_type"] = "compilation"
    # What extract_source_metadata stashes (R4F1 adds the display names).
    context["_track_own_artist_display_names"] = ["Foo Fighters"]
    return context, artist_context, album_info


def test_distrusted_library_artist_row_uses_track_own_artist(tmp_path):
    """R4F1: a distrusted batch must not create the library artist/album rows
    under the stranger's name — the track's own artist owns both rows."""
    conn = _make_db()
    context, artist_context, album_info = _r4f1_bundle_context(
        tmp_path, _own_provider_album())
    _record(context, artist_context, album_info, conn)

    artist_rows = [dict(r) for r in conn.execute("SELECT * FROM artists")]
    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())

    assert len(artist_rows) == 1, f"stranger artist row leaked: {artist_rows}"
    assert artist_rows[0]["name"] == "Foo Fighters"
    assert album_row["title"] == "The Colour and the Shape"
    assert album_row["artist_id"] == artist_rows[0]["id"], \
        "album row not joined to the track's own artist row"


def test_distrusted_library_artist_source_id_dropped(tmp_path):
    """R4F1: the batch artist's source id must not be written on the track's
    own artist row (it would mismatch the name) — left for enrichment."""
    conn = _make_db()
    context, artist_context, album_info = _r4f1_bundle_context(
        tmp_path, _own_provider_album())
    # The batch artist's Spotify id is what get_import_source_ids would
    # surface via the stranger's artist_context.
    context["artist"] = {"id": "sp-nickelback", "name": "Nickelback"}
    _record(context, artist_context, album_info, conn)

    artist_row = dict(conn.execute("SELECT * FROM artists").fetchone())
    assert artist_row["name"] == "Foo Fighters"
    assert artist_row["spotify_artist_id"] in (None, ""), \
        "batch artist's id written on the track's own artist row"


def test_distrusted_library_album_name_falls_back_to_track_title(tmp_path):
    """R4F1: when the track's own provider album has no usable name either,
    the album title falls back to the track title — never the stranger's
    batch album name."""
    conn = _make_db()
    context, artist_context, album_info = _r4f1_bundle_context(
        tmp_path, _own_provider_album(name=""))
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["title"] == "Everlong"
    assert album_row["title"] != "Music"


# ---------------------------------------------------------------------------
# NF-1 / NF-2 (reviewer-4 verification): two more poisoned values leaked into
# the library rows — the batch artist_context's genres, and the batch
# album_info/album_ctx's record_type. Both are sticky (fill-only writers).
# ---------------------------------------------------------------------------


def test_distrusted_library_genres_not_from_batch_artist(tmp_path):
    """NF-1: a distrusted batch must not stamp the stranger's genres on the
    track's own artist row — left empty for enrichment."""
    conn = _make_db()
    context, artist_context, album_info = _r4f1_bundle_context(
        tmp_path, _own_provider_album())
    _record(context, artist_context, album_info, conn)

    artist_row = dict(conn.execute("SELECT * FROM artists").fetchone())
    assert artist_row["name"] == "Foo Fighters"
    assert artist_row["genres"] in (None, "", "[]"), \
        f"stranger's genres leaked: {artist_row['genres']}"


def test_distrusted_library_record_type_not_from_batch(tmp_path):
    """NF-2: a distrusted batch must not stamp the stranger's record_type on
    the album row — left NULL for the workers."""
    conn = _make_db()
    context, artist_context, album_info = _r4f1_bundle_context(
        tmp_path, _own_provider_album())
    _record(context, artist_context, album_info, conn)

    album_row = dict(conn.execute("SELECT * FROM albums").fetchone())
    assert album_row["title"] == "The Colour and the Shape"
    assert album_row["record_type"] in (None, ""), \
        f"stranger's record_type leaked: {album_row['record_type']}"
