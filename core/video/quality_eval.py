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


__all__ = ["resolution_rank", "resolution_label", "meets_cutoff", "evaluate_owned"]
