"""Listening Mix (#913) generator.

Reads the full, render-ready track dicts the watchlist scan stored under the
``listening_recs_tracks_full`` metadata key — built from the recommended artists'
top tracks (see ``core.watchlist_scanner._build_listening_recommendations``) — and
coerces them into ``Track`` records.

Unlike Fresh Tape / Archives this needs NO discovery-pool hydration: the stored
dicts are already complete, so the snapshot can't shrink when the pool rotates. It
also means the generator is a pure read — generation/network all happen during the
scan; this just hands the stored tracks to the personalized manager so the mix can
participate in the Sync-page mirror + Auto-Sync pipeline like every other kind."""

from __future__ import annotations

import json
from typing import Any, List

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'listening_mix'
METADATA_KEY = 'listening_recs_tracks_full'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    """Return the stored Listening Mix tracks, trimmed to ``config.limit``.

    Empty (not an error) when the scan hasn't produced a mix yet — the manager
    preserves any prior snapshot rather than dropping it. Tolerates a missing/garbage
    metadata blob the same way.
    """
    db = getattr(deps, 'database', None) or (deps.get('database') if isinstance(deps, dict) else None)
    if db is None:
        raise RuntimeError("Listening Mix generator deps missing `database`")

    raw = db.get_metadata(METADATA_KEY)
    if not raw:
        return []
    try:
        rows = json.loads(raw) or []
    except (ValueError, TypeError):
        return []

    tracks: List[Track] = []
    for d in rows:
        if not isinstance(d, dict):
            continue
        tracks.append(Track.from_dict(d))
        if len(tracks) >= config.limit:
            break
    return tracks


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Your Listening Mix',
    description='Tracks from artists matched to what you actually listen to.',
    default_config=PlaylistConfig(limit=50, max_per_album=5, max_per_artist=3),
    generator=generate,
    requires_variant=False,
    tags=['curated', 'listening'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
