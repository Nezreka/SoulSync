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


def _as_year(v: Any):
    """Coerce a wanted-year hint (int / '2026' / '2026-07-01') to an int, or None."""
    try:
        return int(str(v)[:4]) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


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


def _scope_ok(parsed, scope, want_season, want_episode, want_year=None, want_title=None,
              want_date=None, want_absolute=None):
    """Validate a hit actually matches what was searched (Sonarr/Radarr-style): the
    TITLE must match the wanted film/show (not just be a substring — 'The Cloverfield
    Paradox 2018' must NOT satisfy a search for 'Paradox (2017)'), an episode search
    wants SxxExx, a season search wants the whole season PACK, a show search wants a
    complete-series pack, and a movie's release YEAR must match the wanted year (±1 for
    production-vs-release slop)."""
    # Title gate first — applies to every scope. Rejects a confident title mismatch;
    # an unknown/unisolable title passes through to the scope/year checks below.
    if want_title:
        from core.video.release_parse import extract_title, titles_match
        if not titles_match(parsed.get("title"), want_title):
            return None, "Wrong title (%s — wanted %s)" % (
                extract_title(parsed.get("title")) or "?", want_title)
    season, episode = parsed.get("season"), parsed.get("episode")
    if scope == "movie":
        if season is not None:
            return None, "This is a TV release, not the movie"
        py, wy = parsed.get("year"), _as_year(want_year)
        # An EARLIER-year release is a different, older film ("Moana 2 … 2025" for the 2026
        # "Moana", "Troy The Odyssey 2017" for the 2026 one). A slightly later year is allowed
        # (a film's home release can land the next calendar year). Unknown year → no judgement.
        if wy and py and (py < wy or py > wy + 1):
            return None, "Wrong year (%s — wanted %s)" % (py, wy)
        return None, None
    if scope == "episode":
        if episode is None:
            # Daily series (Sonarr-style): releases are named by AIR DATE, not SxxExx
            # ('The.Daily.Show.2026.07.08...'). A date match IS the episode identity.
            if want_date and parsed.get("air_date") == want_date:
                return None, None
            # Anime (Sonarr's absolute numbering): releases are named by ABSOLUTE
            # episode number, no season ('[SubsPlease] One Piece - 1071 (1080p)').
            # The wanted absolute number appearing in the title region IS a match.
            if want_absolute:
                from core.video.release_parse import has_absolute_episode
                if has_absolute_episode(parsed.get("title"), want_absolute):
                    return None, None
            return None, "Not a single episode"
        if want_season is not None and season != want_season:
            # A date match trumps a numbering mismatch — scene season numbering for
            # dailies rarely agrees with TMDB's, but the air date is unambiguous.
            if want_date and parsed.get("air_date") == want_date:
                return None, None
            return None, "Wrong season"
        # A multi-episode file (S01E01E02 / E01-03) satisfies any episode it spans.
        ep_end = parsed.get("episode_end") or episode
        if want_episode is not None and not (episode <= want_episode <= ep_end):
            if want_date and parsed.get("air_date") == want_date:
                return None, None
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
                     want_episode=None, size_gb=None, want_year=None, want_title=None,
                     want_date=None, want_absolute=None) -> dict:
    """Judge a parsed search hit against the quality profile + the search scope.

    Returns ``{accepted, score, rejected, tier, quality_label}`` — ``accepted`` False
    means it's filtered out (``rejected`` says why); ``score`` ranks the keepers."""
    parsed = parsed if isinstance(parsed, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    res, source = parsed.get("resolution"), parsed.get("source")
    rejects = profile.get("rejects") or []
    rejected = None

    # Resolution inference — Soulseek files (and plenty of loose torrents) often
    # carry NO resolution token at all ('90.Day.Fiance.S12E09.HEVC.mkv'), which used
    # to dead-end as 'Unknown / unsupported quality' even when the file was clearly a
    # real episode. Sonarr rejects those too — but unlike an indexer we always know
    # the FILE SIZE, so infer a conservative resolution from it (movie/episode only;
    # packs are cumulative so sizes mean nothing there). ffprobe verifies the true
    # quality after download (the existing invariant for sourceless names), and the
    # import rejects fakes — so a wrong guess self-corrects instead of losing a grab.
    inferred = False
    if not res and size_gb and scope in ("movie", "episode"):
        res = _infer_resolution(scope, size_gb)
        inferred = res is not None

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

    # 4) scope validation (episode vs season pack vs series pack; movie year match)
    if not rejected:
        _, scope_reason = _scope_ok(parsed, scope, want_season, want_episode, want_year, want_title,
                                    want_date, want_absolute)
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

    res_lab = resolution_label(res)
    if inferred and res_lab:
        res_lab += "~"          # size-inferred, not from the name — ffprobe confirms on import
    label = " · ".join([x for x in [res_lab,
                        (source or "").upper() if source else "", fam.upper() if fam else ""] if x])
    return {"accepted": rejected is None, "score": score, "rejected": rejected,
            "tier": tier, "quality_label": label}


def _infer_resolution(scope, size_gb):
    """Conservative size→resolution guess for a release whose NAME carries no
    resolution token. Thresholds sit at the low edge of each tier's typical size so
    we under-promise (a 1.4GB episode reads as 1080p web, an 800MB one as 720p) —
    the post-download ffprobe measures the truth."""
    try:
        gb = float(size_gb)
    except (TypeError, ValueError):
        return None
    if gb <= 0:
        return None
    if scope == "episode":
        if gb < 0.25:
            return "480p"
        if gb < 0.85:
            return "720p"
        if gb < 4.0:
            return "1080p"
        return "2160p"
    # movie
    if gb < 0.95:
        return "480p"
    if gb < 2.5:
        return "720p"
    if gb < 12.0:
        return "1080p"
    return "2160p"


__all__ = ["resolution_rank", "resolution_label", "meets_cutoff", "evaluate_owned",
           "tier_key", "evaluate_release"]
