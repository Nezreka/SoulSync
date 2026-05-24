"""Daily Mix generator — top library genre → discovery picks.

Variant = rank position as a string ('1' / '2' / '3' / '4'). Each
mix tracks the user's Nth top library genre and returns discovery
picks within it. Top genres recompute at refresh time, so as the
library evolves a mix's underlying genre can shift -- the playlist
metadata records which genre was used at the most recent refresh
so the UI can label the mix accurately.

Note: previously this kind ambitiously promised 50% library + 50%
discovery. The library half was a stub (`tracks` table has no
source IDs to sync), so the new generator is discovery-only.
A future enhancement can backfill source IDs into library rows
and re-add the hybrid behavior."""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'daily_mix'

# Default rank set — UI surfaces 4 daily mixes by default.
_DEFAULT_RANKS = ('1', '2', '3', '4')

# Number of top library genres to consider when ranking.
_MAX_TOP_GENRES = 8


def _resolve_genre_for_rank(service, rank: int) -> str:
    """Look up the user's Nth-ranked top library genre. Returns the
    genre key or '' when no genre at that rank.

    Calls ``service.get_top_genres_from_library(limit=...)`` and
    indexes the resulting (genre, count) tuples by 0-based rank.
    """
    top = service.get_top_genres_from_library(limit=_MAX_TOP_GENRES) or []
    if rank < 1 or rank > len(top):
        return ''
    pair = top[rank - 1]
    if not pair:
        return ''
    # `top` is List[Tuple[str, int]] per service signature.
    return pair[0] if isinstance(pair, (tuple, list)) else str(pair)


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    try:
        rank = int(variant)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Daily Mix variant {variant!r} must be a rank int") from exc
    genre = _resolve_genre_for_rank(service, rank)
    if not genre:
        # User's library doesn't have enough genres for this rank.
        return []
    rows = service.get_genre_playlist(genre=genre, limit=config.limit)
    return coerce_tracks(rows)


def variant_resolver(deps: Any) -> List[str]:
    """Return the standard rank set."""
    return list(_DEFAULT_RANKS)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Daily Mix {variant}',
    description='Personalized mix based on your top library genres. One mix per top genre rank.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=3),
    generator=generate,
    variant_resolver=variant_resolver,
    requires_variant=True,
    tags=['discovery', 'personalized'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
