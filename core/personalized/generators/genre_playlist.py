"""Genre Playlist generator — discovery picks within one genre.

Variant = either a parent-genre key from
``PersonalizedPlaylistsService.GENRE_MAPPING`` (e.g. ``'rock'``,
``'electronic_dance'``) or a specific child-genre keyword (e.g.
``'house'``). Stored variant is always normalized to lowercase
underscore-separated form so the UI and storage agree.
"""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'genre_playlist'


def _normalize_variant_to_genre_key(variant: str, service) -> str:
    """Resolve a variant string back into the genre identifier the
    legacy service expects.

    Service accepts both parent-genre KEYS from GENRE_MAPPING (e.g.
    'Electronic/Dance', 'Hip Hop/Rap') and free-form keywords.
    The URL-safe variant we store is the parent key with `/` replaced
    by `_` and lowercased — e.g. 'electronic_dance'. This helper
    inverts that mapping."""
    if not variant:
        raise ValueError('Genre playlist requires a variant')

    # Build a once-computed lookup of normalized → original parent key.
    mapping = getattr(service, 'GENRE_MAPPING', {})
    for parent_key in mapping.keys():
        normalized = parent_key.lower().replace('/', '_').replace(' ', '_')
        if normalized == variant.lower():
            return parent_key

    # Fall through: treat the variant as a free-form keyword (the
    # legacy service handles partial matching for those).
    return variant


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    genre_key = _normalize_variant_to_genre_key(variant, service)
    rows = service.get_genre_playlist(genre=genre_key, limit=config.limit)
    return coerce_tracks(rows)


def variant_resolver(deps: Any) -> List[str]:
    """Return the URL-safe variant for every parent genre defined on
    the service. Specific (free-form) genre variants aren't enumerated
    — they're created on demand when the user requests a custom one."""
    service = get_service(deps)
    mapping = getattr(service, 'GENRE_MAPPING', {})
    return [
        parent.lower().replace('/', '_').replace(' ', '_')
        for parent in mapping.keys()
    ]


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Genre — {variant}',
    description='Discovery picks within one genre. Supports parent-genre families + free-form genre keywords.',
    default_config=PlaylistConfig(limit=50, max_per_album=3, max_per_artist=5),
    generator=generate,
    variant_resolver=variant_resolver,
    requires_variant=True,
    tags=['genre'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
