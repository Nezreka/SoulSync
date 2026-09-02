"""When the download client refuses a release, the wishlist must not blame quality.

Read out of Boulder's live install. ``Project.Pay.Day.2021.1080p.WEB-DL.DD5.1.H.264-EVO``
was sitting in his qBittorrent at ``metaDL`` 0%. Every hour the drain searched, found
that exact release, judged it ACCEPTED, offered it to qBittorrent, and qBittorrent
answered ``Fails.`` — its response to a torrent it already holds. Three things then
went wrong in a row:

1. ``_one`` fell through to the all-rejected branch and logged "none accepted — none
   met your quality profile", which was false: the release had passed the profile.
   The user is pointed at the one setting that cannot fix it.
2. ``record_outcome`` counted it as a fruitless search, so ``search_attempts`` climbed
   — 133 on that row, 198 on another — implying the release does not exist.
3. The next run re-picked the same top candidate and repeated all of it, forever. The
   second-best release was never offered.

The fix is one idea: **a refusal is evidence about the CLIENT, not about the release.**
So it gets its own outcome, it does not touch the fruitless-search counter, and the
drain walks on to the next acceptable candidate.
"""

from __future__ import annotations

from core.automation.handlers.video_process_wishlist import (
    MAX_GRAB_ATTEMPTS,
    acceptable_candidates,
    auto_video_process_wishlist,
    display_name,
    enqueue_outcome,
)


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, _id, **kw):
        self.progress.append(kw)


def _cand(name, accepted=True, resolution="1080p", **kw):
    return dict({"filename": name, "title": name, "accepted": accepted,
                 "resolution": resolution, "rejected": None if accepted else "junk",
                 "source": "torrent"}, **kw)


def _run(items, searches, enqueue, media_type="movie", root="/movies", recorded=None):
    deps = _Deps()
    res = auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1}, deps, media_type=media_type,
        fetch_items=lambda mt: items, active_keys=lambda mt: set(),
        target_dir=lambda mt: root, search=lambda it, mt: searches,
        enqueue=enqueue,
        record_outcome=(lambda it, mt, ok, refusal=None:
                        recorded.append((display_name(it, mt), ok)))
        if recorded is not None else (lambda *a: None))
    return res, deps


def _logs(deps):
    return " ".join(p.get("log_line") or "" for p in deps.progress)


# ── the refusal is its own outcome ───────────────────────────────────────────

def test_a_refused_release_is_not_reported_as_a_quality_rejection():
    refuse = lambda *a: {"ok": False, "error": "qBittorrent already holds this torrent"}
    res, deps = _run([{"tmdb_id": 1, "title": "Project Pay Day"}],
                     [_cand("Project.Pay.Day.2021.1080p.WEB-DL")], refuse)
    assert res["grabbed"] == 0
    assert res["refused"] == 1
    assert res["rejected"] == 0, "a client refusal is not a quality rejection"
    logs = _logs(deps)
    assert "quality profile" not in logs, logs
    assert "download client refused" in logs
    assert "qBittorrent already holds this torrent" in logs, "the real reason must survive"


def test_a_refusal_does_not_count_as_a_fruitless_search():
    """search_attempts drives the 'this item keeps failing' signal. A client
    refusing a release we DID find says nothing about whether it exists, so
    recording it poisons the one number meant to surface genuinely dead items."""
    recorded = []
    refuse = lambda *a: {"ok": False, "error": "Fails."}
    _run([{"tmdb_id": 1, "title": "A"}], [_cand("A.2021.1080p.WEB")], refuse,
         recorded=recorded)
    assert recorded == [], "a refusal must not be recorded as a search outcome"


def test_a_genuine_quality_rejection_is_still_recorded_and_still_says_so():
    """Guard against the fix over-reaching: when nothing passed the profile, the
    old message and the old bookkeeping are exactly right."""
    recorded = []
    res, deps = _run([{"tmdb_id": 1, "title": "A"}],
                     [dict(_cand("junk", accepted=False), rejected="No seeders — nobody is sharing this")],
                     lambda *a: {"ok": True}, recorded=recorded)
    assert res["rejected"] == 1 and res["refused"] == 0
    assert recorded == [("A", False)]
    assert "none accepted — No seeders" in _logs(deps)


def test_an_empty_search_is_still_recorded():
    recorded = []
    res, _ = _run([{"tmdb_id": 1, "title": "A"}], [], lambda *a: {"ok": True},
                  recorded=recorded)
    assert res["noresults"] == 1 and recorded == [("A", False)]


# ── the walk ─────────────────────────────────────────────────────────────────

def test_the_next_candidate_is_offered_when_the_first_is_refused():
    """The whole point: one bad release must not block the item."""
    tried = []

    def enqueue(item, best, cands, mt, target):
        tried.append(best["filename"])
        return {"ok": best["filename"] == "second", "error": "refused"}

    res, _ = _run([{"tmdb_id": 1, "title": "A"}],
                  [_cand("first"), _cand("second")], enqueue)
    assert tried == ["first", "second"]
    assert res["grabbed"] == 1 and res["refused"] == 0


def test_the_walk_stops_at_the_first_success():
    tried = []

    def enqueue(item, best, cands, mt, target):
        tried.append(best["filename"])
        return {"ok": True}

    _run([{"tmdb_id": 1, "title": "A"}],
         [_cand("first"), _cand("second"), _cand("third")], enqueue)
    assert tried == ["first"], "a successful grab must not keep offering releases"


def test_the_walk_is_bounded():
    """A stuck item must not turn into an indexer/client hammer — offering every
    hit of a 100-release search every hour would be its own bug."""
    tried = []

    def enqueue(item, best, cands, mt, target):
        tried.append(best["filename"])
        return {"ok": False, "error": "no"}

    cands = [_cand("rel-%d" % i) for i in range(20)]
    res, _ = _run([{"tmdb_id": 1, "title": "A"}], cands, enqueue)
    assert len(tried) == MAX_GRAB_ATTEMPTS
    assert res["refused"] == 1


def test_only_acceptable_candidates_are_ever_offered():
    """The walk must respect the quality profile — it is a retry over releases
    that PASSED, not a bypass of the gate."""
    tried = []

    def enqueue(item, best, cands, mt, target):
        tried.append(best["filename"])
        return {"ok": False, "error": "no"}

    _run([{"tmdb_id": 1, "title": "A"}],
         [_cand("good"), _cand("bad", accepted=False), _cand("also-good")], enqueue)
    assert tried == ["good", "also-good"]


def test_the_walk_honours_the_upgrade_floor():
    """An owned item only accepts strictly-better releases; walking must not
    quietly hand over the same-quality one the floor exists to exclude."""
    tried = []

    def enqueue(item, best, cands, mt, target):
        tried.append(best["filename"])
        return {"ok": False, "error": "no"}

    # _min_rank 3 = the user already has 1080p; only 2160p qualifies.
    _run([{"tmdb_id": 1, "title": "A", "_min_rank": 3}],
         [_cand("same-1080", resolution="1080p"), _cand("better-4k", resolution="2160p")],
         enqueue)
    assert tried == ["better-4k"]


# ── the seam contract ────────────────────────────────────────────────────────

def test_a_bool_returning_enqueue_still_works():
    """Several callers and every older test fake answer a bare bool. Accepting
    both shapes is what let this land without rewriting them."""
    assert enqueue_outcome(True) == {"ok": True, "error": None}
    assert enqueue_outcome(False) == {"ok": False, "error": None}
    assert enqueue_outcome(None) == {"ok": False, "error": None}
    assert enqueue_outcome({"ok": True}) == {"ok": True, "error": None}
    assert enqueue_outcome({"ok": False, "error": "why"}) == {"ok": False, "error": "why"}


def test_a_legacy_bool_refusal_still_reads_as_a_refusal():
    res, deps = _run([{"tmdb_id": 1, "title": "A"}], [_cand("rel")], lambda *a: False)
    assert res["refused"] == 1 and res["rejected"] == 0
    assert "no reason given" in _logs(deps)


def test_acceptable_candidates_keeps_the_ranked_order():
    cands = [_cand("a"), _cand("b", accepted=False), _cand("c")]
    assert [c["filename"] for c in acceptable_candidates(cands)] == ["a", "c"]
    assert acceptable_candidates([]) == []
    assert acceptable_candidates(None) == []


# ── the name in the log ──────────────────────────────────────────────────────

def test_an_episode_refusal_names_the_episode():
    """Episode rows carry show_title, not title, so the refusal log line read
    'grab refused for None' for every single episode."""
    item = {"show_tmdb_id": 9, "show_title": "Aussie Shore",
            "season_number": 2, "episode_number": 4}
    assert display_name(item, "episode") == "Aussie Shore S02E04"
    assert "None" not in display_name(item, "episode")


def test_a_movie_keeps_its_plain_title():
    assert display_name({"tmdb_id": 1, "title": "Project Pay Day"}, "movie") == "Project Pay Day"


def test_a_nameless_row_degrades_to_a_placeholder():
    assert display_name({}, "movie") == "?"
    assert display_name({}, "episode") == "? S00E00"


def test_the_episode_refusal_message_carries_the_episode_name():
    res, deps = _run([{"show_tmdb_id": 9, "show_title": "Aussie Shore",
                       "season_number": 2, "episode_number": 4}],
                     [_cand("Aussie.Shore.S02E04.1080p.WEB")],
                     lambda *a: {"ok": False, "error": "Fails."},
                     media_type="episode", root="/tv")
    assert res["refused"] == 1
    assert "Aussie Shore S02E04" in _logs(deps)


# ── the receipt reaches record_outcome ───────────────────────────────────────

def test_the_drain_hands_over_the_best_refused_release():
    """The whole point: the drain already judged every candidate and was about to
    discard the verdicts. This is the seam where the evidence survives."""
    seen = []
    cands = [
        _cand("a", accepted=False, resolution="480p", quality_label="480p WEB",
              rejected="480p WEB isn't in your enabled tiers"),
        _cand("b", accepted=False, resolution="720p", quality_label="720p WEB",
              rejected="720p WEB isn't in your enabled tiers"),
        _cand("c", accepted=False, resolution="1080p", quality_label="1080p WEB",
              rejected="Wrong season"),
    ]
    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1}, _Deps(), media_type="movie",
        fetch_items=lambda mt: [{"tmdb_id": 7, "title": "Stuck"}],
        active_keys=lambda mt: set(), target_dir=lambda mt: "/movies",
        search=lambda it, mt: cands,
        enqueue=lambda *a, **k: {"ok": False, "error": "no"},
        record_outcome=(lambda it, mt, ok, refusal=None: seen.append(refusal)))
    assert seen, "an outcome must be recorded for a fruitless search"
    got = seen[0]
    assert got and got["quality_label"] == "720p WEB", "the best AVAILABILITY refusal"
    assert got["seen"] == 2, "the wrong-season hit is noise, not evidence"



def test_a_refused_release_records_a_visible_note_without_search_outcome():
    outcomes, notes = [], []
    refuse = lambda *a: {"ok": False, "error": "No working release found after retries"}
    auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1}, _Deps(), media_type="movie",
        fetch_items=lambda mt: [{"tmdb_id": 1, "title": "A"}],
        active_keys=lambda mt: set(), target_dir=lambda mt: "/movies",
        search=lambda it, mt: [_cand("A.2021.1080p.WEB", quality_label="WEBDL-1080p")],
        enqueue=refuse,
        record_outcome=lambda *a, **k: outcomes.append(a),
        record_note=lambda it, mt, note, quality=None: notes.append((display_name(it, mt), note, quality)))
    assert outcomes == []
    assert notes == [("A", "Download client refused: No working release found after retries", "WEBDL-1080p")]
