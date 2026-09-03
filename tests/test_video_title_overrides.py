"""Per-title acquisition overrides, at the three seams that act on them.

These narrow ONE title's download rules without touching the global config.
The load-bearing rule everywhere below is that an EMPTY override means "follow
the global settings", never "allow nothing" — an empty allow-list treated as a
filter would silently stop the title being grabbed at all, and the page would
report it as "no releases exist". That exact lie is what the refusal work in
test_video_wishlist_refusals.py was written to kill; it must not come back
wearing a different hat.
"""

from __future__ import annotations

import pytest

from core.automation.handlers.video_process_wishlist import (
    apply_group_overrides,
    chain_for,
    release_group,
    title_overrides_for_item,
)

CHAIN = ["torrent", "usenet", "soulseek"]


def _ovr(**kw):
    base = {"preferred_sources": [], "release_group_allow": [],
            "release_group_block": [], "pack_preference": "auto"}
    base.update(kw)
    return base


# ── reading the group off a release name ─────────────────────────────────────
@pytest.mark.parametrize("name,want", [
    ("Show.S01E01.1080p.WEB-DL-NTb", "NTb"),
    ("Movie.2021.2160p.UHD.BluRay-FLUX.mkv", "FLUX"),
    ("Show.S01E01.1080p-Tigole.mp4", "Tigole"),
    # WEB-DL is a source tag, not a group — the group is what follows the LAST dash.
    ("Show.S01E01.1080p.WEB-DL", "DL"),
    ("no group here", ""),
    ("", ""),
    (None, ""),
])
def test_release_group_reads_the_tail(name, want):
    assert release_group(name) == want


# ── seam 1: which sources get searched ───────────────────────────────────────
def test_chain_follows_the_global_order_when_nothing_is_preferred():
    assert chain_for(CHAIN, _ovr()) == CHAIN
    assert chain_for(CHAIN, {}) == CHAIN
    assert chain_for(CHAIN, None) == CHAIN


def test_chain_narrows_to_the_preferred_sources_in_the_users_order():
    assert chain_for(CHAIN, _ovr(preferred_sources=["soulseek", "torrent"])) == \
        ["soulseek", "torrent"]
    assert chain_for(CHAIN, _ovr(preferred_sources=["USENET"])) == ["usenet"]


def test_a_preference_the_global_config_cannot_serve_never_empties_the_chain():
    """Preferring usenet on a soulseek-only install must not search NOTHING and
    report it as 'no releases exist'."""
    assert chain_for(["soulseek"], _ovr(preferred_sources=["usenet"])) == ["soulseek"]
    assert chain_for(CHAIN, _ovr(preferred_sources=["carrier pigeon"])) == CHAIN


# ── seam 2: release groups ───────────────────────────────────────────────────
def _cands():
    return [{"filename": "Show.S01E01-NTb", "accepted": True, "rejected": None},
            {"filename": "Show.S01E01-YIFY", "accepted": True, "rejected": None},
            {"filename": "Show.S01E01.unparsable", "accepted": True, "rejected": None}]


def test_no_group_rules_leaves_every_candidate_alone():
    out = apply_group_overrides(_cands(), _ovr())
    assert [c["accepted"] for c in out] == [True, True, True]


def test_a_blocked_group_is_rejected_with_a_reason_not_dropped():
    """Dropping it would read as 'no releases exist'. The drain reports each
    release's rejection note, and 'you blocked that group' is exactly what the
    user needs told."""
    out = apply_group_overrides(_cands(), _ovr(release_group_block=["yify"]))
    assert len(out) == 3, "candidates are marked, never removed"
    by_group = {release_group(c["filename"]): c for c in out}
    assert by_group["YIFY"]["accepted"] is False
    assert "blocked" in by_group["YIFY"]["rejected"]
    assert by_group["NTb"]["accepted"] is True


def test_an_allow_list_rejects_everything_outside_it():
    out = apply_group_overrides(_cands(), _ovr(release_group_allow=["ntb"]))
    by_name = {c["filename"]: c for c in out}
    assert by_name["Show.S01E01-NTb"]["accepted"] is True
    assert by_name["Show.S01E01-YIFY"]["accepted"] is False


def test_an_unreadable_group_fails_an_allow_list_and_says_why():
    """A name we cannot read a group from must not pass as group ''. It is
    rejected, but with a message that names the allow list rather than a
    group that does not exist."""
    out = apply_group_overrides(_cands(), _ovr(release_group_allow=["ntb"]))
    unparsable = next(c for c in out if c["filename"].endswith("unparsable"))
    assert unparsable["accepted"] is False
    assert "unknown" in unparsable["rejected"] and "ntb" in unparsable["rejected"]


def test_an_unreadable_group_is_not_caught_by_a_block_list():
    """Blocking YIFY must not collaterally reject everything unparsable."""
    out = apply_group_overrides(_cands(), _ovr(release_group_block=["yify"]))
    unparsable = next(c for c in out if c["filename"].endswith("unparsable"))
    assert unparsable["accepted"] is True


def test_block_beats_allow_when_a_group_is_on_both_lists():
    out = apply_group_overrides(_cands(), _ovr(release_group_allow=["ntb"],
                                               release_group_block=["ntb"]))
    ntb = next(c for c in out if c["filename"].endswith("-NTb"))
    assert ntb["accepted"] is False and "blocked" in ntb["rejected"]


def test_group_matching_ignores_case():
    out = apply_group_overrides([{"filename": "X-nTb", "accepted": True}],
                                _ovr(release_group_block=["NTB"]))
    assert out[0]["accepted"] is False


# ── the lookup that feeds all three ──────────────────────────────────────────
def test_the_lookup_is_read_once_per_item(monkeypatch):
    calls = []

    class _DB:
        def title_overrides_for(self, kind, *, tmdb_id=None, library_id=None):
            calls.append((kind, tmdb_id, library_id))
            return _ovr(release_group_block=["YIFY"])

    import api.video as videoapi
    monkeypatch.setattr(videoapi, "_video_db", _DB())
    item = {"show_tmdb_id": 1396}
    assert title_overrides_for_item(item, "episode")["release_group_block"] == ["YIFY"]
    title_overrides_for_item(item, "episode")
    title_overrides_for_item(item, "episode")
    assert len(calls) == 1, "the drain hands the same dict around; one read covers the run"
    assert calls[0][0] == "show"


def test_a_lookup_failure_never_stops_a_grab(monkeypatch):
    """An override is a refinement. If reading it breaks, the title still gets
    hunted under the global config."""
    class _Boom:
        def title_overrides_for(self, *a, **kw):
            raise RuntimeError("db gone")

    import api.video as videoapi
    monkeypatch.setattr(videoapi, "_video_db", _Boom())
    out = title_overrides_for_item({"tmdb_id": 27205}, "movie")
    assert out == _ovr()
    assert chain_for(CHAIN, out) == CHAIN


# ── seam 3: season packs ─────────────────────────────────────────────────────
@pytest.mark.parametrize("pack_on,pref,eligible", [
    (True, "auto", True),      # global on, no opinion -> packs
    (True, "never", False),    # this show opts out even though packs are on
    (True, "prefer", True),
    (False, "auto", False),    # global off, no opinion -> no packs
    (False, "never", False),
    (False, "prefer", True),   # this show opts IN even though packs are off
])
def test_pack_preference_overrides_the_global_setting(monkeypatch, pack_on, pref, eligible):
    import core.automation.handlers.video_process_wishlist as vpw
    monkeypatch.setattr(vpw, "title_overrides_for_item",
                        lambda item, mt: _ovr(pack_preference=pref))
    item = {"show_tmdb_id": 1, "season_number": 1, "episode_number": 2}
    got = (vpw._pack_preference_for(item) == "prefer"
           or (pack_on and vpw._pack_preference_for(item) != "never"))
    assert got is eligible


def test_pack_preference_defaults_to_auto_when_unset(monkeypatch):
    import core.automation.handlers.video_process_wishlist as vpw
    monkeypatch.setattr(vpw, "title_overrides_for_item", lambda item, mt: {})
    assert vpw._pack_preference_for({"show_tmdb_id": 1}) == "auto"
