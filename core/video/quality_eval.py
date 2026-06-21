"""Evaluate a video file/release against the quality profile.

Two consumers, one source of truth:
  - the Download modal — to tell the user whether the copy they already own meets
    their quality target (or is eligible for an upgrade), and
  - the (later-phase) download engine — to filter/score search results.

Pure functions (no DB, no network) so they're unit-tested in isolation. Isolated —
imports only the sibling video ``quality_profile`` constants; nothing from music.
"""

from __future__ import annotations

from typing import Any

# Resolution ranking (higher = better). The loose cutoff and the owned-vs-target
# check both compare on this rank, so "1920x1080", "1080p" and "1080" all agree.
_RES_RANK = (("2160", 4), ("4k", 4), ("1440", 3), ("1080", 3),
             ("720", 2), ("576", 1), ("480", 1), ("sd", 1))
_RES_LABEL = {4: "4K", 3: "1080p", 2: "720p", 1: "SD", 0: ""}


def resolution_rank(res: Any) -> int:
    """Map a raw resolution token to a rank int (4=4K … 1=SD, 0=unknown)."""
    s = str(res or "").strip().lower()
    for token, rank in _RES_RANK:
        if token in s:
            return rank
    return 0


def resolution_label(res: Any) -> str:
    """A friendly resolution label ('4K' / '1080p' / '720p' / 'SD' / '')."""
    return _RES_LABEL.get(resolution_rank(res), "")


def _cutoff_label(cutoff: str) -> str:
    return _RES_LABEL.get(resolution_rank(cutoff), "best")


def _codec_family(codec: Any) -> str:
    """Normalise a stored video codec to a reject-list key ('x264'/'hevc'/'av1')."""
    s = str(codec or "").strip().lower()
    if not s:
        return ""
    if "av1" in s:
        return "av1"
    if "265" in s or "hevc" in s:
        return "hevc"
    if "264" in s or "avc" in s:
        return "x264"
    return ""


def meets_cutoff(resolution: Any, profile: dict) -> bool:
    """Does an owned item's resolution already satisfy the loose cutoff target?
    An empty cutoff ('always upgrade') is never 'good enough'."""
    cut = (profile or {}).get("cutoff_resolution", "")
    if not cut:
        return False
    return resolution_rank(resolution) >= resolution_rank(cut)


def evaluate_owned(file: Any, profile: Any) -> dict:
    """Verdict for a copy the user already owns, vs their quality profile.

    Returns ``{"meets": bool, "resolution_label": str, "reasons": [{ok, text}]}``
    — ``meets`` False means it's eligible for an upgrade. ``reasons`` is an ordered,
    render-ready list of the checks (ok=True is reassuring, ok=False explains why
    an upgrade would help)."""
    file = file if isinstance(file, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    reasons: list = []
    meets = True

    res = file.get("resolution")
    cut = profile.get("cutoff_resolution", "")
    if not cut:
        meets = False
        reasons.append({"ok": False, "text": "You're set to always chase the best — eligible for an upgrade."})
    elif resolution_rank(res) >= resolution_rank(cut):
        reasons.append({"ok": True, "text": "Meets your " + _cutoff_label(cut) + " target."})
    else:
        meets = False
        reasons.append({"ok": False, "text": "Below your " + _cutoff_label(cut) + " target — eligible for an upgrade."})

    fam = _codec_family(file.get("video_codec"))
    if fam and fam in (profile.get("rejects") or []):
        meets = False
        reasons.append({"ok": False, "text": "Its " + fam + " codec is on your reject list."})

    return {
        "meets": meets,
        "resolution_label": resolution_label(res),
        "reasons": reasons,
    }


# ── release (search hit) evaluation ───────────────────────────────────────────
# Map a parsed source → the tier-key prefix used in the quality profile's ladder.
_SRC_TIER = {"remux": "remux", "bluray": "bluray", "web-dl": "web",
             "webrip": "webrip", "hdtv": "hdtv", "dvd": "dvd"}
_RES_SCORE = {"2160p": 400, "1080p": 300, "720p": 200, "480p": 100}
_SRC_SCORE = {"remux": 90, "bluray": 70, "web-dl": 55, "webrip": 40, "hdtv": 25, "dvd": 10}


def tier_key(source, resolution) -> str:
    """The quality-ladder key for a parsed (source, resolution), or '' if it isn't a
    ladder tier (junk sources like cam/screener have no tier)."""
    pre = _SRC_TIER.get(source)
    if not pre:
        # A loosely-named release with a known resolution but NO recognised source
        # (very common — lots of releases tag '1080p' but not the source) → assume web
        # so it still lands on a tier instead of being rejected as 'unknown quality'.
        # ffprobe verifies the real quality after download.
        if resolution and not source:
            pre = "web"
        else:
            return ""
    if pre == "dvd":
        return "dvd"
    return (pre + "-" + resolution) if resolution else ""


def _scope_ok(parsed, scope, want_season, want_episode):
    """Validate a hit actually matches what was searched (Sonarr-style): an episode
    search wants SxxExx, a season search wants the whole season PACK, a show search
    wants a complete-series pack."""
    season, episode = parsed.get("season"), parsed.get("episode")
    if scope == "movie":
        return (None, None) if season is None else (None, "This is a TV release, not the movie")
    if scope == "episode":
        if episode is None:
            return None, "Not a single episode"
        if want_season is not None and season != want_season:
            return None, "Wrong season"
        if want_episode is not None and episode != want_episode:
            return None, "Wrong episode"
        return None, None
    if scope == "season":
        if not parsed.get("is_season_pack"):
            return None, "Not a full-season pack"
        if want_season is not None and season != want_season:
            return None, "Wrong season"
        return None, None
    if scope == "series":
        return (None, None) if parsed.get("is_series_pack") else (None, "Not a complete-series pack")
    return None, None


def evaluate_release(parsed, profile, *, scope="movie", want_season=None,
                     want_episode=None, size_gb=None) -> dict:
    """Judge a parsed search hit against the quality profile + the search scope.

    Returns ``{accepted, score, rejected, tier, quality_label}`` — ``accepted`` False
    means it's filtered out (``rejected`` says why); ``score`` ranks the keepers."""
    parsed = parsed if isinstance(parsed, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    res, source = parsed.get("resolution"), parsed.get("source")
    rejects = profile.get("rejects") or []
    rejected = None

    # 1) hard rejects — junk source / 3D / rejected codec
    if source in ("cam", "screener", "workprint") and source in rejects:
        rejected = source + " is on your reject list"
    fam = _codec_family(parsed.get("codec"))
    if not rejected and fam and fam in rejects:
        rejected = fam + " codec is on your reject list"
    if not rejected and parsed.get("three_d") and "3d" in rejects:
        rejected = "3D is on your reject list"

    # 2) must be an enabled ladder tier
    tier = tier_key(source, res)
    if not rejected:
        enabled = {t.get("key") for t in (profile.get("tiers") or []) if t.get("enabled")}
        if not tier:
            rejected = "Unknown / unsupported quality"
        elif tier not in enabled:
            rejected = (resolution_label(res) or "This quality") + " " + (source or "") + " isn't in your enabled tiers"

    # 3) HDR required (a real filter when set)
    if not rejected and profile.get("prefer_hdr") == "require" and not parsed.get("hdr"):
        rejected = "HDR required but this is SDR"

    # 4) scope validation (episode vs season pack vs series pack)
    if not rejected:
        _, scope_reason = _scope_ok(parsed, scope, want_season, want_episode)
        if scope_reason:
            rejected = scope_reason

    # 5) size guard (movie/episode only — packs are legitimately large)
    if not rejected and size_gb:
        cap = profile.get("max_movie_gb") if scope == "movie" else (profile.get("max_episode_gb") if scope == "episode" else 0)
        if cap and size_gb > cap:
            rejected = "Over your " + str(cap) + " GB size cap"

    # score the keepers (higher = better)
    score = _RES_SCORE.get(res, 0) + _SRC_SCORE.get(source, 0)
    if profile.get("prefer_codec") not in (None, "any") and fam == profile.get("prefer_codec"):
        score += 40
    if parsed.get("hdr") and profile.get("prefer_hdr") in ("prefer", "require"):
        score += 30
    if parsed.get("audio") in ("atmos", "truehd", "dts-hd"):
        score += 15
    if profile.get("prefer_repack") and (parsed.get("repack") or parsed.get("proper")):
        score += 10

    label = " · ".join([x for x in [resolution_label(res),
                        (source or "").upper() if source else "", fam.upper() if fam else ""] if x])
    return {"accepted": rejected is None, "score": score, "rejected": rejected,
            "tier": tier, "quality_label": label}


__all__ = ["resolution_rank", "resolution_label", "meets_cutoff", "evaluate_owned",
           "tier_key", "evaluate_release"]
