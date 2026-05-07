"""Registry of enrichment workers exposed via the dashboard bubble UI.

Every "bubble" on the dashboard (MusicBrainz, Spotify, iTunes, Last.fm,
Genius, Deezer, Discogs, AudioDB, Tidal, Qobuz) used to have its own
copy-pasted ``status`` / ``pause`` / ``resume`` Flask routes — 30 routes
that differed only in the worker reference and a couple of per-service
quirks. This module collapses them into a single ``EnrichmentService``
descriptor + registry so the generic blueprint in ``core.enrichment.api``
can drive every bubble from one place.

Hydrabase (P2P mirror) and SoulID (entity ID generation) are intentionally
out of scope here — their workers report fundamentally different status
shapes and don't share the bubble pause/resume contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# Default status payload shape returned when a worker isn't initialized.
# Mirrors the shape every per-service route used to inline before this
# refactor; UI consumers depend on these exact keys.
_DEFAULT_STATUS_FALLBACK: Dict[str, Any] = {
    'enabled': False,
    'running': False,
    'paused': False,
    'current_item': None,
    'stats': {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0},
    'progress': {},
}


@dataclass
class EnrichmentService:
    """Descriptor for one enrichment worker exposed via the dashboard.

    The dashboard talks to every worker through three identical-looking
    endpoints (status / pause / resume). The variation between services
    is captured here as data, not branching code:

    - ``worker_getter`` returns the live worker reference (or None when
      initialization failed). Lazy so the registry can be defined before
      web_server.py finishes module-level imports.
    - ``config_paused_key`` is the ``config_manager`` key that persists
      the user's pause / resume choice across restarts. Empty string
      means "do not persist" (Hydrabase historically did this).
    - ``pre_resume_check`` runs before resume — return ``(http_status,
      error_message)`` to short-circuit (Spotify uses this for the
      rate-limit guard).
    - ``auto_pause_token`` matches an entry in
      ``_download_auto_paused`` / ``_download_yield_override`` so the
      pause/resume routes can clean those up correctly. None means
      this service doesn't participate in the auto-pause-during-download
      mechanism.
    - ``extra_status_defaults`` is merged into the fallback status
      payload (Tidal / Qobuz add ``'authenticated': False``).
    """

    id: str
    display_name: str
    worker_getter: Callable[[], Any]
    config_paused_key: str = ''
    pre_resume_check: Optional[Callable[[], Optional[Tuple[int, str]]]] = None
    auto_pause_token: Optional[str] = None
    extra_status_defaults: Dict[str, Any] = field(default_factory=dict)

    def get_worker(self) -> Any:
        """Resolve the worker reference (None if init failed)."""
        try:
            return self.worker_getter()
        except Exception:
            return None

    def fallback_status(self) -> Dict[str, Any]:
        """Return the shape we serve when the worker isn't initialized."""
        payload = dict(_DEFAULT_STATUS_FALLBACK)
        # stats dict is shared — copy so callers can't mutate the module
        # default.
        payload['stats'] = dict(_DEFAULT_STATUS_FALLBACK['stats'])
        if self.extra_status_defaults:
            payload.update(self.extra_status_defaults)
        return payload


# Module-level registry. Populated by ``register_services`` so the host
# (web_server.py) can wire its module-local worker globals + downstream
# state collections (auto-pause sets, rate-limit guard) without circular
# imports.
_REGISTRY: Dict[str, EnrichmentService] = {}


def register_services(services: List[EnrichmentService]) -> None:
    """Replace the active service registry.

    The host registers all services in one call after its workers are
    initialized. Re-registering is allowed (used by tests) — clears the
    previous set.
    """
    _REGISTRY.clear()
    for svc in services:
        if not svc.id:
            raise ValueError("EnrichmentService.id must be non-empty")
        _REGISTRY[svc.id] = svc


def get_service(service_id: str) -> Optional[EnrichmentService]:
    """Return the registered service with this id, or None."""
    return _REGISTRY.get(service_id)


def all_services() -> List[EnrichmentService]:
    """Return every registered service in registration order."""
    return list(_REGISTRY.values())


def all_service_ids() -> List[str]:
    """Return the ids of every registered service."""
    return list(_REGISTRY.keys())


def clear_registry() -> None:
    """Wipe the registry. Test-only — production code uses ``register_services``."""
    _REGISTRY.clear()
