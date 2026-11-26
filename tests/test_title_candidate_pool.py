"""TitleCandidatePool (the check-tracks matcher) must pick exactly what
choose_best_title_candidate picks, and bounded_ratio must agree with
SequenceMatcher.ratio() on every ">= threshold" question."""

import random
import string
from difflib import SequenceMatcher

import pytest

from core.text.title_match import TitleCandidatePool, bounded_ratio, choose_best_title_candidate


def _clean(t):
    import re
    t = re.sub(r"\s*[\[\(].*?[\]\)]", "", t.lower())
    return re.sub(r"\s+", " ", t.replace(" - ", " ")).strip()


def _entries(titles):
    return [(t.lower(), _clean(t), i) for i, t in enumerate(titles)]


CATALOGUE = [
    "Yellow", "Yellow (Live)", "yellow", "Fix You", "Fix You - Live", "Viva la Vida",
    "Viva La Vida (Demo)", "Clocks", "Clocks [Remastered]", "Beyond I", "Beyond Fate",
    "Solace", "Solve", "Believe", "Beleive", "The Noose", "The Doomed", "Ratata",
    "Ratata (Afro Bros Remix)", "Ratata (Radio Edit)",
]

QUERIES = ["Yellow", "yellow", "Yellow (Live)", "Fix You", "fix you - live", "Viva la Vida (Live)",
           "Clocks", "Beyond II", "Solve", "Beleive", "The Noose", "Ratata", "Ratata (Extended)",
           "Nope", "Viva La Vida", "Clocks (2008 Remaster)"]


@pytest.mark.parametrize("threshold", [0.7, 0.85])
def test_pool_picks_what_the_scan_picks(threshold):
    cands = _entries(CATALOGUE)
    pool = TitleCandidatePool(cands)
    full = lambda a, b: SequenceMatcher(None, a, b).ratio()  # noqa: E731
    for q in QUERIES:
        want = choose_best_title_candidate(q.lower(), _clean(q), cands, full, threshold=threshold)
        assert pool.best(q.lower(), _clean(q), threshold=threshold) == want, q


def test_pool_matches_the_scan_on_random_titles():
    rng = random.Random(7)
    words = ["love", "night", "the", "sun", "rain", "fire", "blue", "song", "remix", "live"]

    def title():
        t = " ".join(rng.choice(words) for _ in range(rng.randint(1, 4)))
        if rng.random() < 0.3:
            t += f" ({rng.choice(words)})"
        if rng.random() < 0.2:
            t = t.replace(rng.choice(string.ascii_lowercase), "", 1)
        return t

    cands = _entries([title() for _ in range(300)])
    pool = TitleCandidatePool(cands)
    full = lambda a, b: SequenceMatcher(None, a, b).ratio()  # noqa: E731
    for _ in range(300):
        q = title()
        for thr in (0.7, 0.85):
            assert pool.best(q, _clean(q), threshold=thr) == \
                choose_best_title_candidate(q, _clean(q), cands, full, threshold=thr), q


def test_bounded_ratio_agrees_at_the_threshold():
    rng = random.Random(3)
    for _ in range(2000):
        a = "".join(rng.choice("abcde ") for _ in range(rng.randint(0, 14)))
        b = "".join(rng.choice("abcde ") for _ in range(rng.randint(0, 14)))
        for thr in (0.7, 0.85):
            real = SequenceMatcher(None, a, b).ratio()
            got = bounded_ratio(thr)(a, b)
            assert (got >= thr) == (real >= thr)
            if real >= thr:
                assert got == real


def test_check_tracks_reads_past_500_rows(monkeypatch):
    """the route used to read 500 rows a-z by title: a big catalogue lost its
    late-alphabet titles and called them not owned"""
    import os
    import tempfile
    import types
    os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(prefix="ss-ct-"), "a.db"))
    os.environ["SOULSYNC_TEST_DB_READY"] = "1"
    web_server = pytest.importorskip("web_server")
    import hashlib
    names = [hashlib.md5(str(i).encode()).hexdigest()[:12] for i in range(1200)]
    rows = [types.SimpleNamespace(id=i, title=names[i], album_title="A", file_path=f"/m/{i}.flac",
                                  bitrate=900, artist_name="Big Artist", track_artist=None)
            for i in range(1200)]

    class _DB:
        def search_tracks(self, **kwargs):
            return rows[:kwargs.get("limit", 50)]

    monkeypatch.setattr("database.music_database.MusicDatabase", lambda *a, **k: _DB())
    monkeypatch.setattr(web_server.config_manager, "get_active_media_server", lambda: "plex")
    r = web_server.app.test_client().post("/api/library/check-tracks", json={
        "artist_name": "Big Artist", "tracks": [{"name": names[1150]}, {"name": names[3]}, {"name": "Nope"}]})
    owned = r.get_json()["owned_tracks"]
    assert owned[names[1150]]["owned"] is True
    assert owned[names[3]]["owned"] is True
    assert owned["Nope"]["owned"] is False
