"""Hidden Gems generator — low-popularity tracks from discovery pool.

Wraps ``PersonalizedPlaylistsService.get_hidden_gems`` so the
existing source-aware popularity threshold + diversity filter
behavior is preserved verbatim. The user-tweakable knobs that
arrive via ``PlaylistConfig`` (limit) flow through; future config
options (popularity_max override, exclude_recent_days) get layered
on the wrapper without changing the legacy implementation."""

from __future__ import annotations

from typing import Any, List

from core.personalized.generators._common import coerce_tracks, get_service
from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'hidden_gems'


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    service = get_service(deps)
    rows = service.get_hidden_gems(limit=config.limit)
    return coerce_tracks(rows)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Hidden Gems',
    description='Low-popularity discovery picks — underground / indie tracks you probably haven\'t heard.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=3),
    generator=generate,
    requires_variant=False,
    tags=['discovery'],
)


# Register at import time so the manager auto-discovers this kind.
# Re-import (e.g. test reloads) is tolerated: only register if absent.
if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
