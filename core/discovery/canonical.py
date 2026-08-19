"""Canonical-form scoring shared by the discovery workers.

A source's track title is rarely the title a provider indexes. The decoration
varies by source — a file/CSV playlist keeps a raw ``"Arctic Monkeys - Do I
Wanna Know?"``; YouTube Music restates a localized title in Latin next to the
original (``"晴る - Sunny"``); plain YouTube prepends the channel. Scored
verbatim, none of those reach the provider's plain form.

``core.text.source_title.canonical_source_track`` undecorates a title/artist
pair conservatively — it returns the input unchanged unless the decoration is
demonstrable. ``canonical_best_score`` uses that to score a result set BOTH
ways and keep the better confidence, so canonicalization can only ever add a
candidate, never weaken an existing match.

This lives here rather than in one worker because every discovery worker has
the same problem; ``core.discovery.playlist`` had the first copy (#785).
"""

from __future__ import annotations


def canonical_best_score(deps, title, artist, duration_ms, results):
    """Score `results` against the source pair and, when canonicalization
    changes it, against the canonical pair too — keeping whichever scores
    higher.

    `deps` need only supply `discovery_score_candidates(title, artist,
    duration_ms, results) -> (match, confidence, index)`.
    Returns (match, confidence)."""
    match, confidence, _ = deps.discovery_score_candidates(title, artist, duration_ms, results)
    try:
        from core.text.source_title import canonical_source_track
        canon_title, canon_artist = canonical_source_track(title or '', artist or '')
    except Exception:
        return match, confidence
    if (canon_title, canon_artist) != (title, artist):
        alt_match, alt_conf, _ = deps.discovery_score_candidates(canon_title, canon_artist, duration_ms, results)
        if alt_match and alt_conf > confidence:
            return alt_match, alt_conf
    return match, confidence
