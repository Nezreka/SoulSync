"""Which kind of stuck a video wishlist row is.

The wishlist page groups stuck rows by cause, the same way the music page does:
nothing found, found but a rule of yours refused it, the title is out there but
this episode isn't yet, or no source could even search. The classification
lives server-side (core.video.wishlist_evidence.row_trouble) so the page never
re-implements the refusal classifier and drifts from it.

Snapshots here are built with the drain's REAL source_outcome(), so they have
exactly the shape the recorder stores.
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.automation.handlers.video_process_wishlist import source_outcome
from core.video.wishlist_evidence import (
    TROUBLE_AWAITING,
    TROUBLE_NOTFOUND,
    TROUBLE_REFUSED,
    TROUBLE_UNSEARCHABLE,
    row_trouble,
)
from database.video_database import VideoDatabase


def _snap(**sources):
    """A receipt: {source name: candidates list, or None for 'could not search'}."""
    return {"chain": list(sources),
            "sources": {name: source_outcome(cands, None if cands is not None else "Prowlarr not configured")
                        for name, cands in sources.items()}}


def _rel(title, rejected=None, accepted=False, quality="1080p WEB"):
    return {"title": title, "rejected": rejected, "accepted": accepted, "quality_label": quality}


class TestRowTrouble:
    def test_no_source_could_search(self):
        assert row_trouble(_snap(torrent=None)) == TROUBLE_UNSEARCHABLE

    def test_one_source_down_does_not_hide_what_the_other_found(self):
        # torrent never ran; soulseek found the film and the profile refused it.
        snap = _snap(torrent=None, soulseek=[_rel("Film 1999", "1080p WEB isn't in your enabled tiers")])
        assert row_trouble(snap) == TROUBLE_REFUSED

    def test_searched_and_nothing_came_back(self):
        assert row_trouble(_snap(soulseek=[], torrent=[])) == TROUBLE_NOTFOUND

    def test_everything_found_was_some_other_title(self):
        snap = _snap(torrent=[_rel("Other Show S01E01", "Wrong title (Other — wanted This)"),
                              _rel("Film 2004", "Wrong year")])
        assert row_trouble(snap) == TROUBLE_NOTFOUND

    def test_right_show_wrong_episode_is_a_wait_not_a_fault(self):
        snap = _snap(torrent=[_rel("Show S02E01", "Wrong episode"),
                              _rel("Show S02 Pack", "Not a single episode")])
        assert row_trouble(snap) == TROUBLE_AWAITING

    @pytest.mark.parametrize("reason", [
        "720p isn't in your enabled tiers",     # quality
        "Over your 20 GB size cap",             # size
        "Uploader blocklisted",                 # blocked
        "No seeders",                           # swarm
        "Something the classifier never saw",   # other
    ])
    def test_a_rule_of_yours_turned_it_down(self, reason):
        assert row_trouble(_snap(torrent=[_rel("Film 1999", reason)])) == TROUBLE_REFUSED

    def test_something_usable_means_it_is_not_stuck_on_availability(self):
        snap = _snap(torrent=[_rel("Film 1999", accepted=True)])
        assert row_trouble(snap) is None

    @pytest.mark.parametrize("snap", [None, {}, {"sources": {}}, {"sources": "x"}, "junk", 7,
                                      {"sources": {"torrent": "not a dict"}}])
    def test_no_receipt_says_nothing(self, snap):
        assert row_trouble(snap) is None

    def test_old_receipt_with_results_but_no_samples_does_not_guess(self):
        # receipts from before samples were stored: 15 results, no way to judge them
        snap = {"chain": ["torrent"], "sources": {"torrent": {"ran": True, "results": 15, "accepted": 0}}}
        assert row_trouble(snap) is None


# ── it reaches the page ──────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_movie_rows_carry_their_trouble(client, db):
    db.add_movie_to_wishlist(501, "Refused Film")
    db.add_movie_to_wishlist(502, "Never Searched")
    db.record_wishlist_search_outcome(
        "movie", 501, grabbed=False,
        snapshot=_snap(torrent=[_rel("Refused Film 2001", "Over your 20 GB size cap")]))
    out = client.get("/api/video/wishlist?kind=movie").get_json()
    by_id = {it["tmdb_id"]: it for it in out["items"]}
    assert by_id[501]["trouble"] == TROUBLE_REFUSED
    assert by_id[502]["trouble"] is None


def test_episode_rows_carry_their_trouble(client, db):
    db.add_episodes_to_wishlist(900, "A Show", [{"season_number": 1, "episode_number": 1},
                                                 {"season_number": 1, "episode_number": 2}])
    db.record_wishlist_search_outcome(
        "episode", 900, grabbed=False, season_number=1, episode_number=2,
        snapshot=_snap(torrent=[_rel("A Show S01E05", "Wrong episode")]))
    out = client.get("/api/video/wishlist?kind=show").get_json()
    eps = {e["episode_number"]: e for e in out["items"][0]["seasons"][0]["episodes"]}
    assert eps[2]["trouble"] == TROUBLE_AWAITING
    assert eps[1]["trouble"] is None


def test_a_broken_classifier_never_breaks_the_page(client, db, monkeypatch):
    import core.video.wishlist_evidence as ev
    db.add_movie_to_wishlist(503, "Fine")

    def boom(_snapshot):
        raise RuntimeError("classifier exploded")
    monkeypatch.setattr(ev, "row_trouble", boom)
    out = client.get("/api/video/wishlist?kind=movie").get_json()
    assert out["success"] is True and out["items"][0]["tmdb_id"] == 503
