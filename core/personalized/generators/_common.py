"""Shared helpers for personalized-playlist generators.

Each per-kind generator module is small + mechanical — it pulls the
legacy ``PersonalizedPlaylistsService`` instance off the deps object
and calls the matching method, then coerces results. This module
holds the bits every generator reuses so we don't repeat them
five times."""

from __future__ import annotations

from typing import Any, List

from core.personalized.types import Track


def get_service(deps: Any):
    """Pull the ``PersonalizedPlaylistsService`` instance from deps.

    Generators access the service via ``deps.service``. Tests can
    pass a fake deps namespace with a ``service`` attribute that
    returns a stub. Raises a clear error if the dep isn't wired."""
    service = getattr(deps, 'service', None) or (deps.get('service') if isinstance(deps, dict) else None)
    if service is None:
        raise RuntimeError(
            "Personalized generator deps missing `service` "
            "(PersonalizedPlaylistsService instance). Wire it during "
            "PersonalizedPlaylistManager construction."
        )
    return service


def coerce_tracks(rows: List[dict]) -> List[Track]:
    """Convert legacy generator output (list of dicts) into Track
    instances. Tolerates None / non-list inputs by returning []."""
    if not rows:
        return []
    return [Track.from_dict(row) for row in rows if isinstance(row, dict)]
