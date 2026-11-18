"""resolve_request_quality (#2168): the one place every request path asks
which quality a request gets, and whether it needs an OK first.

pure: effective maps from the shared fixtures, quality options as
core.video.quality_profile.quality_options returns them."""

from __future__ import annotations

import pytest

from core import permissions as P
from tests.permissions_fixtures import admin_ctx, broken_ctx, legacy_row, member_ctx, member_perms

OPTIONS = [
    {"id": 0, "name": "Default", "media_type": None, "tags": []},
    {"id": 2, "name": "Movie 1080p", "media_type": "movie", "tags": []},
    {"id": 3, "name": "Movie 4K", "media_type": "movie", "tags": ["4k"]},
    {"id": 5, "name": "TV 720p/1080p", "media_type": "tv", "tags": []},
    {"id": 6, "name": "TV 720p/1080p EXTENDED", "media_type": None, "tags": ["extended"]},
]


def rq(ctx, kind, wanted, **kw):
    kw.setdefault("options", OPTIONS)
    return P.resolve_request_quality(ctx, kind, wanted, **kw)


# ── admins ───────────────────────────────────────────────────────────────

def test_admin_gets_what_they_ask_for_never_refused_never_asked():
    a = admin_ctx()
    assert rq(a, "movie", 3).as_dict() == {"profile_id": 3, "needs_approval": False, "refused": False,
                                           "error": None, "note": ""}
    assert rq(a, "movie", None).profile_id is None              # the normal default chain
    assert rq(a, "movie", 5).profile_id == 5                    # even the other kind's: admins aren't limited
    d = rq(a, "tv", 99)                                         # unknown id: the default, noted
    assert d.profile_id is None and not d.refused and "unknown" in d.note
    assert P.resolve_request_quality({"id": 1}, "movie", 2).profile_id == 2   # profile 1 row


# ── open sets (today's installs) ─────────────────────────────────────────

def test_open_set_member_keeps_todays_default_chain():
    adult = member_ctx("adult")
    d = rq(adult, "movie", None)
    assert d.profile_id is None and not d.needs_approval and not d.refused
    assert rq(adult, "movie", 3).profile_id == 3                # any movie profile
    assert rq(adult, "movie", 6).profile_id == 6                # a both profile
    assert rq(member_ctx("teen"), "movie", None).needs_approval  # ask first mode


def test_wrong_media_type_is_refused():
    d = rq(member_ctx("adult"), "movie", 5)                     # a tv profile on a movie
    assert d.refused and d.error == P.QUALITY_NOT_ALLOWED
    d = rq(member_ctx("adult"), "tv", 3)
    assert d.refused and d.error == P.QUALITY_NOT_ALLOWED


def test_unknown_id_gets_the_default_and_a_note():
    d = rq(member_ctx("adult", **{"movies.quality": [2, 3], "movies.quality_default": 2}), "movie", 4242)
    assert d.profile_id == 2 and not d.refused and "unknown" in d.note


def test_without_options_every_id_in_the_set_counts():
    ctx = member_ctx("adult", **{"movies.quality": [2]})
    assert P.resolve_request_quality(ctx, "movie", 2).profile_id == 2
    assert P.resolve_request_quality(ctx, "movie", 3).refused
    assert P.resolve_request_quality(member_ctx("adult"), "movie", 77).profile_id == 77


# ── allowed sets, defaults ───────────────────────────────────────────────

def test_listed_set_allows_only_its_ids():
    ctx = member_ctx("adult", **{"movies.quality": [2]})
    assert rq(ctx, "movie", 2).profile_id == 2
    d = rq(ctx, "movie", 3)
    assert d.refused and d.error == P.QUALITY_NOT_ALLOWED
    assert rq(ctx, "movie", None).profile_id == 2               # one allowed: it is the default


def test_default_order_own_then_kind_default_then_first():
    two = {"movies.quality": [2, 3]}
    assert rq(member_ctx("adult", **two, **{"movies.quality_default": 3}), "movie", None).profile_id == 3
    assert rq(member_ctx("adult", **two), "movie", None, kind_default=3).profile_id == 3
    assert rq(member_ctx("adult", **two), "movie", None, kind_default=5).profile_id == 2   # not in set
    assert rq(member_ctx("adult", **two), "movie", None).profile_id == 2


def test_default_outside_the_set_falls_back():
    # a stored default the set no longer holds (choice rows are clamped, but a
    # map handed in raw must still never escape the set)
    eff = member_perms("adult", **{"movies.quality": [2, 3]})
    eff["movies.quality_default"] = 5
    d = P.resolve_request_quality(member_ctx("adult"), "movie", None, perms=eff, options=OPTIONS)
    assert d.profile_id == 2


def test_open_set_with_own_default():
    assert rq(member_ctx("adult", **{"movies.quality_default": 2}), "movie", None).profile_id == 2


def test_nothing_fits_the_kind_is_refused():
    ctx = member_ctx("adult", **{"movies.quality": [5]})        # only a tv profile listed
    d = rq(ctx, "movie", None)
    assert d.refused and d.error == P.QUALITY_NOT_ALLOWED


# ── approval ─────────────────────────────────────────────────────────────

def test_quality_ask_ids_and_the_4k_token():
    ctx = member_ctx("adult", **{"movies.quality_ask": [3]})
    assert rq(ctx, "movie", 3).needs_approval
    assert not rq(ctx, "movie", 2).needs_approval
    ctx4 = member_ctx("adult", **{"movies.quality_ask": ["4k"]})
    assert rq(ctx4, "movie", 3).needs_approval                  # tagged 4k
    assert not rq(ctx4, "movie", 2).needs_approval
    assert rq(ctx4, "movie", None, kind_default=3).needs_approval   # a 4k household default asks too
    assert not rq(ctx4, "movie", None, kind_default=2).needs_approval


def test_ask_first_mode_always_needs_approval():
    ctx = member_ctx("adult", **{"movies.get": "ask"})
    assert rq(ctx, "movie", 2).needs_approval
    assert not rq(ctx, "tv", 5).needs_approval                  # tv is its own mode


# ── access, modes, the ceiling ───────────────────────────────────────────

def test_cant_request_and_no_access_are_refused():
    d = rq(member_ctx("adult", **{"tv.get": "none"}), "tv", None)
    assert d.refused and d.error == P.NOT_ALLOWED
    tv_only = member_ctx("adult", **{"movies.access": False})
    d = rq(tv_only, "movie", 2)
    assert d.refused and d.error == P.NOT_ALLOWED
    assert not rq(tv_only, "tv", 5).refused
    assert rq(member_ctx("guest"), "movie", None).refused       # music only


def test_the_policy_ceiling_holds():
    # the policy allows only 1080p for movies and makes 4K ask; a personal
    # change above it stays inert
    policy = {"movies.quality": [0, 2], "movies.quality_ask": ["4k"]}
    ctx = member_ctx("adult", policy=policy, **{"movies.quality": [2, 3]})
    assert rq(ctx, "movie", 2).profile_id == 2
    assert rq(ctx, "movie", 3).refused
    assert rq(ctx, "movie", None).profile_id == 2
    ask = member_ctx("adult", policy={"movies.get": "ask"})
    assert rq(ask, "movie", 2).needs_approval                   # policy mode ceiling


def test_broken_map_and_legacy_rows():
    d = rq(broken_ctx(), "movie", None)
    assert d.refused and d.error == P.NOT_ALLOWED
    # a row passed without a map is read from its own columns
    row = legacy_row(allowed_sides="both", can_download=False)
    d = P.resolve_request_quality(row, "movie", 2, options=OPTIONS)
    assert d.profile_id == 2 and d.needs_approval
    assert P.resolve_request_quality(legacy_row(), "movie", None).refused   # music-only row


def test_music_has_no_needs_ok_row():
    ctx = member_ctx("adult", **{"music.quality": [1, 2], "music.quality_default": 2})
    assert P.resolve_request_quality(ctx, "music", None).profile_id == 2
    assert P.resolve_request_quality(ctx, "music", 1).profile_id == 1
    assert P.resolve_request_quality(ctx, "music", 9).refused
    assert P.resolve_request_quality(member_ctx("teen"), "music", 4).needs_approval


def test_kinds_and_bad_input():
    with pytest.raises(ValueError):
        P.resolve_request_quality(member_ctx(), "book", 1)
    for k in ("show", "episode", "season", "tv"):
        assert rq(member_ctx("adult"), k, 5).profile_id == 5
    assert rq(member_ctx("adult"), "movie", "2").profile_id == 2
    assert rq(member_ctx("adult"), "movie", "junk").profile_id is None   # unreadable = nothing asked
    assert rq(member_ctx("adult"), "movie", True).profile_id is None


def test_needs_ok_helper():
    eff = member_perms("adult", **{"tv.quality_ask": [6, "4k"]})
    assert P.needs_ok(eff, "tv", 6)
    assert not P.needs_ok(eff, "tv", 5)
    assert P.needs_ok(eff, "movie", 3, options=OPTIONS) is False   # movies has no flags here
    assert P.needs_ok(member_perms("adult", **{"movies.quality_ask": ["4k"]}), "movie", 3, options=OPTIONS)
    assert not P.needs_ok(None, "movie", 3)
