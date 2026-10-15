"""#1705 — feat.-titled tracks came back with no MusicBrainz ids at all.

Reporter (pm-melo), 65 FLACs: every one of the 5 tracks whose title
carries a featuring credit ended up with no recording/release MBID, and
none of the tracks without one did. Example: "Rather Be
(feat. Jess Glynne)" by Clean Bandit, ISRC GBAHS1300498 —

    No MusicBrainz results for recording 'Rather Be (feat. Jess Glynne)'
    No MusicBrainz results for release 'Rather Be (feat. Jess Glynne)'

Root cause, verified live against MusicBrainz: the featuring segment
is part of the CREDIT, not of the MB title — MB files the recording as
plain "Rather Be" with Jess Glynne in the artist credit. The strict
phrase queries (`recording:"…"`, `release:"…"`) therefore return ZERO
results (live counts: feat. query 0, stripped query 68 recordings / 10
releases), the artist-pinned retry repeats the same phrase, and even a
looser search's candidates die on the raw title-similarity floor
("rather be (feat. jess glynne)" vs "rather be" scores 0.47 < 0.6).

The fix, in `MusicBrainzService`:

* an ISRC rung in `match_recording`, tried first when the caller knows
  the file's ISRC — the only rung that can tell the file's master apart
  from the ~20 same-title/same-credit recordings a text search returns
  for this song. It is NOT a blind trust of the code: the reported ISRC
  also sits on a garbled remix-titled duplicate in MB's data, so every
  ISRC candidate still has to clear title identity (feat.-stripped),
  version-marker agreement, artist credit/identity, and the duration
  tolerance, and a still-tied set falls through to the name search
  instead of guessing;
* a featuring-stripped retry of the whole recording chain (and of
  `match_release`) when the original found nothing — search and
  scoring both use the stripped title, and the featured names ride
  along as evidence: among gate-clearing candidates at/over the bar, a
  candidate actually credited to the featured artist beats a
  higher-scored solo credit.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from core.musicbrainz_service import (
    MusicBrainzService,
    featured_credit_names,
    strip_featuring_credit,
)

FEAT_TITLE = "Rather Be (feat. Jess Glynne)"
ISRC = "GBAHS1300498"


def _credit(name, mbid=None):
    artist = {"name": name}
    if mbid:
        artist["id"] = mbid
    return {"name": name, "artist": artist}


CB = _credit("Clean Bandit", "mbid-clean-bandit")
JG = _credit("Jess Glynne", "mbid-jess-glynne")

# The recording the reported ISRC actually identifies (live MB data).
REC_ISRC_EXACT = {
    "id": "78a8d975-1257-4fc4-a634-50f89a62de47",
    "title": "Rather Be",
    "length": 227707,
    "artist-credit": [CB, JG],
}
# The same ISRC also sits on this garbled duplicate in MB's data —
# the reason the ISRC rung cannot just take the first row back.
REC_ISRC_GARBLED = {
    "id": "70e74165-ed19-4408-952e-5dd3b6e6c978",
    "title": "Rather Be Rather Be (Jack Patterson & Wez Clarke remix)",
    "length": 227000,
    "artist-credit": [CB, JG],
}
# What the stripped text search returns, in MB's relevance order: a
# solo-credit compilation cut at score 100 ahead of the featured cut.
REC_SOLO = {
    "id": "rec-solo",
    "title": "Rather Be",
    "score": 100,
    "length": 223546,
    "artist-credit": [CB],
}
REC_FEATURED = {
    "id": "rec-featured",
    "title": "Rather Be",
    "score": 81,
    "length": 227000,
    "artist-credit": [CB, JG],
}
REC_INSTRUMENTAL = {
    "id": "rec-instrumental",
    "title": "Rather Be (instrumental)",
    "score": 94,
    "length": 224000,
    "artist-credit": [CB],
}


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    # No artist pin is resolvable unless a test says otherwise.
    svc.mb_client.search_artist.return_value = []
    svc.mb_client.lookup_recordings_by_isrc.return_value = []
    return svc


# --- the helpers -----------------------------------------------------------


@pytest.mark.parametrize("title,expected", [
    ("Rather Be (feat. Jess Glynne)", "Rather Be"),
    ("Song (feat. X) (Live)", "Song (Live)"),
    ("Song (Live) (feat. X)", "Song (Live)"),
    ("Song feat. X", "Song"),
    ("Song feat. X - Remix", "Song - Remix"),
    # A distinct-track tail is title text, never part of the credit.
    ("Song feat. X - Pt. 2", "Song - Pt. 2"),
    ("Song - feat. X - Remix", "Song - Remix"),
    # An em-dash version tail is canonicalised to the hyphen form the
    # version-marker rule recognises.
    ("Rather Be (feat. Jess Glynne) — Remix", "Rather Be - Remix"),
    ("Song featuring A & B", "Song"),
    ("Song ft. X (Remix)", "Song (Remix)"),
    ("Song [feat. X]", "Song"),
    ("Song - feat. X", "Song"),
    ("Song — feat. X", "Song"),
    ("Song (feat. X (of Y))", "Song"),
    ("Plain Title", "Plain Title"),
    ("Live Forever", "Live Forever"),
    ("Featuring Myself", "Featuring Myself"),
    # a title that just ends in the word is not a credit.
    ("Tribute to Little Feat", "Tribute to Little Feat"),
    ("Little Feat (Live)", "Little Feat (Live)"),
])
def test_strip_featuring_credit(title, expected):
    assert strip_featuring_credit(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("Rather Be (feat. Jess Glynne)", ["Jess Glynne"]),
    # The whole credit always leads; a comma list also yields its parts.
    ("Song (feat. X, Y & Z)", ["X, Y & Z", "X", "Y", "Z"]),
    # A bare & / "and" never splits — one artist carries it in the name.
    ("Song featuring A & B", ["A & B"]),
    ("Song (feat. Hall and Oates)", ["Hall and Oates"]),
    ("Song (feat. Earth, Wind & Fire)",
     ["Earth, Wind & Fire", "Earth", "Wind", "Fire"]),
    ("Song ft. X (Remix)", ["X"]),
    ("Song feat. X - Remix", ["X"]),
    ("Song feat. X - Pt. 2", ["X"]),
    ("Song (feat. X (Live))", ["X"]),
    ("Plain Title", []),
])
def test_featured_credit_names(title, expected):
    assert featured_credit_names(title) == expected


# --- the featuring-stripped recording retry ---------------------------------


def test_feat_title_matches_featured_credit_candidate(service):
    # The reporter's exact shape: strict search on the feat. title finds
    # nothing at all (live-verified), the stripped search finds the song.
    service.mb_client.search_recording.side_effect = [
        [], [REC_SOLO, REC_INSTRUMENTAL, REC_FEATURED],
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    # The candidate credited to the featured artist wins over the
    # higher-scored solo credit; the instrumental is marker-gated out.
    assert out["mbid"] == "rec-featured"
    assert [c.args[0] for c in service.mb_client.search_recording.call_args_list] == [
        FEAT_TITLE, "Rather Be",
    ]


def test_feat_title_without_featured_candidate_takes_best_plain(service):
    service.mb_client.search_recording.side_effect = [[], [REC_SOLO]]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rec-solo"


def test_feat_retry_keeps_the_version_gate(service):
    # "(feat. X) (Live)" strips to "Song (Live)" — a bare "Song"
    # candidate is a different performance and must stay rejected.
    service.mb_client.search_recording.side_effect = [
        [],
        [{"id": "rec-bare", "title": "Song", "score": 100,
          "artist-credit": [_credit("Some Artist")]}],
    ]

    out = service.match_recording("Song (feat. X) (Live)", "Some Artist")

    assert out is None


def test_plain_title_hit_never_pays_for_fallbacks(service):
    service.mb_client.search_recording.return_value = [REC_SOLO]

    out = service.match_recording("Rather Be", "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rec-solo"
    service.mb_client.search_recording.assert_called_once_with(
        "Rather Be", "Clean Bandit", limit=5, raise_on_error=True)
    service.mb_client.lookup_recordings_by_isrc.assert_not_called()


def test_feat_retry_runs_when_original_chain_only_reaches_sub_bar(service):
    # The original search DOES return the exact feat. title — at MB
    # score 0 with no artist credit, so it clears the gates at
    # confidence 50 and match_recording would have returned None. That
    # also-ran must not block the stripped retry.
    weak = {"id": "rec-weak", "title": FEAT_TITLE, "score": 0}
    service.mb_client.search_recording.side_effect = [
        [weak], [REC_SOLO, REC_FEATURED],
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rec-featured"


def test_featured_pool_requires_main_artist_evidence(service):
    # A candidate credited ONLY to the featured artist (a Jess Glynne
    # solo recording of the same title) clears 70 on title + MB score —
    # the pool must not elect it over the main artist's own recording.
    featured_only = {
        "id": "rec-featured-only",
        "title": "Rather Be",
        "score": 81,
        "artist-credit": [JG],
    }
    service.mb_client.search_recording.side_effect = [
        [], [REC_SOLO, featured_only],
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rec-solo"


def test_stripped_search_outage_is_not_cached(service):
    service.mb_client.search_recording.side_effect = [
        [], TimeoutError("read timed out"),
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is None
    recording_writes = [
        c for c in service._save_to_cache.call_args_list if c.args[0] == "recording"
    ]
    assert recording_writes == []


def test_feat_title_genuine_miss_is_negative_cached_once(service):
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is None
    recording_writes = [
        c for c in service._save_to_cache.call_args_list if c.args[0] == "recording"
    ]
    assert recording_writes == [
        call("recording", FEAT_TITLE, "Clean Bandit", None, {"feat_retry": 1}, 0)
    ]


# --- the ISRC rung ------------------------------------------------------------


def test_isrc_resolves_the_exact_recording_without_a_name_search(service):
    service.mb_client.lookup_recordings_by_isrc.return_value = [
        REC_ISRC_GARBLED, REC_ISRC_EXACT,
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == REC_ISRC_EXACT["id"]
    assert out["confidence"] == 100
    service.mb_client.search_recording.assert_not_called()
    service.mb_client.lookup_recordings_by_isrc.assert_called_once_with(
        ISRC, raise_on_error=True)
    # The ISRC is part of the cache key (like the artist MBID, #1509):
    # one file's exact answer must not be served to a different file
    # that merely shares (title, artist), and an ISRC-less negative row
    # must not block this lookup for its whole TTL.
    service._save_to_cache.assert_any_call(
        "recording", FEAT_TITLE, "Clean Bandit [isrc:GBAHS1300498]",
        REC_ISRC_EXACT["id"], REC_ISRC_EXACT, 100)


def test_isrc_rescues_a_plain_title_the_name_chain_scores_sub_bar(service):
    # Plain title: the name chain runs first (today's behaviour, no
    # extra lookup for the common case) — here it only reaches 50, so
    # the ISRC rung gets its say.
    weak = {"id": "rec-weak", "title": "Rather Be", "score": 0}
    service.mb_client.search_recording.return_value = [weak]
    service.mb_client.lookup_recordings_by_isrc.return_value = [REC_ISRC_EXACT]

    out = service.match_recording("Rather Be", "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == REC_ISRC_EXACT["id"]
    assert out["confidence"] == 100


def test_isrc_is_not_consulted_when_the_name_chain_accepts(service):
    service.mb_client.search_recording.return_value = [REC_SOLO]

    out = service.match_recording("Rather Be", "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == "rec-solo"
    service.mb_client.lookup_recordings_by_isrc.assert_not_called()


def test_isrc_garbled_duplicate_alone_is_not_a_match(service):
    # Only the remix-titled duplicate carries the ISRC in this stub —
    # its title is not the file's title, so the rung must not bless it.
    service.mb_client.lookup_recordings_by_isrc.return_value = [REC_ISRC_GARBLED]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is None


def test_isrc_tie_falls_through_to_name_search(service):
    # Two indistinguishable recordings share the ISRC (same title, same
    # credit, no duration to separate them): guessing would write a
    # coin flip into the cache, so the rung abstains and the (here
    # empty) name search decides.
    twin = dict(REC_ISRC_EXACT, id="rec-twin")
    service.mb_client.lookup_recordings_by_isrc.return_value = [
        REC_ISRC_EXACT, twin,
    ]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is None


def test_isrc_candidate_credited_to_another_artist_is_rejected(service):
    foreign = {
        "id": "rec-foreign",
        "title": "Rather Be",
        "length": 227707,
        "artist-credit": [_credit("Clean Bandit", "mbid-a-different-clean-bandit"), JG],
    }
    service.mb_client.lookup_recordings_by_isrc.return_value = [foreign]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(
        FEAT_TITLE, "Clean Bandit",
        artist_mbid="mbid-clean-bandit", isrc=ISRC)

    assert out is None


def test_isrc_candidate_with_wrong_version_markers_is_rejected(service):
    live = {
        "id": "rec-live",
        "title": "Rather Be (Live)",
        "length": 227707,
        "artist-credit": [CB, JG],
    }
    service.mb_client.lookup_recordings_by_isrc.return_value = [live]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is None


def test_isrc_candidate_far_off_the_file_duration_is_rejected(service):
    far = dict(REC_ISRC_EXACT, id="rec-far", length=120000)
    service.mb_client.lookup_recordings_by_isrc.return_value = [far]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(
        FEAT_TITLE, "Clean Bandit", isrc=ISRC, duration_ms=227707)

    assert out is None


def test_isrc_candidate_with_name_only_contradicting_credit_is_rejected(service):
    # The caller knows the artist's MBID, but this candidate's credits
    # carry names and NO artist ids — the names are parseable evidence
    # and they contradict, so the candidate must not become the sole
    # survivor just because the identity check had nothing to bite on.
    foreign = {
        "id": "rec-foreign-names",
        "title": "Rather Be",
        "length": 227707,
        "artist-credit": [{
            "name": "Totally Different Band",
            "artist": {"name": "Totally Different Band"},
        }],
    }
    service.mb_client.lookup_recordings_by_isrc.return_value = [foreign]
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(
        FEAT_TITLE, "Clean Bandit",
        artist_mbid="mbid-clean-bandit", isrc=ISRC)

    assert out is None


def test_isrc_transport_failure_is_not_cached(service):
    # raise_on_error=True on the rung: a transport failure means the
    # exact identifier never answered, so even though the name rungs
    # run, their miss must NOT be cached — the ISRC might have
    # resolved it, and a cached miss would hide that for the TTL.
    service.mb_client.lookup_recordings_by_isrc.side_effect = TimeoutError("503")
    service.mb_client.search_recording.return_value = []

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is None
    recording_writes = [
        c for c in service._save_to_cache.call_args_list if c.args[0] == "recording"
    ]
    assert recording_writes == []


def test_isrc_outage_does_not_block_a_name_chain_hit(service):
    # An /isrc/-only outage must not cost the track a match the name
    # chain can still make — and that verified hit is cached as usual.
    service.mb_client.lookup_recordings_by_isrc.side_effect = TimeoutError("503")
    service.mb_client.search_recording.side_effect = [[], [REC_FEATURED]]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == "rec-featured"


def test_emdash_remix_feat_title_does_not_match_the_bare_recording(service):
    # "… — Remix" strips to "Rather Be - Remix" (hyphen-canonical), so
    # the marker gate sees the version and the bare studio recording
    # stays rejected.
    service.mb_client.search_recording.side_effect = [[], [REC_SOLO]]

    out = service.match_recording(
        "Rather Be (feat. Jess Glynne) — Remix", "Clean Bandit")

    assert out is None


def test_isrc_emdash_remix_matches_the_remix_recording(service):
    remix = {
        "id": "rec-isrc-remix",
        "title": "Rather Be (Remix)",
        "length": 300000,
        "artist-credit": [CB, JG],
    }
    service.mb_client.lookup_recordings_by_isrc.return_value = [
        REC_ISRC_EXACT, remix,
    ]

    out = service.match_recording(
        "Rather Be (feat. Jess Glynne) — Remix", "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == "rec-isrc-remix"


def test_isrc_featured_credit_breaks_a_tie(service):
    # Two same-title recordings share the ISRC; only one is credited to
    # the artist the file's own title names as featured.
    solo_twin = {
        "id": "rec-isrc-solo",
        "title": "Rather Be",
        "length": 227707,
        "artist-credit": [CB],
    }
    service.mb_client.lookup_recordings_by_isrc.return_value = [
        solo_twin, REC_ISRC_EXACT,
    ]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit", isrc=ISRC)

    assert out is not None
    assert out["mbid"] == REC_ISRC_EXACT["id"]


# --- the featuring-stripped release retry -------------------------------------


def test_feat_release_matches_stripped_title(service):
    service.mb_client.search_release.side_effect = [
        [],
        [{"id": "rel-1", "title": "Rather Be", "score": 86,
          "artist-credit": [{"artist": {"name": "Clean Bandit"}}]}],
    ]

    out = service.match_release(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rel-1"
    assert [c.args[0] for c in service.mb_client.search_release.call_args_list] == [
        FEAT_TITLE, "Rather Be",
    ]
    # Cached under the ORIGINAL album name, like every release match.
    service._save_to_cache.assert_any_call(
        "release", FEAT_TITLE, "Clean Bandit", "rel-1",
        {"id": "rel-1", "title": "Rather Be", "score": 86,
         "artist-credit": [{"artist": {"name": "Clean Bandit"}}]}, 95)


def test_plain_release_hit_never_pays_for_the_retry(service):
    service.mb_client.search_release.return_value = [
        {"id": "rel-1", "title": "New Eyes", "score": 90,
         "artist-credit": [{"artist": {"name": "Clean Bandit"}}]},
    ]

    out = service.match_release("New Eyes", "Clean Bandit")

    assert out is not None
    service.mb_client.search_release.assert_called_once_with(
        "New Eyes", "Clean Bandit", limit=5, raise_on_error=True)


def test_feat_release_prefers_the_featured_credit_release(service):
    compilation = {"id": "rel-compilation", "title": "Rather Be", "score": 100,
                   "artist-credit": [{"artist": {"name": "Clean Bandit"}}]}
    single = {"id": "rel-single", "title": "Rather Be", "score": 86,
              "artist-credit": [{"artist": {"name": "Clean Bandit"}},
                                 {"artist": {"name": "Jess Glynne"}}]}
    service.mb_client.search_release.side_effect = [
        [], [compilation, single],
    ]

    out = service.match_release(FEAT_TITLE, "Clean Bandit")

    assert out is not None
    assert out["mbid"] == "rel-single"


# --- the worker plumbing -------------------------------------------------------


def test_priority_queue_track_item_carries_isrc(tmp_path):
    from core.worker_utils import priority_pending_item
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "prio.db"))
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Clean Bandit')")
    cur.execute(
        "INSERT INTO albums (id, artist_id, title) VALUES ('al1', 'a1', 'Rather Be')")
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, isrc) "
        "VALUES ('t1', 'al1', 'a1', 'Rather Be (feat. Jess Glynne)', 'GBAHS1300498')")
    conn.commit()

    item = priority_pending_item(cur, "musicbrainz", "track")

    assert item is not None
    assert item["isrc"] == "GBAHS1300498"
    conn.close()


# --- the import flow's file-tag ISRC probe ------------------------------------


def test_audio_file_isrc_reads_embedded_tag(monkeypatch):
    from core.metadata import source as source_module

    class _FakeAudio(dict):
        pass

    fake = _FakeAudio({"ISRC": ["GBAHS1300498"]})
    fake.info = type("I", (), {"length": 227.5})()
    monkeypatch.setattr(
        source_module, "get_mutagen_symbols",
        lambda: type("S", (), {"File": staticmethod(lambda _p: fake)})())

    assert source_module._audio_file_probes("/tmp/whatever.flac") == ("GBAHS1300498", 227500)
    assert source_module._audio_file_probes(None) == (None, None)


def test_audio_file_isrc_swallows_unreadable_files(monkeypatch):
    from core.metadata import source as source_module

    def _boom(_p):
        raise RuntimeError("not audio")

    monkeypatch.setattr(
        source_module, "get_mutagen_symbols",
        lambda: type("S", (), {"File": staticmethod(_boom)})())

    assert source_module._audio_file_probes("/tmp/whatever.flac") == (None, None)


# --- rows cached before the fix ----------------------------------------------


def _negative_row(metadata=None):
    return {"musicbrainz_id": None, "metadata": metadata, "confidence": 0}


def test_pre_fix_negative_recording_row_does_not_block_the_retry(service):
    # every track #1705 hit already has a 30 day "no results" row for its
    # feat. title. honouring it would keep the reporter's 5 songs
    # unmatched for a month after updating.
    service._check_cache.return_value = _negative_row()
    service.mb_client.search_recording.side_effect = [[], [REC_FEATURED]]

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out is not None and out["mbid"] == "rec-featured"


def test_post_fix_negative_recording_row_is_honoured(service):
    service._check_cache.return_value = _negative_row({"feat_retry": 1})

    out = service.match_recording(FEAT_TITLE, "Clean Bandit")

    assert out["mbid"] is None and out["cached"] is True
    service.mb_client.search_recording.assert_not_called()


def test_plain_title_negative_row_is_still_honoured(service):
    service._check_cache.return_value = _negative_row()

    out = service.match_recording("Rather Be", "Clean Bandit")

    assert out["mbid"] is None and out["cached"] is True
    service.mb_client.search_recording.assert_not_called()


def test_pre_fix_negative_release_row_does_not_block_the_retry(service):
    service._check_cache.return_value = _negative_row()
    service.mb_client.search_release.side_effect = [
        [],
        [{"id": "rel-1", "title": "Rather Be", "score": 86,
          "artist-credit": [{"artist": {"name": "Clean Bandit"}}]}],
    ]

    out = service.match_release(FEAT_TITLE, "Clean Bandit")

    assert out is not None and out["mbid"] == "rel-1"


def test_feat_release_miss_is_cached_with_the_marker(service):
    service.mb_client.search_release.return_value = []

    assert service.match_release(FEAT_TITLE, "Clean Bandit") is None
    service._save_to_cache.assert_called_once_with(
        "release", FEAT_TITLE, "Clean Bandit", None, {"feat_retry": 1}, 0)


# --- the source metadata isrc -------------------------------------------------


@pytest.mark.parametrize("track_info,expected", [
    ({"isrc": "GBAHS1300498"}, "GBAHS1300498"),
    ({"external_ids": {"isrc": "GBAHS1300498"}}, "GBAHS1300498"),
    ({"external_ids": ["junk"]}, None),
    ({}, None),
])
def test_source_metadata_carries_the_matched_track_isrc(monkeypatch, track_info, expected):
    # _process_musicbrainz_source reads metadata["isrc"]; nothing used to
    # set it, so that rung of the isrc lookup was dead.
    from core.metadata import source as source_module

    monkeypatch.setattr(source_module, "get_config_manager",
                        lambda: type("C", (), {"get": lambda self, k, d=None: d})())
    context = {
        "track_info": dict(track_info, name="Rather Be"),
        "original_search_result": {"title": "Rather Be", "artist": "Clean Bandit"},
        "source": "spotify",
    }
    metadata = source_module.extract_source_metadata(
        context, {"name": "Clean Bandit"}, {"album_name": "Rather Be"})

    assert metadata.get("isrc") == expected
