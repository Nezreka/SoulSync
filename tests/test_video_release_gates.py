"""The two release gates that were silently unsatisfiable, and the swarm-health floor.

Found by reading Boulder's live install: 173 wished episodes, every one searched
every hour, `search_attempts` up to 198, and essentially nothing grabbed. Two
independent gates were rejecting releases in ways no setting could undo.

**The keyspace hole.** ``quality_eval.tier_key`` built its answer by concatenating
``source + '-' + resolution``, but ``quality_profile.TIERS`` is a CURATED ladder, not
the full product. So ``tier_key`` could name tiers the ladder never defined —
``webrip-720p`` (one of the commonest TV release shapes), ``hdtv-2160p``, and every
``<source>-480p``. Those releases died on "isn't in your enabled tiers" with no
toggle anywhere that could accept them. The mirror-image bug: ``sdtv`` sat in the
ladder AND in the settings UI while ``tier_key`` could never return it, so the SDTV
switch did nothing at all.

The fix is two-sided, and so is the test: the ladder gained the missing rungs, and
``tier_key`` now guarantees its output is '' or a ladder member. Both directions are
pinned below — no key outside the ladder, and no ladder tier that nothing can reach.
A one-directional test would have missed the dead ``sdtv`` switch entirely.

**The missing swarm floor.** The video ranker had no minimum-seeder rule, so the
drain happily picked 0-seeder torrents. Live evidence: every Prowlarr candidate for
the stuck titles had ``seeders=0``, and 10 of the 12 torrents in Boulder's
qBittorrent were parked at 0% in ``stalledDL``/``metaDL``. The music side already
gained this floor with #1139; this is the video twin. Usenet and Soulseek have no
seeders and must never be gated on them — that exemption is the easy thing to get
wrong, so it is pinned twice.
"""

from __future__ import annotations

import pytest

from core.video.quality_eval import evaluate_release, tier_key
from core.video.quality_profile import TIERS, default_profile, normalize
from core.video.release_parse import parse_release

# Every source the parser can emit, plus the very common "no recognised source".
_SOURCES = (None, "remux", "bluray", "web-dl", "webrip", "hdtv", "dvd",
            "cam", "screener", "workprint")
# Every resolution the parser can emit, plus "couldn't tell".
_RESOLUTIONS = (None, "2160p", "1080p", "720p", "480p")


# ── the ladder and the keyspace must be the same shape ───────────────────────

@pytest.mark.parametrize("source", _SOURCES)
@pytest.mark.parametrize("resolution", _RESOLUTIONS)
def test_tier_key_never_names_a_tier_the_ladder_lacks(source, resolution):
    """The invariant, walked over the whole product space. A key outside the
    ladder is unreachable by construction: no profile can enable it, so the
    release is rejected forever however the user configures things."""
    key = tier_key(source, resolution)
    assert key == "" or key in TIERS, (
        "tier_key(%r, %r) = %r, which is not in the ladder — releases of that "
        "shape can never be accepted" % (source, resolution, key))


def test_every_ladder_tier_is_reachable():
    """The other direction: a tier nothing can produce is a dead switch in the
    settings UI. ``sdtv`` was exactly that — present in the ladder, rendered as a
    toggle, and impossible to hit, because SD releases were named
    ``<source>-480p`` instead."""
    reachable = {tier_key(s, r) for s in _SOURCES for r in _RESOLUTIONS}
    orphans = sorted(set(TIERS) - reachable)
    assert orphans == [], (
        "these tiers are offered in the profile but nothing can produce them: %s"
        % orphans)


def test_sd_collapses_onto_the_source_less_sd_tiers():
    """The ladder deliberately stops splitting by source below 720p: disc-sourced
    SD is `dvd`, everything else broadcast-grade is `sdtv`."""
    assert tier_key("dvd", "480p") == "dvd"
    assert tier_key("dvd", None) == "dvd"
    for src in ("web-dl", "webrip", "hdtv", "bluray", None):
        assert tier_key(src, "480p") == "sdtv", src


def test_junk_sources_still_have_no_tier():
    """Guard against the fix over-reaching: cam/screener/workprint must stay
    tier-less so the reject list and the 'unknown quality' rejection still bite."""
    for src in ("cam", "screener", "workprint"):
        assert tier_key(src, "1080p") == ""
    assert tier_key(None, None) == ""


def test_a_lone_resolution_still_reads_as_web():
    """Pre-existing behaviour that must survive: lots of releases tag 1080p and
    no source, and assuming WEB keeps them on the ladder (ffprobe confirms the
    truth after download)."""
    assert tier_key(None, "1080p") == "web-1080p"


# ── the concrete releases that were dying ────────────────────────────────────

def _accept(name, profile=None, **kw):
    return evaluate_release(parse_release(name), profile or default_profile(),
                            scope="episode", want_season=kw.pop("season", 1),
                            want_episode=kw.pop("episode", 1), **kw)


def test_a_720p_webrip_episode_is_acceptable():
    """The headline regression. WEBRip existed only at 1080p, so this landed on
    ``webrip-720p``, which no profile contained."""
    v = _accept("Guys.Grocery.Games.S01E01.720p.WEBRip.x264-GROUP")
    assert v["tier"] == "webrip-720p"
    assert v["accepted"] is True, v["rejected"]


def test_an_sd_episode_is_rejected_by_default_but_the_toggle_now_works():
    """SD stays off by default — that is a deliberate product choice, not the
    bug. The bug was that turning it ON changed nothing."""
    name = "House.Hunters.International.S208E13.480p.HDTV.x264-GROUP"
    assert _accept(name, season=208, episode=13)["accepted"] is False

    prof = normalize({**default_profile(),
                      "tiers": [{"key": k, "enabled": k == "sdtv"} for k in TIERS]})
    v = _accept(name, prof, season=208, episode=13)
    assert v["tier"] == "sdtv"
    assert v["accepted"] is True, v["rejected"]


def test_a_576p_release_is_sd_not_a_lost_tier():
    """The parser folds 576p into 480p; PAL broadcast rips must not fall off the
    ladder on the way."""
    assert parse_release("A.Show.S01E01.576p.HDTV.x264")["resolution"] == "480p"
    assert tier_key("hdtv", "480p") == "sdtv"


# ── swarm health ─────────────────────────────────────────────────────────────

def _torrent(name, seeders, profile=None):
    return evaluate_release(parse_release(name), profile or default_profile(),
                            scope="episode", want_season=1, want_episode=1,
                            seeders=seeders)


def test_a_dead_swarm_is_not_an_acceptable_release():
    """A 0-seeder torrent cannot finish: the client parks it on 'downloading
    metadata' or stalls it at 0% forever. Grabbing it anyway is how a client
    fills with dead torrents while the wishlist reports a fruitless search."""
    v = _torrent("A.Show.S01E01.1080p.WEB.h264-GROUP", 0)
    assert v["accepted"] is False
    assert "seeder" in v["rejected"].lower()


def test_one_seeder_clears_the_default_floor():
    assert _torrent("A.Show.S01E01.1080p.WEB.h264-GROUP", 1)["accepted"] is True


def test_a_higher_floor_rejects_a_thin_swarm_and_says_the_numbers():
    prof = normalize({**default_profile(), "min_seeders": 5})
    v = _torrent("A.Show.S01E01.1080p.WEB.h264-GROUP", 2, prof)
    assert v["accepted"] is False
    assert "2" in v["rejected"] and "5" in v["rejected"]


def test_a_zero_floor_turns_the_gate_off():
    """0 means 'no floor' — the user who wants to chase dead swarms may."""
    prof = normalize({**default_profile(), "min_seeders": 0})
    assert _torrent("A.Show.S01E01.1080p.WEB.h264-GROUP", 0, prof)["accepted"] is True


def test_sources_without_seeders_are_never_gated():
    """Usenet and Soulseek have no swarm. Passing None must read as 'not
    applicable', never as 'zero' — gating them would silently kill both."""
    v = evaluate_release(parse_release("A.Show.S01E01.1080p.WEB.h264-GROUP"),
                         default_profile(), scope="episode", want_season=1,
                         want_episode=1, seeders=None)
    assert v["accepted"] is True


def test_the_evaluator_only_hands_the_judge_seeders_for_torrents():
    """The exemption above is only real if the caller honours it. A usenet hit
    that an indexer reported as 0 seeders must still be judged as seeder-less."""
    import api.video.downloads as dl
    hits = [{"title": "A.Show.S01E01.1080p.WEB.h264-GROUP", "size_bytes": 1_200_000_000,
             "protocol": "usenet", "seeders": 0, "username": "indexer"}]
    out = dl._evaluate_hits(hits, default_profile(), "episode", 1, 1,
                            blocked=frozenset(), blocked_users=frozenset())
    assert out[0]["accepted"] is True, out[0]["rejected"]


def test_a_torrent_hit_through_the_evaluator_is_gated():
    """Same path, protocol flipped — the gate must actually engage in production
    shape, not only when evaluate_release is called directly."""
    import api.video.downloads as dl
    hits = [{"title": "A.Show.S01E01.1080p.WEB.h264-GROUP", "size_bytes": 1_200_000_000,
             "protocol": "torrent", "seeders": 0, "username": "indexer"}]
    out = dl._evaluate_hits(hits, default_profile(), "episode", 1, 1,
                            blocked=frozenset(), blocked_users=frozenset())
    assert out[0]["accepted"] is False
    assert "seeder" in out[0]["rejected"].lower()


# ── the profile carries the new knob safely ──────────────────────────────────

def test_a_profile_stored_before_the_gate_existed_gains_the_floor():
    """Installs written before min_seeders existed must not keep grabbing dead
    torrents just because their stored blob predates the field."""
    old = {k: v for k, v in default_profile().items() if k != "min_seeders"}
    assert normalize(old)["min_seeders"] == 1


def test_an_explicit_zero_survives_normalisation():
    """...but an explicit 0 is a real choice and must not be 'helpfully' reset
    to the default by the same code path."""
    assert normalize({**default_profile(), "min_seeders": 0})["min_seeders"] == 0


def test_the_floor_is_clamped_to_something_sane():
    assert normalize({**default_profile(), "min_seeders": -5})["min_seeders"] == 0
    assert normalize({**default_profile(), "min_seeders": 10 ** 6})["min_seeders"] == 100
    assert normalize({**default_profile(), "min_seeders": "junk"})["min_seeders"] == 1


def test_the_new_rungs_are_on_by_default_only_where_their_siblings_are():
    """webrip-720p joins its enabled 720p siblings (it exists to fix a false
    rejection, so shipping it off would preserve the bug). The 4K rungs join
    the other 4K tiers: off."""
    on = {t["key"] for t in default_profile()["tiers"] if t["enabled"]}
    assert "webrip-720p" in on
    assert "webrip-2160p" not in on and "hdtv-2160p" not in on
