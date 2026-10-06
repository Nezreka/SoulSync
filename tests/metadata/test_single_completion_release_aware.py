"""SeadogsBooty (Oct 2026): artist-page single ownership must be release-aware.

The 1-track single path in ``check_single_completion`` used to call
``db.check_track_exists(title, artist)`` with no release context, so a
single whose song is also on an album showed OWNED as soon as the album
track existed in the library — while the download analysis
(``owned_release_tracks``, release-level) correctly reported it missing.
The artist page and the Begin Analysis modal disagreed.

The fix: the single path now resolves the library RELEASE first (the same
strict + year-gated release lookup the EP path and the download analysis
use), then applies two single-specific guards, then credits the single only
if its track is on THAT release:

- release-kind gate: a single card is never satisfied by a known album-kind
  library row (the #1289 Deezer reissue-date exemption can skip the year
  gate for a same-titled album);
- track-count guard: a 1-track card is never the same release as a 4+ track
  row (codebase single/EP/album cutoffs: <=3 single, <=6 EP).

Unknown kinds/counts stay lenient, and the #1071 id-proof rescue still
applies. All hermetic: temp DB, no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.metadata.completion import check_single_completion
from database.music_database import MusicDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent

ARTIST = "Yellowcard"
ALBUM_TRACKS = [
    "Ocean Avenue", "Back Home", "One Year, Six Months", "Believe",
    "Inside Out", "Twentythree", "Miles Apart", "Life Of A Salesman",
    "View From Heaven", "Empty Apartment", "Only One", "Way Away",
    "Twentythree (Acoustic)",
]


def _build_library(tmp_path, albums):
    """Build a temp library from album specs.

    Each spec: dict(title, year, track_count, record_type, deezer_id,
    tracks=[titles]). Returns (db, candidate_albums, candidate_tracks).
    """
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO artists (id, name, server_source) VALUES ('AR1', ?, 'test')",
            (ARTIST,))
        for i, spec in enumerate(albums):
            aid = f"AL{i}"
            cols = ["id", "artist_id", "title", "year", "track_count", "server_source"]
            vals = [aid, "AR1", spec["title"], spec["year"], spec["track_count"], "test"]
            if spec.get("record_type") is not None:
                cols.append("record_type")
                vals.append(spec["record_type"])
            if spec.get("deezer_id") is not None:
                cols.append("deezer_id")
                vals.append(spec["deezer_id"])
            conn.execute(
                f"INSERT INTO albums ({', '.join(cols)}) VALUES ({', '.join('?' * len(vals))})",
                vals)
            for j, title in enumerate(spec["tracks"]):
                conn.execute(
                    "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, server_source) "
                    "VALUES (?, ?, 'AR1', ?, ?, ?, 'test')",
                    (f"T{i}_{j}", aid, title, j + 1, f"/m/{aid}/t{j}.mp3"))
        conn.commit()
    candidates = db.get_candidate_albums_for_artist(ARTIST, server_source="test")
    tracks = db.get_candidate_tracks_for_albums([a.id for a in candidates]) if candidates else []
    return db, candidates, tracks


def _check(db, card, candidates, tracks, source="deezer"):
    return check_single_completion(
        db, card, ARTIST,
        source_override=source,
        source_chain=[source],
        candidate_albums=candidates,
        candidate_tracks=tracks,
    )


def _single_card(**kw):
    card = {
        "id": "DZ-SINGLE-1",
        "name": "Ocean Avenue",
        "total_tracks": 1,
        "album_type": "single",
        "year": 2024,
    }
    card.update(kw)
    return card


def _album_spec(**kw):
    spec = {
        "title": "Ocean Avenue",
        "year": 2003,
        "track_count": 13,
        "record_type": "album",
        "deezer_id": "DZ-ALBUM-1",
        "tracks": ALBUM_TRACKS,
    }
    spec.update(kw)
    return spec


def test_single_not_credited_by_album_track(tmp_path):
    """The reported bug: library holds only the 2003 album (enriched,
    conflicting Deezer id kills the reissue-date exemption); the 2024 single
    card must stay missing. The old track-wide check returned completed."""
    db, candidates, tracks = _build_library(tmp_path, [_album_spec()])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0
    assert result["found_in_db"] is False


def test_single_not_credited_when_year_gate_exempt(tmp_path):
    """Deezer reissue-date exemption fires (no stored id to conflict), so the
    year gate is skipped and the album fuzzy-matches — the release-kind gate
    must still reject the known album-kind row."""
    db, candidates, tracks = _build_library(
        tmp_path, [_album_spec(deezer_id=None)])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0


def test_single_not_credited_by_track_count(tmp_path):
    """Same year, unknown kind: the year gate and kind gate both pass, but a
    1-track card is never the same release as a 13-track row."""
    db, candidates, tracks = _build_library(
        tmp_path, [_album_spec(year=2024, record_type=None, deezer_id=None)])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0


def test_genuinely_owned_single_stays_owned(tmp_path):
    """The single release itself is in the library: still completed, with
    the release track's formats."""
    db, candidates, tracks = _build_library(tmp_path, [
        _album_spec(),
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": "DZ-SINGLE-1",
            "tracks": ["Ocean Avenue"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1
    assert result["found_in_db"] is True
    assert result["formats"] == ["MP3"]


def test_owned_single_unknown_kind_stays_owned(tmp_path):
    """Unknown record_type stays lenient: an unenriched single row still
    credits the single."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 1,
            "record_type": None,
            "deezer_id": None,
            "tracks": ["Ocean Avenue"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1


def test_id_proof_rescues_single_despite_year_gate(tmp_path):
    """#1071 rescue on the single path: two same-title candidates kill the
    Deezer exemption, the year gate rejects both fuzzy matches, but the
    card's Deezer id equals the single row's stored id — certain proof of
    the same release."""
    db, candidates, tracks = _build_library(tmp_path, [
        _album_spec(deezer_id=None),
        {
            "title": "Ocean Avenue",
            "year": 2000,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": "DZ-SINGLE-1",
            "tracks": ["Ocean Avenue"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1


def test_album_and_single_both_owned_prefers_single(tmp_path):
    """Library holds both releases: the single card must resolve to the
    single release, not get shadowed by the album's title track."""
    db, candidates, tracks = _build_library(tmp_path, [
        _album_spec(),
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": "DZ-SINGLE-1",
            "tracks": ["Ocean Avenue"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1
    assert result["confidence"] == 1.0
