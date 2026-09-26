"""Why a recommendation exists, in one shape (plan phase 5b).

Every producer writes this at the moment it makes the recommendation, from
the signal it actually used, so no screen has to guess afterwards::

    {"kind": "similar_to" | "listened" | "genre" | "new_release" | "trending",
     "seeds": [{"name": "Tool", "id": "...", "source": "deezer"}],
     "confidence": 0.82}

It lives under ``explanation``: ``why`` was already taken by the chips on
artist cards and the per-track note on BYLT rows.

``kind`` says what the seeds are: artists yours resembles (similar_to),
artists you play (listened), genres (genre), the artist with something new
(new_release), or nothing personal (trending). ``confidence`` is 0..1, or
None when the producer has no honest number.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

KINDS = ('similar_to', 'listened', 'genre', 'new_release', 'trending')
MAX_SEEDS = 5


def seed(name: Any, id: Any = None, source: Optional[str] = None) -> Dict[str, Any]:  # noqa: A002
    return {'name': str(name or '').strip(), 'id': str(id) if id else None,
            'source': source or None}


def explanation(kind: str, seeds: Iterable[Any] = (),
                confidence: Optional[float] = None) -> Dict[str, Any]:
    """The shape. ``seeds`` takes seed dicts or plain names; blanks and
    repeats go, and at most five are kept (the line names two or three)."""
    if kind not in KINDS:
        raise ValueError(f"unknown explanation kind: {kind!r}")
    out: List[Dict[str, Any]] = []
    seen = set()
    for s in seeds or ():
        if isinstance(s, str):
            item = seed(s)
        elif isinstance(s, dict):
            item = seed(s.get('name'), s.get('id'), s.get('source'))
        else:
            continue
        if not item['name']:
            continue
        key = item['name'].casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_SEEDS:
            break
    if confidence is not None:
        try:
            confidence = round(min(1.0, max(0.0, float(confidence))), 2)
        except (TypeError, ValueError):
            confidence = None
    return {'kind': kind, 'seeds': out, 'confidence': confidence}


def consensus_confidence(endorsements: Any) -> Optional[float]:
    """How sure N agreeing seeds make us: 1 → .5, 2 → .75, 3 → .88. More
    of your artists pointing at the same place is the signal, and it
    saturates rather than climbing forever."""
    try:
        n = int(endorsements or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return round(1 - 0.5 ** n, 2)
