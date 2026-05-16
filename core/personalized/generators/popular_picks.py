"""Popular Picks generator — high-popularity discovery pool picks."""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'popular_picks'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    rows = service.get_popular_picks(limit=config.limit)
    return coerce_tracks(rows)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Popular Picks',
    description='High-popularity tracks from the discovery pool — what most people are listening to.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=3),
    generator=generate,
    requires_variant=False,
    tags=['discovery'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
