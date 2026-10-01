"""Daily Mix generator: the same mixes the Discover page shows.

Variant = the mix number as a string ('1', '2', ...). Daily Mix N here IS
Daily Mix N on Discover: both read core.personalized.daily_mixes (taste
clusters from listening history, mostly owned tracks plus a few similar-artist
discovery picks, rebuilt daily), so what you see is what syncs.

this used to be a separate generator (Nth top library genre -> discovery pool
picks), discovery-only and fixed at 4 mixes. same name, different playlist:
clicking Daily Mix 1 on Discover opened one thing, auto-syncing Daily Mix 1 put
another on the server.

owned tracks carry no source id. that's fine, sync matches by title + artist
and skips its id caches when the id is blank."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'daily_mix'

# shown before Discover has built anything (fresh install, no listening yet)
_DEFAULT_RANKS = ('1', '2', '3', '4')


def _database(deps: Any):
    db = getattr(deps, 'database', None) or (deps.get('database') if isinstance(deps, dict) else None)
    if db is None:
        raise RuntimeError("Daily Mix generator deps missing `database`")
    return db


def _profile_id(deps: Any) -> int:
    fn = getattr(deps, 'get_current_profile_id', None) or (
        deps.get('get_current_profile_id') if isinstance(deps, dict) else None
    )
    return fn() if callable(fn) else 1


def _discover_mixes(deps: Any) -> List[Dict[str, Any]]:
    from core.personalized.daily_mixes import get_or_build_daily_mixes
    payload = get_or_build_daily_mixes(_database(deps), _profile_id(deps)) or {}
    return [m for m in payload.get('mixes') or [] if isinstance(m, dict)]


def _mix_for(mixes: List[Dict[str, Any]], variant: str) -> Optional[Dict[str, Any]]:
    for mix in mixes:
        if mix.get('key') == f'daily_mix_{variant}':
            return mix
    return None


def _to_track(t: Dict[str, Any]) -> Track:
    artists = t.get('artists') or []
    first = artists[0] if artists else {}
    artist = first.get('name', '') if isinstance(first, dict) else str(first)
    album = t.get('album') if isinstance(t.get('album'), dict) else {}
    images = album.get('images') or []
    cover = images[0].get('url') if images and isinstance(images[0], dict) else None
    # the full dict rides along so a discovery pick keeps its real id and
    # source for sync and the wishlist
    rich = {k: v for k, v in t.items() if k not in ('owned', 'play_count')}
    return Track(
        track_name=t.get('name') or 'Unknown',
        artist_name=artist or 'Unknown',
        album_name=album.get('name', '') or '',
        album_cover_url=cover,
        duration_ms=int(t.get('duration_ms') or 0),
        track_data_json=rich,
        source=t.get('source'),
    )


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    try:
        int(variant)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Daily Mix variant {variant!r} must be a mix number") from exc
    mix = _mix_for(_discover_mixes(deps), str(variant))
    if not mix:
        # Discover built fewer mixes than this number today
        return []
    tracks = [_to_track(t) for t in mix.get('tracks') or [] if isinstance(t, dict)]
    return tracks[:config.limit]


def variant_resolver(deps: Any) -> List[str]:
    """One variant per mix Discover built, the default four until it has."""
    try:
        keys = [m.get('key', '') for m in _discover_mixes(deps)]
    except Exception:  # noqa: BLE001 - the picker must still render
        return list(_DEFAULT_RANKS)
    variants = [k.rsplit('_', 1)[-1] for k in keys if k.startswith('daily_mix_')]
    return variants or list(_DEFAULT_RANKS)


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Daily Mix {variant}',
    description='The Daily Mixes from Discover: built from what you listen to, mostly songs you own.',
    default_config=PlaylistConfig(limit=50, max_per_album=2, max_per_artist=3),
    generator=generate,
    variant_resolver=variant_resolver,
    requires_variant=True,
    tags=['personalized'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
