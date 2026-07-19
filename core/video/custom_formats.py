"""Custom Formats — scored release matchers (arr-parity P3).

Radarr's Custom Formats let a release's NAME move its rank: prefer trusted
groups, penalize x265 for movies but not TV, chase freeleech tags, bury
hardcoded-subs releases — anything a regex can see. SoulSync's version keeps
the sharp edge and drops the ceremony:

  format = {id, name, include: [term, ...], exclude: [term, ...], score}

  · a term is a case-insensitive SUBSTRING, or a REGEX when written /like.this/
  · a format matches when ALL include terms hit and NO exclude term does
    (synonyms go inside one regex term: /x265|hevc/)
  · matching formats ADD their score to the release's rank (negative scores
    bury without rejecting)
  · per-profile overrides: profile["format_scores"][str(id)] replaces the
    format's default score — 4K profile can love what the TV profile hates
  · profile["min_format_score"] (default 0 = off) HARD-rejects accepted
    releases whose summed format score falls below it

Definitions are global (video_settings['custom_formats']); scoring is applied
inside the shared ranker so every path — drain, RSS, manual search, requery —
judges identically. Pure matching; storage helpers take the db.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.custom_formats")

_KEY = "custom_formats"
MAX_TERMS = 16
MAX_FORMATS = 64


def _norm_terms(value: Any) -> List[str]:
    out: List[str] = []
    for t in (value if isinstance(value, list) else []):
        s = str(t or "").strip()
        if s and s not in out:
            out.append(s[:200])
        if len(out) >= MAX_TERMS:
            break
    return out


def normalize_format(raw: Any) -> Optional[Dict[str, Any]]:
    """One stored/posted format → valid shape, or None when unusable."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    include = _norm_terms(raw.get("include"))
    exclude = _norm_terms(raw.get("exclude"))
    if not name or (not include and not exclude):
        return None
    try:
        score = max(-1000, min(1000, int(raw.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    fid = raw.get("id")
    return {"id": int(fid) if isinstance(fid, (int, float)) and int(fid) >= 1 else None,
            "name": name[:80], "include": include, "exclude": exclude, "score": score}


def load_formats(db) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(db.get_setting(_KEY) or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        f = normalize_format(r)
        if f and f["id"]:
            out.append(f)
    return out


def save_format(db, raw: Any) -> Optional[Dict[str, Any]]:
    """Create (no id) or update a format; returns the stored entry."""
    f = normalize_format(raw)
    if not f:
        return None
    rows = load_formats(db)
    if f["id"] is None:
        f["id"] = max([0] + [r["id"] for r in rows]) + 1
        rows.append(f)
    else:
        rows = [r for r in rows if r["id"] != f["id"]] + [f]
    db.set_setting(_KEY, json.dumps(rows[:MAX_FORMATS]))
    return f


def delete_format(db, format_id: Any) -> bool:
    try:
        fid = int(format_id)
    except (TypeError, ValueError):
        return False
    rows = load_formats(db)
    kept = [r for r in rows if r["id"] != fid]
    if len(kept) == len(rows):
        return False
    db.set_setting(_KEY, json.dumps(kept))
    return True


# ── pure matching ─────────────────────────────────────────────────────────────

def _term_matches(term: str, name: str) -> bool:
    if len(term) > 2 and term.startswith("/") and term.endswith("/"):
        try:
            return re.search(term[1:-1], name, re.IGNORECASE) is not None
        except re.error:
            return False           # a broken regex matches nothing, never explodes
    return term.lower() in name.lower()


def matching_formats(name: Any, formats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The formats a release name satisfies (ALL includes hit, NO exclude does)."""
    n = str(name or "")
    if not n:
        return []
    out = []
    for f in formats or []:
        if f.get("include") and not all(_term_matches(t, n) for t in f["include"]):
            continue
        if any(_term_matches(t, n) for t in f.get("exclude") or []):
            continue
        out.append(f)
    return out


def format_score(name: Any, formats: List[Dict[str, Any]],
                 profile: Optional[Dict[str, Any]]) -> Tuple[int, List[str]]:
    """(summed score, matched names) for a release under a profile — per-profile
    overrides in profile['format_scores'] replace each format's default."""
    overrides = (profile or {}).get("format_scores") or {}
    total = 0
    names: List[str] = []
    for f in matching_formats(name, formats):
        try:
            sc = int(overrides.get(str(f["id"]), f["score"]))
        except (TypeError, ValueError):
            sc = f["score"]
        total += sc
        names.append(f["name"])
    return total, names
