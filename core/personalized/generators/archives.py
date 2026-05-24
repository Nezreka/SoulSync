"""The Archives (Spotify Discover Weekly) generator.

Same shape as Fresh Tape — read curated track-id list from
``discovery_curated_playlists`` under ``discovery_weekly_<source>``
(fallback ``discovery_weekly``), hydrate via discovery pool."""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators.fresh_tape import _hydrate_curated
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'archives'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    return _hydrate_curated(deps, 'discovery_weekly', config)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='The Archives',
    description='Your Spotify Discover Weekly — curated discovery picks.',
    default_config=PlaylistConfig(limit=50, max_per_album=5, max_per_artist=10),
    generator=generate,
    requires_variant=False,
    tags=['curated', 'spotify'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
