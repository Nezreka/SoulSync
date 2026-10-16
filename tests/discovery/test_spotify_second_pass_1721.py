"""#1721 (kevin2xk): a spotify playlist identified on deezer left 44 of 157
tracks as wing it stubs. a stub never gets wishlisted and never lands on the
synced playlist, so those tracks just vanished with no word why.

every one of them came with its own spotify id. the second pass fetches that
track (no login, SpotipyFree) and tries the primary source again with it,
keeping the spotify track itself when the primary still has nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

import core.discovery.spotify_second_pass as ssp
from core.discovery import playlist as dp
from core.discovery import youtube as dy
from core.discovery.manual_match import should_rediscover

SP_ID = "4cOdK2wGLETKBW3PvgPWqT"


def _raw(track_id=SP_ID, name="Quiet Pages", artist="Evren Calder",
         album="Reading Light", duration_ms=201000, isrc=""):
    """a track the way SpotipyFree hands it back (spotify api shape)."""
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist, "id": "art1"}],
        "duration_ms": duration_ms,
        "track_number": 4,
        "disc_number": 1,
        "external_ids": {"isrc": isrc},
        "album": {
            "id": "alb1",
            "name": album,
            "release_date": "2023-05-12",
            "album_type": "album",
            "total_tracks": 11,
            "images": [{"url": "https://i.scdn.co/image/cover", "height": 300, "width": 300}],
            "artists": [{"name": artist, "id": "art1"}],
            # junk the free client carries that must not reach extra_data
            "tracks": {"items": [{"name": "x"}] * 11},
            "copyright": [{"text": "(c) someone"}],
        },
    }


@dataclass
class _Cand:
    id: str
    name: str = "Quiet Pages"
    artists: list = field(default_factory=lambda: ["Evren Calder"])
    album: str = "Reading Light"
    duration_ms: int = 201500
    image_url: str = ""
    release_date: str = ""


class _Primary:
    """a primary source that isn't deezer/itunes, so search_song falls back
    to plain search_tracks."""

    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search_tracks(self, query, limit=10):
        self.queries.append(query)
        return list(self.results)


def _scorer(conf=0.8):
    seen = []

    def score(title, artist, duration_ms, results):
        seen.append(list(results))
        return (results[0], conf, 0) if results else (None, 0.0, -1)

    score.seen = seen
    return score


@pytest.fixture
def free(monkeypatch):
    calls = []
    box = {"raw": _raw()}

    def fetch(track_id):
        calls.append(track_id)
        return box["raw"]

    monkeypatch.setattr(ssp, "_free_track", fetch)
    box["calls"] = calls
    return box


def _run(**kw):
    args = dict(
        id_source="spotify_public", source_track_id=SP_ID, discovery_source="deezer",
        use_spotify=False, title="Quiet Pages", artist="Evren Calder",
        primary_client=_Primary(), score_candidates=_scorer(),
    )
    args.update(kw)
    return ssp.spotify_second_pass(**args)


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------

def test_no_primary_match_keeps_the_spotify_track(free):
    """the reporter's case: deezer has nothing, the spotify track is kept as the
    real release instead of a stub."""
    hit = _run()
    assert hit is not None
    assert hit.provider == "spotify" and hit.how == "spotify"
    assert hit.track.id == SP_ID and hit.track.name == "Quiet Pages"
    assert hit.track.artists == ["Evren Calder"]
    assert hit.album["id"] == "alb1"
    assert hit.album["release_date"] == "2023-05-12"
    assert hit.album["images"][0]["url"].startswith("https://")
    assert "tracks" not in hit.album and "copyright" not in hit.album
    assert (hit.track_number, hit.disc_number) == (4, 1)
    assert free["calls"] == [SP_ID]


def test_primary_candidate_backed_by_album_and_length_wins(free):
    """spotify's album + length make a 0.8 title score on deezer trustworthy:
    the primary source gets its own track, not a spotify one."""
    cand = _Cand(id="dz1")
    hit = _run(primary_client=_Primary([cand]), score_candidates=_scorer(0.8))
    assert hit.provider == "deezer" and hit.how == "corroborated"
    assert hit.track is cand
    assert hit.confidence == 0.8


def test_candidate_on_another_album_is_never_scored(free):
    score = _scorer(0.99)
    hit = _run(primary_client=_Primary([_Cand(id="dz1", album="Lofi Covers Vol 3")]),
               score_candidates=score)
    assert hit.provider == "spotify"
    assert score.seen == []


def test_candidate_with_a_different_length_is_never_scored(free):
    score = _scorer(0.99)
    hit = _run(primary_client=_Primary([_Cand(id="dz1", duration_ms=201000 + 3001)]),
               score_candidates=score)
    assert hit.provider == "spotify"
    assert score.seen == []


def test_weak_title_score_is_refused_even_when_the_release_agrees(free):
    hit = _run(primary_client=_Primary([_Cand(id="dz1")]), score_candidates=_scorer(0.69))
    assert hit.provider == "spotify"


def test_only_the_backed_candidates_reach_the_scorer(free):
    good = _Cand(id="dz-good")
    score = _scorer(0.75)
    _run(primary_client=_Primary([_Cand(id="dz-other", album="Something Else"), good]),
         score_candidates=score)
    assert score.seen == [[good]]


def test_logged_in_spotify_hands_over_an_isrc_and_deezer_answers_it(monkeypatch, free):
    """logged in, the official api carries the isrc and the free client is not
    needed at all."""
    raw = _raw(isrc="GBAAA2300001")

    class _Official:
        def is_spotify_authenticated(self):
            return True

        def get_track_details(self, track_id, allow_fallback=True):
            assert allow_fallback is False
            return {"raw_data": raw}

    hit_track = _Cand(id="dz-isrc")
    seen = {}

    def fake_isrc_match(code, source, use_spotify, spotify_client=None, fallback_client=None, artists=()):
        seen.update(code=code, source=source, artists=list(artists))
        return hit_track

    import core.discovery.isrc_match as im
    monkeypatch.setattr(im, "isrc_match", fake_isrc_match)
    hit = _run(spotify_client=_Official())
    assert hit.provider == "deezer" and hit.how == "isrc" and hit.track is hit_track
    assert seen == {"code": "GBAAA2300001", "source": "deezer", "artists": ["Evren Calder"]}
    assert free["calls"] == []


@pytest.mark.parametrize("kw", [
    {"id_source": "youtube"},
    {"id_source": "deezer"},
    {"id_source": ""},
    {"source_track_id": "mirrored_12"},
    {"source_track_id": "123456"},
    {"source_track_id": ""},
    {"title": "  "},
    {"discovery_source": "spotify", "use_spotify": True},
])
def test_gates_never_reach_spotify(free, kw):
    assert _run(**kw) is None
    assert free["calls"] == []


def test_spotify_primary_without_login_still_gets_the_second_pass(free):
    """discovery_source spotify but not logged in means the search ran on the
    fallback, so the spotify id still has something to add."""
    hit = _run(discovery_source="spotify", use_spotify=False)
    assert hit is not None and hit.provider == "spotify"


def test_no_track_back_stays_wing_it(free):
    free["raw"] = None
    assert _run() is None


def test_a_track_by_someone_else_is_not_trusted(free):
    """the id is exact, but a row whose id names another artist's song means
    something upstream is off."""
    free["raw"] = _raw(artist="Totally Different Band")
    assert _run() is None


def test_a_different_track_back_is_not_trusted(free):
    free["raw"] = _raw(track_id="ZZZZZZZZZZZZZZZZZZZZZZ")
    assert _run() is None


def test_a_track_with_no_album_is_not_trusted(free):
    raw = _raw()
    raw["album"] = {}
    free["raw"] = raw
    assert _run() is None


def test_fetch_errors_stay_wing_it(monkeypatch):
    def boom(track_id):
        raise RuntimeError("spotapi down")

    monkeypatch.setattr(ssp, "_free_track", boom)
    assert _run() is None


@pytest.mark.parametrize("a,b,agree", [
    ("Reading Light", "Reading Light", True),
    ("Reading Light", "reading light!", True),
    ("Reading Light", "Reading Light (Deluxe)", True),
    ("Reading Light", "Lofi Covers", False),
    ("", "Reading Light", False),
    ("", "", False),
])
def test_albums_agree(a, b, agree):
    assert ssp.albums_agree(a, b) is agree


# ---------------------------------------------------------------------------
# the interactive discovery worker (what the reporter ran)
# ---------------------------------------------------------------------------

class _YtDB:
    def __init__(self):
        self.cache_saves = []
        self.mirrored_updates = []

    def get_discovery_cache_match(self, *a):
        return None

    def save_discovery_cache_match(self, *a):
        self.cache_saves.append(a)

    def update_mirrored_track_extra_data(self, db_track_id, extra):
        self.mirrored_updates.append((db_track_id, extra))


class _Engine:
    def generate_download_queries(self, track):
        return [f"{track.artists[0]} {track.name}"]


class _NoCache:
    def get_entity(self, *a):
        return None


def _yt_deps(states, db, primary):
    return dy.YoutubeDiscoveryDeps(
        youtube_playlist_states=states,
        spotify_client=None,
        matching_engine=_Engine(),
        pause_enrichment_workers=lambda label: {},
        resume_enrichment_workers=lambda s, label: None,
        get_active_discovery_source=lambda: "deezer",
        get_metadata_fallback_client=lambda: primary,
        get_discovery_cache_key=lambda t, a: (t.lower(), a.lower()),
        validate_discovery_cache_artist=lambda a, m: True,
        extract_artist_name=lambda a: a if isinstance(a, str) else a.get("name", ""),
        spotify_rate_limited=lambda: False,
        discovery_score_candidates=lambda *a: (None, 0.0, -1),
        get_metadata_cache=lambda: _NoCache(),
        build_discovery_wing_it_stub=lambda t, a, d: {"id": "wing_it_x", "name": t, "artists": [a]},
        get_database=lambda: db,
        add_activity_item=lambda *a, **k: None,
    )


def _yt_state(states, source):
    states["mirrored_29"] = {
        "phase": "discovering",
        "playlist": {"name": "Reading Soundtrack", "source": source, "tracks": [{
            "id": SP_ID, "name": "Quiet Pages", "artists": ["Evren Calder"],
            "duration_ms": 201000, "db_track_id": 77,
        }]},
        "discovery_progress": 0, "spotify_matches": 0, "discovery_results": [],
    }


def test_interactive_discovery_keeps_the_spotify_track_instead_of_wing_it(free):
    states, db = {}, _YtDB()
    _yt_state(states, "spotify_public")
    dy.run_youtube_discovery_worker("mirrored_29", _yt_deps(states, db, _Primary()))

    result = states["mirrored_29"]["discovery_results"][0]
    assert result["status_class"] == "found"
    assert not result.get("wing_it_fallback")
    assert states["mirrored_29"].get("wing_it_count", 0) == 0
    assert result["discovery_source"] == "spotify"
    md = result["matched_data"]
    assert md["id"] == SP_ID and md["source"] == "spotify"
    assert md["album"]["id"] == "alb1" and md["track_number"] == 4

    [(db_id, extra)] = db.mirrored_updates
    assert db_id == 77
    assert extra["provider"] == "spotify"
    assert extra["discovered"] is True
    assert extra["wing_it_fallback"] is False
    # filed under deezer, the cache would hand other playlists a spotify match
    assert db.cache_saves == []


def test_interactive_discovery_leaves_non_spotify_mirrors_alone(free):
    states, db = {}, _YtDB()
    _yt_state(states, "youtube")
    dy.run_youtube_discovery_worker("mirrored_29", _yt_deps(states, db, _Primary()))

    result = states["mirrored_29"]["discovery_results"][0]
    assert result["status_class"] == "wing-it"
    assert free["calls"] == []


def test_interactive_discovery_still_wing_its_when_spotify_has_nothing(free):
    free["raw"] = None
    states, db = {}, _YtDB()
    _yt_state(states, "spotify_public")
    dy.run_youtube_discovery_worker("mirrored_29", _yt_deps(states, db, _Primary()))

    result = states["mirrored_29"]["discovery_results"][0]
    assert result["status_class"] == "wing-it"
    [(_, extra)] = db.mirrored_updates
    assert extra["wing_it_fallback"] is True and extra["provider"] == "wing_it_fallback"


# ---------------------------------------------------------------------------
# the pipeline worker (scheduled / auto-sync mirrors)
# ---------------------------------------------------------------------------

class _PlDB:
    def __init__(self, tracks):
        self.tracks = tracks
        self.writes = []
        self.cache_saves = []

    def get_mirrored_playlist_tracks(self, pl_id):
        return self.tracks

    def get_discovery_cache_match(self, *a):
        return None

    def update_mirrored_track_extra_data(self, track_id, extra):
        self.writes.append((track_id, extra))

    def adopt_discovered_artist(self, *a):
        pass

    def save_discovery_cache_match(self, *a):
        self.cache_saves.append(a)


def _pl_deps(db, primary, score=(None, 0.0, -1)):
    return dp.PlaylistDiscoveryDeps(
        spotify_client=None,
        matching_engine=_Engine(),
        automation_engine=None,
        playlist_discovery_cancelled=set(),
        pause_enrichment_workers=lambda label: {},
        resume_enrichment_workers=lambda s, label: None,
        get_active_discovery_source=lambda: "deezer",
        get_metadata_fallback_client=lambda: primary,
        get_metadata_fallback_source=lambda: "deezer",
        update_automation_progress=lambda *a, **k: None,
        get_database=lambda: db,
        get_discovery_cache_key=lambda t, a: (t.lower(), a.lower()),
        validate_discovery_cache_artist=lambda a, m: True,
        discovery_score_candidates=lambda *a: score,
        get_metadata_cache=lambda: _NoCache(),
        build_discovery_wing_it_stub=lambda t, a, d: {"id": "wing_it_x", "name": t, "artists": [a]},
    )


def _pl_track(extra=None):
    t = {"id": 5, "track_name": "Quiet Pages", "artist_name": "Evren Calder",
         "duration_ms": 201000, "source_track_id": SP_ID}
    if extra is not None:
        t["extra_data"] = json.dumps(extra)
    return t


def test_pipeline_discovery_keeps_the_spotify_track_instead_of_wing_it(monkeypatch, free):
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    db = _PlDB([_pl_track()])
    dp.run_playlist_discovery_worker(
        [{"id": 29, "name": "Reading Soundtrack", "source": "spotify_public"}],
        deps=_pl_deps(db, _Primary()))

    [(track_id, extra)] = db.writes
    assert track_id == 5
    assert extra["provider"] == "spotify"
    assert extra["wing_it_fallback"] is False
    md = extra["matched_data"]
    assert md["id"] == SP_ID and md["source"] == "spotify"
    assert md["album"]["id"] == "alb1" and md["album"]["release_date"] == "2023-05-12"
    assert md["track_number"] == 4
    assert db.cache_saves == []
    # complete, so the next run leaves it alone instead of fetching again
    assert should_rediscover(extra) is False


def test_pipeline_real_match_clears_the_flag_a_past_wing_it_left(monkeypatch, free):
    """extra_data merges. without the clear, a track that was wing it once kept
    wing_it_fallback after a real match and was rediscovered on every run."""
    monkeypatch.setattr(dp.time, "sleep", lambda s: None)
    old = {"discovered": True, "wing_it_fallback": True, "provider": "wing_it_fallback",
           "matched_data": {"id": "wing_it_x"}}
    db = _PlDB([_pl_track(extra=old)])
    match = _Cand(id="dz1")
    dp.run_playlist_discovery_worker(
        [{"id": 29, "name": "Reading Soundtrack", "source": "spotify_public"}],
        deps=_pl_deps(db, _Primary([match]), score=(match, 0.95, 0)))

    [(_, extra)] = db.writes
    assert extra["provider"] == "deezer"
    merged = {**old, **extra}
    assert merged["wing_it_fallback"] is False
    assert free["calls"] == []  # matched on the first pass, no second needed
