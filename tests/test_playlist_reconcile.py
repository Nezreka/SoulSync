"""Extreme battery for the playlist sync-editor reconcile (#768).

Covers: the reported YouTube failure (Bug A — "Artist - Title" source matching
its clean server copy instead of showing unmatched + orphan extra), the
source_track_id echo (Bug B), and parity with the original three-pass behavior
(override → exact → fuzzy → extra), plus duplicate-server-track handling.
"""

from __future__ import annotations

from core.sync.playlist_reconcile import norm_title, reconcile_playlist


def _src(name, artist, sid="", **kw):
    return {"name": name, "artist": artist, "source_track_id": sid, **kw}


def _svr(title, artist, tid):
    return {"title": title, "artist": artist, "id": tid, "ratingKey": tid}


def _status(combined):
    return [(c["match_status"],
             (c["source_track"] or {}).get("name"),
             (c["server_track"] or {}).get("title")) for c in combined]


# ── Bug A: the reported YouTube case ──────────────────────────────────────

def test_youtube_artist_title_source_matches_clean_server_track():
    source = [_src("Arctic Monkeys - Do I Wanna Know?", "Official Arctic Monkeys", "sp1")]
    server = [_svr("Do I Wanna Know?", "Arctic Monkeys", "nv72")]
    combined = reconcile_playlist(source, server)
    assert len(combined) == 1
    assert combined[0]["match_status"] == "matched"
    assert combined[0]["server_track"]["id"] == "nv72"
    # ...and the server track is NOT left as an orphan extra.
    assert not any(c["match_status"] == "extra" for c in combined)


def test_youtube_match_does_not_leave_unmatched_or_extra():
    # Before the fix this produced one 'missing' + one 'extra'.
    source = [_src("The Killers - Mr. Brightside", "The KillersVEVO", "sp2")]
    server = [_svr("Mr. Brightside", "The Killers", "nv5")]
    statuses = [c["match_status"] for c in reconcile_playlist(source, server)]
    assert statuses == ["matched"]


# ── Bug B: source_track_id is echoed back ─────────────────────────────────

def test_source_track_id_present_on_matched_entry():
    source = [_src("Do I Wanna Know?", "Arctic Monkeys", "spotify:track:abc")]
    server = [_svr("Do I Wanna Know?", "Arctic Monkeys", "nv1")]
    combined = reconcile_playlist(source, server)
    assert combined[0]["source_track"]["source_track_id"] == "spotify:track:abc"


def test_source_track_id_present_on_missing_entry():
    # A genuinely-missing source must still carry its id so it can be
    # manually matched and persisted (the #768 manual-match loop).
    source = [_src("Some Obscure B-Side", "Some Artist", "spotify:track:xyz")]
    server = [_svr("Completely Different", "Other Artist", "nv9")]
    combined = reconcile_playlist(source, server)
    missing = [c for c in combined if c["match_status"] == "missing"]
    assert missing and missing[0]["source_track"]["source_track_id"] == "spotify:track:xyz"


# ── parity: override / exact / fuzzy / extra ──────────────────────────────

def test_override_pair_wins_first():
    source = [_src("Anything", "Whoever", "s1")]
    server = [_svr("Totally Different Title", "Nobody", "nvX")]
    combined = reconcile_playlist(source, server, override_pairs={0: 0})
    assert combined[0]["match_status"] == "matched"
    assert combined[0]["confidence"] == 1.0
    assert combined[0].get("override") is True


def test_exact_normalized_match_strips_feat():
    source = [_src("Stay (feat. Justin Bieber)", "The Kid LAROI", "s1")]
    server = [_svr("Stay", "The Kid LAROI", "nv1")]
    assert reconcile_playlist(source, server)[0]["match_status"] == "matched"


def test_fuzzy_match_above_threshold():
    source = [_src("Mr Brightside", "The Killers", "s1")]
    server = [_svr("Mr. Brightside", "The Killers", "nv1")]
    c = reconcile_playlist(source, server)[0]
    assert c["match_status"] == "matched"
    assert c["confidence"] >= 0.75


def test_truly_absent_track_is_missing_and_unrelated_server_is_extra():
    source = [_src("Nonexistent Song", "Ghost Artist", "s1")]
    server = [_svr("Yellow", "Coldplay", "nv1")]
    statuses = sorted(c["match_status"] for c in reconcile_playlist(source, server))
    assert statuses == ["extra", "missing"]


def test_each_server_track_claimed_once_no_double_match():
    # Two identical source rows must not both claim the single server track.
    source = [_src("Yellow", "Coldplay", "s1"), _src("Yellow", "Coldplay", "s2")]
    server = [_svr("Yellow", "Coldplay", "nv1")]
    combined = reconcile_playlist(source, server)
    matched = [c for c in combined if c["match_status"] == "matched"]
    missing = [c for c in combined if c["match_status"] == "missing"]
    assert len(matched) == 1 and len(missing) == 1


def test_duplicate_server_tracks_one_matched_one_extra():
    # The #768 duplicate scenario: two copies of the same track on the server.
    source = [_src("Do I Wanna Know?", "Arctic Monkeys", "s1")]
    server = [_svr("Do I Wanna Know?", "Arctic Monkeys", "nv72"),
              _svr("Do I Wanna Know?", "Arctic Monkeys", "nv73")]
    combined = reconcile_playlist(source, server)
    assert sorted(c["match_status"] for c in combined) == ["extra", "matched"]


# ── #766: source borrows the matched server cover ─────────────────────────

def _svr_art(title, artist, tid, thumb):
    return {"title": title, "artist": artist, "id": tid, "ratingKey": tid, "thumb": thumb}


def test_artless_source_borrows_matched_server_cover():
    # YouTube-style source row, no art of its own, matched to a server track
    # that has a cover -> source side borrows it.
    source = [_src("Arctic Monkeys - Do I Wanna Know?", "Official Arctic Monkeys", "s1")]
    server = [_svr_art("Do I Wanna Know?", "Arctic Monkeys", "nv1", "/api/navidrome/cover/al42")]
    combined = reconcile_playlist(source, server)
    assert combined[0]["match_status"] == "matched"
    assert combined[0]["source_track"]["image_url"] == "/api/navidrome/cover/al42"


def test_source_keeps_its_own_art_when_present():
    # A Spotify-style source row with its own CDN art must NOT be overwritten.
    source = [_src("Do I Wanna Know?", "Arctic Monkeys", "s1", image_url="https://cdn/spotify.jpg")]
    server = [_svr_art("Do I Wanna Know?", "Arctic Monkeys", "nv1", "/api/navidrome/cover/al42")]
    combined = reconcile_playlist(source, server)
    assert combined[0]["source_track"]["image_url"] == "https://cdn/spotify.jpg"


def test_unmatched_source_has_no_cover_to_borrow():
    source = [_src("Totally Absent Song", "Ghost", "s1")]
    server = [_svr_art("Something Else", "Nobody", "nv1", "/api/navidrome/cover/al99")]
    combined = reconcile_playlist(source, server)
    missing = [c for c in combined if c["match_status"] == "missing"]
    assert missing and not missing[0]["source_track"]["image_url"]


def test_borrow_skipped_when_server_track_has_no_thumb():
    source = [_src("Do I Wanna Know?", "Arctic Monkeys", "s1")]
    server = [_svr("Do I Wanna Know?", "Arctic Monkeys", "nv1")]  # no thumb
    combined = reconcile_playlist(source, server)
    assert combined[0]["match_status"] == "matched"
    assert not combined[0]["source_track"]["image_url"]


def test_norm_title_helper_parity():
    assert norm_title("Stay (feat. X)") == "stay"
    assert norm_title("Song (2019 Remaster)") == "song"
    assert norm_title("Album (Deluxe Edition)") == "album"
