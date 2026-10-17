"""Shared base for media server clients: declared capabilities and the
hooks the library scan and the UI ask a server for.

Call sites today ask "is the active server Navidrome?" when what they
mean is "can the active server search tracks?", "does it need a rescan
after a tag write?", "can a profile act as one of its users?". Each
client answers those here, once, as a ``CAPABILITIES`` set and a few
hook methods with safe defaults. A call site that reads
``engine.active_supports(Capability.SEARCH)`` keeps working when a fifth
server arrives; one that reads ``active_server == 'navidrome'`` does not.

Nothing in this module changes behaviour on its own. The hooks have the
defaults the call sites assume today for a server that does not override
them, and the capability sets describe what each client already
implements. Call sites move over one at a time.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

from core.media_server.contract import CAPABILITY_METHODS, MediaServerClient


class MediaServerBase(MediaServerClient):
    """Explicit base every client inherits. Subclasses set
    ``CAPABILITIES`` and override the hooks that apply to them."""

    #: Capabilities this server really provides. The conformance test
    #: checks every declared capability has its backing methods, so a
    #: stub (Navidrome's metadata writers) must not be declared.
    CAPABILITIES: FrozenSet[str] = frozenset()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    @property
    def capabilities(self) -> FrozenSet[str]:
        return self.CAPABILITIES

    def supports(self, capability: str) -> bool:
        return capability in self.CAPABILITIES

    @classmethod
    def declared_capability_gaps(cls) -> Dict[str, list]:
        """{capability: [missing method, ...]} for every declared
        capability whose backing methods the class does not define.
        Empty when the declaration is honest."""
        gaps: Dict[str, list] = {}
        for cap in cls.CAPABILITIES:
            missing = [m for m in CAPABILITY_METHODS.get(cap, ()) if not callable(getattr(cls, m, None))]
            if missing:
                gaps[cap] = missing
        return gaps

    # ------------------------------------------------------------------
    # Scan hooks. database_update_worker branches on server_type for
    # these today; the defaults here are what it assumes for Plex.
    # ------------------------------------------------------------------

    def scan_hints(self) -> Dict[str, Any]:
        """Tuning the scan worker should apply for this server.

        Keys read by the worker:
          ``max_workers_cap``   upper bound on parallel workers (int)
          ``preparing_message`` phase text shown before the first fetch
          ``caches_listings``   the client keeps album/track listings on a
                                process-wide cache the scan must clear
        Absent key means no constraint / no message / no cache."""
        return {}

    def post_scan(self, database) -> bool:
        """Runs after a full or deep scan finished writing. Returns True
        when nothing was needed or the work succeeded. Default: nothing
        to do."""
        return True

    # ------------------------------------------------------------------
    # Artwork. Each server stores a relative or server-local image
    # reference on artists and albums; turning it into something a
    # browser can load needs the server's base URL and auth, which only
    # the client knows. core.metadata.artwork used to rebuild all three
    # shapes itself from config.
    # ------------------------------------------------------------------

    def image_url(self, thumb: Optional[str]) -> Optional[str]:
        """A loadable URL for a server image reference, or None when the
        reference is not this server's or the client is not configured.
        Default: no server rule."""
        return None

    # ------------------------------------------------------------------
    # Per-user view. Plex, Jellyfin and Navidrome each have a method
    # that returns "this client, acting as one server user", with a
    # different signature. ``user_view`` is the one name for it.
    # ------------------------------------------------------------------

    def user_view(self, identity: Dict[str, Any]) -> Optional['MediaServerBase']:
        """This client acting as the server user ``identity`` describes,
        or None when the server has no user accounts. Keys per server:
        Plex ``token`` (+ ``title``), Jellyfin ``user_id`` (+
        ``library_id``), Navidrome ``username`` + ``password``."""
        return None
