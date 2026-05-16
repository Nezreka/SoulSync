"""Standardized personalized-playlist subsystem.

Replaces the patchwork of `PersonalizedPlaylistsService` (computed-on-
demand views, no persistence) + `discovery_curated_playlists` (ID-only
storage) + `curated_seasonal_playlists` (full storage) with a single
unified abstraction:

- ``manager.PersonalizedPlaylistManager`` — owns the storage layer +
  generator dispatch + refresh lifecycle.
- ``specs.PlaylistKindSpec`` — one spec per playlist KIND
  (``hidden_gems``, ``time_machine``, ``seasonal_mix``, etc.) with
  generator function, default config, variant resolver, and display-
  name template.
- ``types.Track`` / ``types.PlaylistConfig`` — shared dataclasses.

The legacy ``PersonalizedPlaylistsService`` keeps its existing
generator implementations — they're called BY the manager rather than
duplicated. This means:
- The improved diversity logic / popularity thresholds / blacklist
  filtering all stays.
- New behavior layered on top: persistence, refresh-on-demand,
  per-playlist user-tweakable config, staleness windows, listening-
  history cross-reference, seeded randomization.
"""
