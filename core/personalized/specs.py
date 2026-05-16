"""Per-kind specifications for the personalized-playlist subsystem.

A ``PlaylistKindSpec`` declares everything the manager needs to know
about one playlist type:

- The kind identifier (stable string used in URLs / configs / DB).
- Human-readable display name template (with optional ``{variant}``
  substitution).
- Whether the kind supports / requires variants and what valid
  variants look like.
- The default user-tweakable config for this kind.
- A generator callable that produces a fresh track list given
  ``(deps, variant, config)``.

Generators live in ``core/personalized/generators/`` (added in
later commits as each kind is migrated). For commit 1 the registry
ships empty — schema + manager land first; generators arrive
incrementally with their per-kind tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.personalized.types import PlaylistConfig, Track


# Generator callable signature.
# Args:
#   deps: opaque object the generator may need (DB, service handle,
#         config_manager). Each generator declares what it pulls.
#   variant: e.g. '1980s' for time machine, 'halloween' for seasonal.
#            Empty string '' for singleton kinds.
#   config: PlaylistConfig with the user's per-playlist overrides
#           merged onto the kind's defaults.
# Returns:
#   List[Track] — the fresh snapshot to persist as the playlist's
#   current track list.
GeneratorFn = Callable[[Any, str, PlaylistConfig], List[Track]]


# Variant resolver: returns the list of currently-valid variant
# identifiers for a kind that supports multiple instances. Used by
# the manager when auto-creating playlist rows for newly available
# variants (e.g. a new decade, a new season). Singletons return [''].
VariantResolver = Callable[[Any], List[str]]


@dataclass
class PlaylistKindSpec:
    """Declaration of one playlist kind.

    See module docstring for the contract.
    """

    kind: str
    name_template: str  # e.g. 'Time Machine — {variant}', 'Hidden Gems'
    description: str
    default_config: PlaylistConfig
    generator: GeneratorFn
    variant_resolver: Optional[VariantResolver] = None
    requires_variant: bool = False
    # Tags for UI grouping ('curated' / 'discovery' / 'time' / 'genre').
    tags: List[str] = field(default_factory=list)

    def display_name(self, variant: str) -> str:
        """Render the human-readable playlist name for a given variant."""
        if not variant:
            return self.name_template.replace('{variant}', '').strip(' —-')
        return self.name_template.format(variant=variant)


class PlaylistKindRegistry:
    """Module-level registry of every kind the manager knows about.

    Populated at import time as each generator module is loaded. The
    manager queries the registry at runtime to dispatch refresh
    requests, list available kinds for the UI, and resolve variants.
    """

    def __init__(self) -> None:
        self._kinds: Dict[str, PlaylistKindSpec] = {}

    def register(self, spec: PlaylistKindSpec) -> None:
        if spec.kind in self._kinds:
            raise ValueError(f"Kind {spec.kind!r} already registered")
        self._kinds[spec.kind] = spec

    def get(self, kind: str) -> Optional[PlaylistKindSpec]:
        return self._kinds.get(kind)

    def all(self) -> List[PlaylistKindSpec]:
        return list(self._kinds.values())

    def kinds(self) -> List[str]:
        return list(self._kinds.keys())

    def reset_for_tests(self) -> None:
        """Drop every registration. Tests only — production runs
        register at module import and never reset."""
        self._kinds.clear()


# Module-level singleton. Generators register against this on import.
_registry = PlaylistKindRegistry()


def get_registry() -> PlaylistKindRegistry:
    """Public accessor for the module-level registry. Tests can reset
    via ``get_registry().reset_for_tests()``."""
    return _registry


__all__ = [
    'PlaylistKindSpec',
    'PlaylistKindRegistry',
    'GeneratorFn',
    'VariantResolver',
    'get_registry',
]
