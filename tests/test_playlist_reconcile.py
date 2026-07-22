"""Extreme battery for the playlist sync-editor reconcile (#768).

Covers: the reported YouTube failure (Bug A — "Artist - Title" source matching
its clean server copy instead of showing unmatched + orphan extra), the
source_track_id echo (Bug B), and parity with the original three-pass behavior
(override → exact → fuzzy → extra), plus duplicate-server-track handling.
"""

from __future__ import annotations

from core.sync.playlist_reconcile import compute_order_status, norm_title, reconcile_playlist


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


# ── order status: server playlist accurate-but-out-of-order detection ─────────
# The editor renders the server column in SOURCE order, so a reordered-but-same-
# membership playlist used to read "in sync" when the real Navidrome order differed.
# compute_order_status surfaces that drift (one-way: source order is truth).

def test_reconcile_attaches_server_index_to_matched():
    source = [_src("Yellow", "Coldplay", "s1")]
    server = [_svr("Filler", "X", "nv0"), _svr("Yellow", "Coldplay", "nv1")]
    combined = reconcile_playlist(source, server)
    matched = [c for c in combined if c["match_status"] == "matched"][0]
    assert matched["server_index"] == 1            # Yellow is at server position 1


def test_in_order_when_server_matches_source_sequence():
    titles = ["Mandinka", "Real Love Baby", "Liquid Indian", "Heaven or Las Vegas", "hospital beach"]
    source = [_src(t, "A", f"s{i}") for i, t in enumerate(titles)]
    server = [_svr(t, "A", f"nv{i}") for i, t in enumerate(titles)]   # same order
    status = compute_order_status(reconcile_playlist(source, server))
    assert status == {"matched": 5, "in_order": True, "out_of_order": False}


def test_out_of_order_reproduces_real_love_baby_case():
    # Source (Spotify): Real Love Baby at position 2. Server (Navidrome): still last.
    src_titles = ["Mandinka", "Real Love Baby", "Liquid Indian", "Heaven or Las Vegas", "hospital beach"]
    svr_titles = ["Mandinka", "Liquid Indian", "Heaven or Las Vegas", "hospital beach", "Real Love Baby"]
    source = [_src(t, "A", f"s{i}") for i, t in enumerate(src_titles)]
    server = [_svr(t, "A", f"nv{i}") for i, t in enumerate(svr_titles)]
    status = compute_order_status(reconcile_playlist(source, server))
    assert status["matched"] == 5
    assert status["out_of_order"] is True          # the bug: looked synced, wasn't


def test_missing_tracks_do_not_false_flag_out_of_order():
    # 2 tracks missing on the server, but the present ones are in the right relative
    # order -> NOT out of order (membership is a separate axis).
    source = [_src(t, "A", f"s{i}") for i, t in enumerate(["one", "two", "three", "four"])]
    server = [_svr("one", "A", "nv0"), _svr("three", "A", "nv1")]   # two/four missing, order ok
    status = compute_order_status(reconcile_playlist(source, server))
    assert status["matched"] == 2
    assert status["out_of_order"] is False


def test_missing_and_shuffled_still_flags_out_of_order():
    source = [_src(t, "A", f"s{i}") for i, t in enumerate(["one", "two", "three", "four"])]
    server = [_svr("three", "A", "nv0"), _svr("one", "A", "nv1")]   # present pair is reversed
    status = compute_order_status(reconcile_playlist(source, server))
    assert status["matched"] == 2
    assert status["out_of_order"] is True


def test_extras_ignored_for_order():
    # An extra server track (not in source) must not affect the order verdict.
    source = [_src("a", "A", "s0"), _src("b", "A", "s1")]
    server = [_svr("a", "A", "nv0"), _svr("zzz extra", "A", "nv1"), _svr("b", "A", "nv2")]
    status = compute_order_status(reconcile_playlist(source, server))
    assert status["matched"] == 2 and status["out_of_order"] is False


def test_fewer_than_two_matches_never_out_of_order():
    assert compute_order_status([])["out_of_order"] is False
    one = reconcile_playlist([_src("a", "A", "s0")], [_svr("a", "A", "nv0")])
    assert compute_order_status(one)["out_of_order"] is False


# ── #1005: the fuzzy pass got fast — the scores must not have changed ────────

def test_fast_fuzzy_pass_scores_identically_to_the_naive_matcher():
    """The optimized pass 2 (prebuilt matchers + quick_ratio upper-bound gates)
    must pair EXACTLY like the old per-pair SequenceMatcher loop. Brute-force
    reference computed here; any gate that could drop a >=threshold pair fails."""
    import random
    from difflib import SequenceMatcher

    from core.sync.playlist_reconcile import (
        _FUZZY_THRESHOLD, canonical_source_track, norm_title, reconcile_playlist,
    )

    rng = random.Random(1005)
    words = ['love', 'night', 'fire', 'rain', 'gold', 'heart', 'run', 'blue',
             'star', 'wild', 'echo', 'ghost', 'city', 'road', 'home', 'light']
    def title():
        return ' '.join(rng.sample(words, rng.randint(2, 4))).title()

    sources, servers = [], []
    for i in range(120):
        t, a = title(), f'Artist {rng.randint(1, 30)}'
        sources.append({'name': t, 'artist': a, 'source_track_id': f's{i}'})
        r = rng.random()
        if r < 0.5:
            servers.append({'id': f'v{i}', 'title': t, 'artist': a})               # exact
        elif r < 0.75:
            servers.append({'id': f'v{i}', 'title': t + ' x', 'artist': a})        # fuzzy-ish
        # else: missing on server
    for j in range(20):
        servers.append({'id': f'x{j}', 'title': title(), 'artist': 'Other'})       # extras
    rng.shuffle(servers)

    combined = reconcile_playlist(sources, servers)

    # brute-force reference for pass 2 decisions, replayed over the SAME greedy state
    def naive_best(src_entry, canon_artist, used):
        canon_t, _ = canonical_source_track(src_entry['name'], src_entry['artist'])
        src_key = f"{canon_artist} {norm_title(canon_t)}".strip().lower()
        best, best_j = 0.0, -1
        for j, svr in enumerate(servers):
            if j in used:
                continue
            svr_key = f"{svr.get('artist', '')} {norm_title(svr.get('title', ''))}".strip().lower()
            score = SequenceMatcher(None, src_key, svr_key).ratio()
            if score > best and score >= _FUZZY_THRESHOLD:
                best, best_j = score, j
        return best, best_j

    # replay: walk the combined output and verify every fuzzy pairing (confidence
    # < 1.0) and every miss agrees with the naive matcher given the same used-set
    used = {e['server_index'] for e in combined
            if e['match_status'] == 'matched' and e['confidence'] >= 1.0}
    fuzzy_rows = [e for e in combined
                  if e['source_track'] and e['confidence'] < 1.0]
    assert fuzzy_rows, "test data produced no fuzzy candidates — regenerate"
    for e in sorted(fuzzy_rows, key=lambda x: x['source_track']['position']):
        canon_t, canon_a = canonical_source_track(
            e['source_track']['name'], e['source_track']['artist'])
        score, j = naive_best(e['source_track'], canon_a or e['source_track']['artist'], used)
        if e['match_status'] == 'matched':
            assert j == e['server_index'], (e['source_track']['name'], j, e['server_index'])
            assert abs(score - e['confidence']) < 5e-4
            used.add(j)
        else:
            assert j == -1, f"optimized pass missed a naive match: {e['source_track']['name']} -> {j}"
