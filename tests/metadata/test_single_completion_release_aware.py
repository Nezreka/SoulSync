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
    assert result["confidence"] >= 0.9


def test_kind_gate_rejects_known_album_row(tmp_path):
    """Pins the kind gate's rejection branch: a same-year, same-title row
    with a KNOWN album kind and only 2 tracks (so the count guard cannot
    fire) must still be rejected for a single card. Fails if the gate is
    deleted — the row's title track would then credit the single."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 2,
            "record_type": "album",
            "deezer_id": None,
            "tracks": ["Ocean Avenue", "B-Side Thing"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0
    assert result["found_in_db"] is False


def test_id_proof_rescues_single_after_gate_kill(tmp_path, caplog):
    """M1: same-year reissue album + single, album inserted first so the
    fuzzy lookup can return the album row (confidence tie); the kind gate
    kills it, but the card's Deezer id equals the single row's stored id —
    the rescue must re-run and credit the single instead of missing."""
    db, candidates, tracks = _build_library(tmp_path, [
        _album_spec(year=2024, deezer_id="DZ-ALBUM-1"),
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": "DZ-SINGLE-1",
            "tracks": ["Ocean Avenue"],
        },
    ])
    with caplog.at_level("DEBUG", logger="soulsync"):
        result = _check(db, _single_card(), candidates, tracks)
    # Pin the premise: the test only exercises the rescue re-run if the
    # fuzzy lookup actually returned the album row and the gate killed it.
    # (If the tie ever breaks the other way, this fails loudly instead of
    # passing as silent theater.)
    assert any("not the single" in m for m in caplog.messages)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1
    assert result["found_in_db"] is True


def test_ep_branch_single_not_credited_by_album_row(tmp_path):
    """M3: the EP branch (2-track single card) gets the same gates — a
    same-year 2-track known-album row (count guard cannot fire) must not
    credit the single. Fails if the EP kind gate is deleted."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 2,
            "record_type": "album",
            "deezer_id": None,
            "tracks": ["Ocean Avenue", "B-Side Thing"],
        },
    ])
    card = _single_card(id="DZ-SINGLE-2", total_tracks=2)
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0
    assert result["found_in_db"] is False


def test_single_suffixed_row_matches_single_card(tmp_path):
    """M5: a library row titled 'Ocean Avenue (Single)' is the single
    release — the strict title cleaner strips the kind marker so the
    single card still resolves instead of flipping owned -> missing."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue (Single)",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": None,
            "tracks": ["Ocean Avenue"],
        },
    ])
    result = _check(db, _single_card(), candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1


def _single_row_library(tmp_path):
    """Library holds only the 1-track '(Single)'-titled single row."""
    return _build_library(tmp_path, [
        {
            "title": "Ocean Avenue (Single)",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": None,
            "tracks": ["Ocean Avenue"],
        },
    ])


def test_album_card_does_not_match_single_suffixed_row(tmp_path):
    """R3: the (Single) stripping is single-card-only — an album card must
    NOT resolve a '(Single)'-titled row (missing, not partial)."""
    from core.metadata.completion import check_album_completion
    db, candidates, tracks = _single_row_library(tmp_path)
    card = {"id": "DZ-ALBUM-1", "name": "Ocean Avenue", "total_tracks": 13,
            "album_type": "album", "year": 2024}
    result = check_album_completion(
        db, card, ARTIST, source_override="deezer", source_chain=["deezer"],
        candidate_albums=candidates, candidate_tracks=tracks)
    assert result["status"] == "missing"


def test_ep_card_does_not_match_single_row(tmp_path):
    """R3: EP cards don't strip either — a 5-track EP card vs the 1-track
    single row stays missing (was partial with the shared cleaner)."""
    db, candidates, tracks = _single_row_library(tmp_path)
    card = _single_card(id="DZ-EP-1", total_tracks=5, album_type="ep")
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "missing"


def test_one_track_ep_card_does_not_match_single_row(tmp_path):
    """R3 sharpest edge: a 1-track EP card vs the single row was false
    'completed' with the shared cleaner — must stay missing."""
    db, candidates, tracks = _single_row_library(tmp_path)
    card = _single_card(id="DZ-EP-1", total_tracks=1, album_type="ep")
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "missing"


def test_ep_branch_unknown_card_count_stays_lenient(tmp_path):
    """M-C: when the card's own track count is unknown (0), the EP count
    guard stays lenient — a genuinely-owned 4-track row still credits
    instead of missing."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 4,
            "record_type": None,
            "deezer_id": None,
            "tracks": ["Ocean Avenue", "B2", "B3", "B4"],
        },
    ])
    card = _single_card(total_tracks=0)
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "completed"


def test_single_card_with_single_suffix_matches_plain_row(tmp_path):
    """Major B (round 3): the card direction. A card titled
    'Ocean Avenue (Single)' must match the library's plain-titled single
    row — the release lookup stripped the marker, but the track check
    didn't, vetoing the 1.0-confidence release match. The old code
    returned missing here."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 1,
            "record_type": "single",
            "deezer_id": None,
            "tracks": ["Ocean Avenue"],
        },
    ])
    card = _single_card(name="Ocean Avenue (Single)")
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "completed"
    assert result["owned_tracks"] == 1


def test_ep_branch_unknown_card_count_rejects_album_shaped_row(tmp_path):
    """Major A (round 3): an unknown-count single card plus an unenriched
    13-track album row must stay missing. The old code skipped the count
    guard for unknown card sizes, and the 0.6-edition completeness rule
    then blessed the album row as the single — the reported symptom, one
    branch over."""
    db, candidates, tracks = _build_library(
        tmp_path, [_album_spec(deezer_id=None, record_type=None)])
    card = _single_card(total_tracks=0)
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "missing"
    assert result["owned_tracks"] == 0


def test_ep_branch_unknown_card_count_trusts_known_single_row(tmp_path):
    """Major A boundary: a 13-track row positively known as a single (the
    #1289 13-track-single shape) is still trusted when the card count is
    unknown — the album-shaped rejection only fires on unknown kinds."""
    db, candidates, tracks = _build_library(tmp_path, [
        {
            "title": "Ocean Avenue",
            "year": 2024,
            "track_count": 13,
            "record_type": "single",
            "deezer_id": None,
            "tracks": ["Ocean Avenue"] + [f"B{i}" for i in range(2, 14)],
        },
    ])
    card = _single_card(total_tracks=0)
    result = _check(db, card, candidates, tracks)
    assert result["status"] == "completed"


def _spotify_ep_library(tmp_path, record_type):
    # spotify files eps under album_type 'single'; deezer/itunes enrichment
    # writes 'ep' for the same release. record_type is fill-only, so either
    # can be on the row.
    return _build_library(tmp_path, [{
        "title": "Southern Air B-Sides",
        "year": 2012,
        "track_count": 5,
        "record_type": record_type,
        "deezer_id": None,
        "tracks": ["Song A", "Song B", "Song C", "Song D", "Song E"],
    }])


def _spotify_ep_card():
    return _single_card(id="SP-EP-1", name="Southern Air B-Sides",
                        total_tracks=5, album_type="single", year=2012)


def test_spotify_single_typed_ep_owned_when_row_says_ep(tmp_path):
    """an owned ep must stay owned when the card says 'single' (spotify) and
    the row says 'ep' (deezer/itunes enrichment)."""
    db, candidates, tracks = _spotify_ep_library(tmp_path, "ep")
    result = _check(db, _spotify_ep_card(), candidates, tracks, source="spotify")
    assert result["status"] == "completed"


def test_spotify_single_typed_ep_download_sees_owned_tracks(tmp_path):
    """download discography agrees with the page: the ep's tracks count as
    owned, so they are not grabbed again."""
    from core.metadata.discography_filters import owned_release_tracks

    db, candidates, tracks = _spotify_ep_library(tmp_path, "ep")
    owned = owned_release_tracks(
        db, "Southern Air B-Sides", ARTIST, release_date="2012",
        expected_tracks=5, server_source="test",
        candidate_albums=candidates, candidate_tracks=tracks,
        metadata_source="spotify", card_source_id="SP-EP-1",
        card_album_type="single")
    assert len(owned) == 5
