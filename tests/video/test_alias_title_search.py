"""Alias-title fallback in the wishlist search (#2021).

A wishlist row carries ONE primary title — for the reporter that was the
French TMDB name 'L'amour sous couverture' — while releases are posted under
the show's English name 'Love in Disguise'. The alias set (TMDB alternative
titles + manual aliases + anime names) was already built into the search
context and used to GATE releases and to re-query Soulseek retries, but
the initial search only ever queried the primary name, so nothing was
ever found. ``_search_one_source`` now falls back to the other names —
one at a time, capped, stopping at the first name that yields a usable
(accepted, group-override-aware) release — whenever the primary search
ran but yielded nothing usable. When no name yields one, the returned
evidence is every name's candidates merged.
"""

from __future__ import annotations

import pytest

import core.automation.handlers.video_process_wishlist as vpw

PRIMARY = "L'amour sous couverture"
ENGLISH = "Love in Disguise"
THIRD = "A Third Name"

CTX = {"scope": "episode", "title": PRIMARY, "titles": [PRIMARY, ENGLISH, THIRD],
       "season": 1, "episode": 6, "year": None}


def _rig(monkeypatch, ctx=None):
    """Patch the I/O seams ``_search_one_source`` imports at call time."""
    monkeypatch.setattr(vpw, "search_context", lambda item, media_type: dict(ctx or CTX))
    monkeypatch.setattr(vpw, "title_overrides_for_item", lambda item, media_type: {})
    monkeypatch.setattr("api.video.get_video_db", lambda: object())
    monkeypatch.setattr("core.video.quality_profile.load_for_item",
                        lambda db, item: object())
    evaluated = {}

    def _eval(raw, profile, scope, want_season, want_episode, **kw):
        evaluated["raw"] = list(raw)
        evaluated["want_title"] = kw.get("want_title")
        return [{"title": h.get("title"), "filename": h.get("title"),
                 "indexer_id": h.get("indexer_id"),
                 "accepted": h.get("accepted", True)} for h in raw]

    monkeypatch.setattr("api.video.downloads._evaluate_hits", _eval)
    return evaluated


def _prowlarr(monkeypatch, hits_by_title, configured=True, error=None):
    calls = []

    def fake(scope, title, **kw):
        calls.append(title)
        if not configured:
            return {"configured": False, "hits": []}
        if error:
            return {"configured": True, "error": error, "hits": []}
        return {"configured": True, "hits": list(hits_by_title.get(title, []))}

    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", fake)
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx, title=None: ([], None))
    return calls


# ── _search_titles (pure) ────────────────────────────────────────────────────
def test_search_titles_primary_first_deduped():
    assert vpw._search_titles({"title": "A", "titles": ["A", "b", "B", " c "]}) == ["A", "b", "c"]
    assert vpw._search_titles({"title": "A"}) == ["A"]
    assert vpw._search_titles({}) == []
    # a hand-built ctx whose 'titles' forgot the primary still leads with it
    assert vpw._search_titles({"title": "P", "titles": ["X"]}) == ["P", "X"]


# ── torrent / prowlarr lane ──────────────────────────────────────────────────
def test_alias_is_searched_when_primary_finds_nothing(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {ENGLISH: [{"title": "Love.in.Disguise.S01E06.1080p"}]})
    cands, err = vpw._search_one_source("torrent", {"show_title": PRIMARY}, "episode")
    assert err is None
    assert calls == [PRIMARY, ENGLISH]           # stopped at the first name with hits
    assert [c["title"] for c in cands] == ["Love.in.Disguise.S01E06.1080p"]
    assert all(c["source"] == "torrent" for c in cands)


def test_no_alias_search_when_primary_yields_accepted(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {PRIMARY: [{"title": "hit"}],
                                    ENGLISH: [{"title": "other"}]})
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert calls == [PRIMARY] and [c["title"] for c in cands] == ["hit"]


def test_alias_fallback_is_capped(monkeypatch):
    titles = ["T%d" % i for i in range(10)]
    _rig(monkeypatch, {"scope": "movie", "title": "T0", "titles": titles, "year": 2020})
    calls = _prowlarr(monkeypatch, {})
    cands, err = vpw._search_one_source("torrent", {}, "movie")
    assert cands == [] and err is None
    assert calls == titles[:1 + vpw.ALIAS_TITLE_SEARCH_LIMIT]


def test_no_alias_burn_when_prowlarr_not_configured(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {}, configured=False)
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: (None, "EXT.to requires FlareSolverr"))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert cands is None and "Prowlarr not configured" in err
    assert "EXT.to requires FlareSolverr" in err           # both halves of the original error
    assert calls == [PRIMARY]                     # one attempt, error reported as before


def test_usenet_error_does_not_burn_aliases(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {}, configured=False)
    cands, err = vpw._search_one_source("usenet", {}, "episode")
    assert cands is None and err == "Prowlarr not configured"
    assert calls == [PRIMARY]


def test_evaluation_still_gates_on_the_full_alias_set(monkeypatch):
    evaluated = _rig(monkeypatch)
    _prowlarr(monkeypatch, {ENGLISH: [{"title": "hit"}]})
    vpw._search_one_source("torrent", {}, "episode")
    assert evaluated["want_title"] == [PRIMARY, ENGLISH, THIRD]


# ── soulseek lane ────────────────────────────────────────────────────────────
def _soulseek(monkeypatch, results_by_query):
    calls = []

    def fake(query, **kw):
        calls.append(query)
        return results_by_query.get(query, {"started": True, "hits": []})

    monkeypatch.setattr("core.video.download_monitor._search_for_retry", fake)
    return calls


def test_soulseek_falls_back_to_alias_query(monkeypatch):
    from core.video.slskd_search import build_query
    _rig(monkeypatch)
    q_primary = build_query("episode", PRIMARY, season=1, episode=6)
    q_alias = build_query("episode", ENGLISH, season=1, episode=6)
    calls = _soulseek(monkeypatch, {q_alias: {"started": True,
                                             "hits": [{"title": "hit", "filename": "hit"}]}})
    cands, err = vpw._search_one_source("soulseek", {}, "episode")
    assert calls == [q_primary, q_alias]
    assert [c["title"] for c in cands] == ["hit"]


def test_soulseek_search_not_started_reports_without_aliases(monkeypatch):
    _rig(monkeypatch)
    calls = _soulseek(monkeypatch, {})
    monkeypatch.setattr("core.video.download_monitor._search_for_retry",
                        lambda q, **kw: calls.append(q) or {"started": False, "error": "slskd down"})
    cands, err = vpw._search_one_source("soulseek", {}, "episode")
    assert cands is None and err == "slskd down" and len(calls) == 1


# ── extto lane ───────────────────────────────────────────────────────────────
def test_extto_falls_back_to_alias(monkeypatch):
    _rig(monkeypatch)
    seen = []

    def fake_extto(ctx, title=None):
        seen.append(title)
        return ([{"title": "hit"}] if title == ENGLISH else []), None

    monkeypatch.setattr(vpw, "_extto_hits_for_context", fake_extto)
    cands, err = vpw._search_one_source("extto", {}, "episode")
    assert seen == [PRIMARY, ENGLISH]
    assert [c["title"] for c in cands] == ["hit"] and cands[0]["source"] == "extto"


def test_unsupported_source_still_rejected(monkeypatch):
    _rig(monkeypatch)
    cands, err = vpw._search_one_source("deezer", {}, "episode")
    assert cands is None and "unsupported source" in err


# ── round-1 review additions ─────────────────────────────────────────────────
def test_alias_tried_when_primary_hits_are_all_rejected(monkeypatch):
    """The trigger is 'no USABLE release', not 'no raw hits': a popular
    primary name returning only wrong-year junk must not block the alias."""
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {
        PRIMARY: [{"title": "wrong.year.junk", "accepted": False}],
        ENGLISH: [{"title": "Love.in.Disguise.S01E06.1080p"}]})
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert calls == [PRIMARY, ENGLISH]
    # the winner leads; the primary's rejected junk rides behind it as evidence
    assert [c["title"] for c in cands] == ["Love.in.Disguise.S01E06.1080p", "wrong.year.junk"]


def test_all_rejected_returns_merged_evidence(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {
        PRIMARY: [{"title": "rej.primary", "accepted": False}],
        ENGLISH: [{"title": "rej.alias", "accepted": False}],
        THIRD: [{"title": "rej.third", "accepted": False}]})
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert calls == [PRIMARY, ENGLISH, THIRD]     # every name tried
    # every name's rejects are evidence — not just the primary's
    assert [c["title"] for c in cands] == ["rej.primary", "rej.alias", "rej.third"]


def test_usenet_alias_success(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {ENGLISH: [{"title": "nzb.hit"}]})
    cands, err = vpw._search_one_source("usenet", {}, "episode")
    assert calls == [PRIMARY, ENGLISH]
    assert [c["title"] for c in cands] == ["nzb.hit"] and cands[0]["source"] == "usenet"


def test_torrent_alias_hits_can_come_from_extto(monkeypatch):
    _rig(monkeypatch)
    _prowlarr(monkeypatch, {})
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: ([{"title": "ext.hit", "indexer_id": "extto"}]
                                                 if title == ENGLISH else [], None))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert [c["title"] for c in cands] == ["ext.hit"] and cands[0]["source"] == "extto"


def test_notes_from_earlier_attempts_survive(monkeypatch):
    """Primary ran with EXT.to down (note recorded) and found nothing; the
    alias then lands via Prowlarr. The degradation note must survive."""
    _rig(monkeypatch)
    _prowlarr(monkeypatch, {ENGLISH: [{"title": "alias.hit"}]})
    seen = []

    def fake_extto(ctx, title=None):
        seen.append(title)
        return (None, "FlareSolverr timeout") if title == PRIMARY else ([], None)

    monkeypatch.setattr(vpw, "_extto_hits_for_context", fake_extto)
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert [c["title"] for c in cands] == ["alias.hit"]
    assert err is not None and "EXT.to skipped" in err
    # …and the dead EXT.to leg is not re-queried under the alias either
    assert seen == [PRIMARY]


def test_fatal_on_alias_keeps_the_ran_verdict(monkeypatch):
    """Primary searched fine and found nothing; the alias query then fails
    to start. The source DID run — the result stays 'ran, empty' with the
    failure as a note, never (None, 'search didn't run')."""
    from core.video.slskd_search import build_query
    _rig(monkeypatch)
    q_alias = build_query("episode", ENGLISH, season=1, episode=6)
    seen = []

    def fake(query, **kw):
        seen.append(query)
        if query == q_alias:
            return {"started": False, "error": "slskd rate limited"}
        return {"started": True, "hits": []}

    monkeypatch.setattr("core.video.download_monitor._search_for_retry", fake)
    cands, err = vpw._search_one_source("soulseek", {}, "episode")
    assert cands == [] and err is not None and "alias search stopped" in err
    assert "slskd rate limited" in err and len(seen) == 2


def test_soulseek_alias_fallback_is_capped(monkeypatch):
    titles = ["T%d" % i for i in range(10)]
    _rig(monkeypatch, {"scope": "movie", "title": "T0", "titles": titles, "year": 2020})
    calls = _soulseek(monkeypatch, {})
    cands, err = vpw._search_one_source("soulseek", {}, "movie")
    assert cands == [] and len(calls) == 1 + vpw.ALIAS_TITLE_SEARCH_LIMIT


def test_extto_query_is_built_from_the_alias_title(monkeypatch):
    """The REAL _extto_hits_for_context: its new title parameter must be the
    name the EXT.to query is built from (not silently the primary)."""
    queries = []

    def fake_extto(query, **kw):
        queries.append(query)
        return {"configured": True, "hits": [{"title": "h"}]}

    monkeypatch.setattr("core.video.extto_search.extto_search", fake_extto)
    ctx = {"scope": "episode", "title": PRIMARY, "season": 1, "episode": 6}
    hits, err = vpw._extto_hits_for_context(ctx, ENGLISH)
    assert hits and queries == ["%s S01E06" % ENGLISH]
    hits, err = vpw._extto_hits_for_context(ctx)           # default: primary
    assert queries[-1] == "%s S01E06" % PRIMARY


def test_acceptable_titles_orders_manual_and_anime_before_tmdb(monkeypatch):
    """Search order is a budget: hand-added names and scene anime names sit
    ahead of TMDB's long alternative-title tail (the gate itself is a set)."""
    class _DB:
        def title_overrides_for(self, kind, tmdb_id=None):
            return {"manual_aliases": ["Manual Name"]}

        def anime_titles_for(self, kind, tmdb_id, season):
            return ["Anime Name"]

    class _Eng:
        def alt_titles_for(self, kind, tmdb_id):
            return ["TMDB One", "TMDB Two"]

    monkeypatch.setattr("api.video.get_video_db", lambda: _DB())
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: _Eng())
    got = vpw._acceptable_titles("Primary", "show", 42, season=1)
    assert got == ["Primary", "Manual Name", "Anime Name", "TMDB One", "TMDB Two"]


# ---------- round 2: overrides, leg death, isolation, snapshot notes ----------


def test_group_blocked_primary_hit_does_not_stop_the_ladder(monkeypatch):
    """The stop trigger must judge usability AFTER this title's release-group
    overrides: a primary-name hit from a blocked group is not usable, so the
    alias still gets searched — and can win with an allowed group."""
    _rig(monkeypatch)
    monkeypatch.setattr(vpw, "title_overrides_for_item",
                        lambda item, media_type: {"release_group_block": ["badgrp"]})
    calls = _prowlarr(monkeypatch, {
        PRIMARY: [{"title": "Show.S01E06.1080p-BADGRP"}],
        ENGLISH: [{"title": "Show.S01E06.1080p-GOODGRP"}],
    })
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx, title=None: ([], None))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert calls == [PRIMARY, ENGLISH]
    assert cands[0]["title"] == "Show.S01E06.1080p-GOODGRP" and cands[0]["accepted"] is True
    blocked = [c for c in cands if c["title"].endswith("BADGRP")]
    assert blocked and blocked[0]["accepted"] is False
    assert "blocked for this title" in (blocked[0].get("rejected") or "")


def test_dead_prowlarr_leg_is_not_requeried_per_alias(monkeypatch):
    """A Prowlarr that errors on the primary name is dead for this item's
    search: the remaining names must not each re-run the full Prowlarr
    search against it (EXT.to, which ran, still answers per name)."""
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {}, error="indexer timeout")
    extto_calls = []
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: (extto_calls.append(title), ([], None))[1])
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert cands == [] and err is not None and "Prowlarr skipped" in err
    assert calls == [PRIMARY]                    # one Prowlarr call, not three
    assert extto_calls == [PRIMARY, ENGLISH, THIRD]       # the live leg still tries each name


def test_dead_extto_leg_is_not_requeried_per_alias(monkeypatch):
    _rig(monkeypatch)
    calls = _prowlarr(monkeypatch, {})
    extto_calls = []
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: (extto_calls.append(title), (None, "extto down"))[1])
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert cands == [] and "EXT.to skipped" in (err or "")
    assert extto_calls == [PRIMARY]
    assert calls == [PRIMARY, ENGLISH, THIRD]


def test_fatal_on_alias_torrent_keeps_ran_verdict_and_stops(monkeypatch):
    """Both torrent legs fail together under the alias name: the ladder stops
    (both transports just failed), the third name is never tried, and the
    primary's clean 'ran, empty' verdict is returned with a note."""
    _rig(monkeypatch)

    def fake_prowlarr(scope, title, **kw):
        if title == ENGLISH:
            return {"configured": True, "error": "indexer exploded", "hits": []}
        return {"configured": True, "hits": []}

    prowlarr_titles = []
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search",
                        lambda scope, title, **kw: (prowlarr_titles.append(title),
                                                    fake_prowlarr(scope, title, **kw))[1])
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: ([], None) if title == PRIMARY else (None, "extto timeout"))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert cands == []
    assert err is not None and err.startswith("alias search stopped")
    assert "indexer exploded" in err and "extto timeout" in err
    assert prowlarr_titles == [PRIMARY, ENGLISH]          # THIRD never tried


def test_alias_attempt_exception_keeps_the_earlier_verdict(monkeypatch):
    """An alias search that raises must not destroy the primary's verdict or
    error the whole drain run: the ladder stops with a note."""
    _rig(monkeypatch)
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx, title=None: ([], None))
    # the primary's only hit is REJECTED, so the ladder runs — and the alias
    # attempt then blows up mid-search
    monkeypatch.setattr("api.video.downloads._evaluate_hits",
                        lambda hits, *a, **k: [{"title": h["title"], "filename": h["title"],
                                                "accepted": h["title"] != "rej.only",
                                                "rejected": None if h["title"] != "rej.only" else "no",
                                                "score": 0} for h in hits])

    def fake_prowlarr(scope, title, **kw):
        if title == ENGLISH:
            raise RuntimeError("prowlarr blew up")
        return {"configured": True, "hits": [{"title": "rej.only"}] if title == PRIMARY else []}

    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", fake_prowlarr)
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert [c["title"] for c in cands] == ["rej.only"]     # primary evidence intact
    assert err is not None and "alias search stopped" in err


def test_alias_fallback_works_for_season_scope(monkeypatch):
    _rig(monkeypatch, ctx=dict(CTX, scope="season", season=1, episode=None))
    calls = _prowlarr(monkeypatch, {ENGLISH: [{"title": "season.pack"}]})
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx, title=None: ([], None))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert calls == [PRIMARY, ENGLISH]
    assert [c["title"] for c in cands] == ["season.pack"]


def test_source_outcome_keeps_degradation_notes():
    cands = [{"title": "X", "accepted": False, "rejected": "nope"}]
    o = vpw.source_outcome(cands, err="alias search stopped — slskd rate limited")
    assert o["ran"] is True and o["note"] == "alias search stopped — slskd rate limited"
    assert vpw.source_outcome(cands)["note"] is None


def test_primary_evaluation_exception_still_propagates(monkeypatch):
    """Round 3: the isolation guard keys on the attempt index, not on `ran`
    (which is set before evaluation). An evaluation bug on the PRIMARY name
    must fail loudly exactly as it did for a single search — not read as
    'searched, found nothing'."""
    _rig(monkeypatch)
    _prowlarr(monkeypatch, {PRIMARY: [{"title": "a.hit"}]})

    def boom(hits, *a, **k):
        raise RuntimeError("evaluate blew up")

    monkeypatch.setattr("api.video.downloads._evaluate_hits", boom)
    with pytest.raises(RuntimeError):
        vpw._search_one_source("torrent", {}, "episode")


def test_alias_evaluation_exception_keeps_the_earlier_verdict(monkeypatch):
    _rig(monkeypatch)
    _prowlarr(monkeypatch, {PRIMARY: [{"title": "rej.only"}], ENGLISH: [{"title": "a.hit"}]})

    def flaky(hits, *a, **k):
        if hits and hits[0]["title"] == "a.hit":
            raise RuntimeError("evaluate blew up on the alias")
        return [{"title": h["title"], "filename": h["title"], "accepted": False,
                 "rejected": "no", "score": 0} for h in hits]

    monkeypatch.setattr("api.video.downloads._evaluate_hits", flaky)
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert [c["title"] for c in cands] == ["rej.only"]
    assert err is not None and "alias search stopped" in err


# ---------- Claude gate: parity and cap pinning ----------


def test_primary_only_result_is_returned_untouched(monkeypatch):
    """One name searched, nothing accepted: the candidates come back exactly
    as a single search returned them — no merge dedupe, no 250 cap — so
    source_outcome's counts stay the truth for the common case."""
    _rig(monkeypatch, ctx=dict(CTX, titles=[PRIMARY]))
    hits = [{"title": "same.release"}] * 300        # identical dedupe keys
    _prowlarr(monkeypatch, {PRIMARY: hits})
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert len(cands) == 300 and err is None


def test_winning_alias_candidates_are_never_truncated(monkeypatch):
    """The evidence cap bounds the merged rejects behind a winner, never the
    winner's own list: an alias yielding 300 accepted returns all 300."""
    _rig(monkeypatch)
    hits = [{"title": "hit.%03d" % i} for i in range(300)]
    _prowlarr(monkeypatch, {ENGLISH: hits})
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert len(cands) == 300
    assert all(c["accepted"] for c in cands)


def test_prowlarr_dying_mid_ladder_leaves_extto_to_win(monkeypatch):
    """Prowlarr errors under the alias name while EXT.to answers: the attempt
    still counts as ran, the note says Prowlarr was skipped, and the EXT.to
    hit wins."""
    _rig(monkeypatch)
    prowlarr_titles = []

    def fake_prowlarr(scope, title, **kw):
        prowlarr_titles.append(title)
        if title == ENGLISH:
            return {"configured": True, "error": "indexer exploded", "hits": []}
        return {"configured": True, "hits": []}

    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", fake_prowlarr)
    monkeypatch.setattr(vpw, "_extto_hits_for_context",
                        lambda ctx, title=None: ([{"title": "extto.hit"}], None)
                        if title == ENGLISH else ([], None))
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert [c["title"] for c in cands] == ["extto.hit"]
    assert err is not None and "Prowlarr skipped" in err and "indexer exploded" in err
    assert prowlarr_titles == [PRIMARY, ENGLISH]


def test_manual_all_sources_search_is_group_gated_too(monkeypatch):
    """The in-search overrides application is the only one on the manual
    all-sources path (it never applied them itself): a blocked-group release
    comes back rejected there too, visible rather than dropped."""
    from core.video import wishlist_search
    _rig(monkeypatch)
    monkeypatch.setattr(vpw, "title_overrides_for_item",
                        lambda item, media_type: {"release_group_block": ["badgrp"]})
    _prowlarr(monkeypatch, {PRIMARY: [{"title": "Show.S01E06.1080p-BADGRP"}]})
    monkeypatch.setattr("core.video.download_monitor._search_for_retry",
                        lambda q: {"started": False, "error": "slskd down"})
    cands, err = wishlist_search._all_source_search({}, "episode")
    blocked = [c for c in (cands or []) if c["title"].endswith("BADGRP")]
    assert blocked and all(c["accepted"] is False for c in blocked)
    assert all("blocked for this title" in (c.get("rejected") or "") for c in blocked)


# ---------- Claude gate pass 2: dedupe keys, caps, idempotency, anime ----------


def test_search_titles_dedupes_punctuation_variants():
    """'Big Brother (US)' and 'Big Brother US' are the same query at every
    indexer — they must not each burn an alias attempt."""
    ctx = {"title": "Big Brother (US)",
           "titles": ["Big Brother (US)", "Big Brother US", "big  brother (us)", "Big Brother"]}
    assert vpw._search_titles(ctx) == ["Big Brother (US)", "Big Brother"]


def test_same_title_uploads_with_different_urls_are_not_merged(monkeypatch):
    """Two distinct uploads sharing an indexer and release title are separate
    evidence: the dedupe key includes the download URL."""
    _rig(monkeypatch)

    def eval_with_urls(hits, *a, **k):
        return [{"title": h["title"], "filename": h["title"], "username": "idx",
                 "download_url": h.get("url"), "accepted": False,
                 "rejected": "no", "score": 0} for h in hits]

    monkeypatch.setattr("api.video.downloads._evaluate_hits", eval_with_urls)
    _prowlarr(monkeypatch, {
        PRIMARY: [{"title": "rel", "url": "http://x/1"}],
        ENGLISH: [{"title": "rel", "url": "http://x/2"}],
    })
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert len(cands) == 2
    assert {c["download_url"] for c in cands} == {"http://x/1", "http://x/2"}


def test_winner_plus_evidence_is_capped_at_the_limit(monkeypatch):
    """200 rejected under the primary + a 200-strong winner under the alias:
    the winner rides whole, the merged evidence behind it is capped."""
    _rig(monkeypatch)
    _prowlarr(monkeypatch, {
        PRIMARY: [{"title": "rej.%03d" % i} for i in range(200)],
        ENGLISH: [{"title": "win.%03d" % i} for i in range(200)],
    })
    # rig's evaluator accepts everything; make the primary's rejects real
    def picky(hits, *a, **k):
        return [{"title": h["title"], "filename": h["title"],
                 "accepted": not h["title"].startswith("rej."),
                 "rejected": None if not h["title"].startswith("rej.") else "no",
                 "score": 0} for h in hits]

    monkeypatch.setattr("api.video.downloads._evaluate_hits", picky)
    cands, err = vpw._search_one_source("torrent", {}, "episode")
    assert len(cands) == vpw.ALIAS_EVIDENCE_LIMIT
    assert sum(1 for c in cands if c["title"].startswith("win.")) == 200


def test_apply_group_overrides_is_idempotent():
    """The in-search application plus the caller's re-application must equal
    one application — block list, allow list, and pre-rejected candidates."""
    import copy
    base = [
        {"title": "Show-GOODGRP", "accepted": True, "rejected": None},
        {"title": "Show-BADGRP", "accepted": True, "rejected": None},
        {"title": "Show-GOODGRP", "accepted": False, "rejected": "wrong season"},
        {"title": "Show", "accepted": True, "rejected": None},
    ]
    for overrides in ({"release_group_block": ["badgrp"]},
                      {"release_group_allow": ["goodgrp"]},
                      {"release_group_block": ["badgrp"], "release_group_allow": ["goodgrp"]}):
        once = vpw.apply_group_overrides(copy.deepcopy(base), overrides)
        twice = vpw.apply_group_overrides(copy.deepcopy(once), overrides)
        assert twice == once, overrides


def test_soulseek_alias_query_for_anime_absolute_numbering(monkeypatch):
    """The Soulseek alias attempt builds its query from the alias name with
    the context's numbering (anime absolute) carried through."""
    ctx = dict(CTX, series_type="anime", absolute=6, season=None, episode=None)
    _rig(monkeypatch, ctx=ctx)
    built = []

    def fake_build(scope, title, **kw):
        built.append((title, kw))
        return "q::" + title

    monkeypatch.setattr("core.video.slskd_search.build_query", fake_build)
    monkeypatch.setattr("core.video.download_monitor._search_for_retry",
                        lambda q: {"started": True,
                                   "hits": [{"title": "hit"}] if q == "q::" + ENGLISH else []})
    cands, err = vpw._search_one_source("soulseek", {}, "episode")
    assert [t for t, _ in built] == [PRIMARY, ENGLISH]
    assert built[0][1].get("absolute") == 6
    assert [c["title"] for c in cands] == ["hit"]


def test_search_titles_keeps_non_latin_aliases_distinct():
    """The normalised dedupe key must not collapse non-Latin names: two
    Japanese titles that share only a digit are different searches."""
    ctx = {"title": "呪術廻戦 2", "titles": ["呪術廻戦 2", "進撃の巨人 2", "Jujutsu Kaisen"]}
    assert vpw._search_titles(ctx) == ["呪術廻戦 2", "進撃の巨人 2", "Jujutsu Kaisen"]


def test_usenet_leg_dying_mid_ladder_keeps_the_ran_verdict(monkeypatch):
    """Usenet is Prowlarr-only: an error under the alias name after a clean
    primary search stops the ladder with a note — it does not rewrite the
    source as 'didn't run'."""
    _rig(monkeypatch)

    def fake_prowlarr(scope, title, **kw):
        if title == ENGLISH:
            return {"configured": True, "error": "newznab down", "hits": []}
        return {"configured": True, "hits": []}

    seen = []
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search",
                        lambda scope, title, **kw: (seen.append(title), fake_prowlarr(scope, title, **kw))[1])
    cands, err = vpw._search_one_source("usenet", {}, "episode")
    assert cands == []
    assert err is not None and err.startswith("alias search stopped") and "newznab down" in err
    assert seen == [PRIMARY, ENGLISH]                     # THIRD never tried
