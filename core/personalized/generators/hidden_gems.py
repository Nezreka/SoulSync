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
    # ranked by your genre taste, the way discover shows it. without this the
    # synced playlist was random obscure while the page was best obscure
    fn = getattr(deps, 'get_current_profile_id', None) or (
        deps.get('get_current_profile_id') if isinstance(deps, dict) else None
    )
    taste_fn = getattr(service, 'genre_taste_profile', None)
    taste = taste_fn(fn() if callable(fn) else None) if callable(taste_fn) else None
    rows = service.get_hidden_gems(limit=config.limit, taste_profile=taste or None)
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
