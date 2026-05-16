"""Time Machine generator — by-decade discovery picks.

Variant = decade label like ``'1980s'`` / ``'1990s'`` / ``'2000s'``.
The variant resolver returns the standard decade set; users see one
playlist per decade (each independently configurable / refreshable).
"""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'time_machine'


# Standard decades the UI exposes. Adjust here when adding eras.
_DEFAULT_DECADES = ('1960s', '1970s', '1980s', '1990s', '2000s', '2010s', '2020s')


def _decade_to_year(variant: str) -> int:
    """'1980s' -> 1980. Tolerates ' 1980 ', '1980'.

    Raises ValueError for anything that doesn't look like a decade
    label so the manager surfaces a clear error instead of generating
    garbage."""
    cleaned = (variant or '').strip().rstrip('sS')
    try:
        year = int(cleaned)
    except ValueError as exc:
        raise ValueError(f"Time Machine variant {variant!r} not a decade label") from exc
    if year < 1900 or year > 2100:
        raise ValueError(f"Time Machine variant {variant!r} out of range")
    return year


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    decade_year = _decade_to_year(variant)
    rows = service.get_decade_playlist(decade=decade_year, limit=config.limit)
    return coerce_tracks(rows)


def variant_resolver(deps: Any) -> List[str]:
    """Return the standard decade set. Future enhancement: filter to
    decades that actually have data in the discovery pool."""
    return list(_DEFAULT_DECADES)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Time Machine — {variant}',
    description='Tracks from a specific decade. One playlist per decade.',
    default_config=PlaylistConfig(limit=100, max_per_album=3, max_per_artist=5),
    generator=generate,
    variant_resolver=variant_resolver,
    requires_variant=True,
    tags=['time'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
