"""Discovery Shuffle generator — pure-random discovery pool exploration."""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'discovery_shuffle'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    rows = service.get_discovery_shuffle(limit=config.limit)
    return coerce_tracks(rows)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Discovery Shuffle',
    description='Pure random shuffle from the discovery pool — different every refresh.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=2),
    generator=generate,
    requires_variant=False,
    tags=['discovery'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
