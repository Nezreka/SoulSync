"""#767-2: the reorganizer's on-demand alternate-edition path.

When the walked edition (the first source we have an ID for) clearly misfits the
on-disk files — e.g. a 1-track single whose only ID points at the 10-track deluxe
— `_resolve_source` must find a better-fitting edition, use it for the plan, and
(on apply) persist the canonical pin. A well-fitting album must keep today's exact
behavior and never trigger an alternate fetch."""

from __future__ import annotations

import core.library_reorganize as lr
import core.metadata.canonical_resolver as cr

# Provider-shaped raw tracklists (what get_album_tracks_for_source returns).
SINGLE_RAW = [{"name": "Scatterbrain", "track_number": 1, "duration_ms": 129_000}]
DELUXE_RAW = [{"name": "Intro", "track_number": 1, "duration_ms": 200_000}] + [
    {"name": "Scatterbrain", "track_number": 2, "duration_ms": 130_000}
] + [
    {"name": f"Bonus {i}", "track_number": i + 2, "duration_ms": 180_000}
    for i in range(1, 9)
]
# Resolver-normalised shape (what default_fetch_tracklist returns).
SINGLE_NORM = [{"title": "Scatterbrain", "track_number": 1, "duration_ms": 129_000}]
DELUXE_NORM = [{"title": t["name"], "duration_ms": t["duration_ms"]} for t in DELUXE_RAW]

ALBUM_META = {
    "sp_deluxe": {"name": "Scatterbrain (Deluxe)"},
    "sp_single": {"name": "Scatterbrain - Single"},
}
TRACKLISTS = {"sp_deluxe": DELUXE_RAW, "sp_single": SINGLE_RAW}


def _wire(monkeypatch, *, alternates):
    """Patch the source-API seams the reorganizer + resolver funnel through."""
    monkeypatch.setattr(lr, "get_source_priority", lambda primary: ["spotify"])
    monkeypatch.setattr(lr, "get_album_for_source", lambda s, aid: ALBUM_META.get(aid))
    monkeypatch.setattr(lr, "get_album_tracks_for_source", lambda s, aid: TRACKLISTS.get(aid))
    # Resolver-internal fetchers (imported by name inside _resolve_better_edition).
    norm = {"sp_deluxe": DELUXE_NORM, "sp_single": SINGLE_NORM}
    monkeypatch.setattr(cr, "default_fetch_tracklist", lambda s, aid: norm.get(aid))
    monkeypatch.setattr(cr, "default_fetch_alternates", alternates)


def test_misfit_single_resolves_to_the_single_edition(monkeypatch):
    alt_calls = []

    def alternates(source, aid, **kw):
        alt_calls.append((source, aid))
        return [
            {"album_id": "sp_single", "tracks": SINGLE_NORM},
            {"album_id": "sp_deluxe", "tracks": DELUXE_NORM},
        ]

    _wire(monkeypatch, alternates=alternates)
    pins = []
    album_data = {
        "spotify_album_id": "sp_deluxe", "title": "Scatterbrain",
        "artist_id": "a1", "artist_name": "The Band",
    }
    file_tracks = [{"duration_ms": 129_000, "title": "Scatterbrain"}]  # owns the single

    source, api_album, items = lr._resolve_source(
        album_data, "spotify",
        file_tracks=file_tracks,
        on_better_edition=lambda s, aid, sc: pins.append((s, aid, sc)),
    )

    assert source == "spotify"
    assert api_album == ALBUM_META["sp_single"]      # used the single, not the deluxe
    assert len(items) == 1
    assert alt_calls, "misfit must trigger an alternate-edition fetch"
    assert pins and pins[0][1] == "sp_single", "apply must persist the better pin"


def test_well_fitting_album_keeps_walk_and_never_expands(monkeypatch):
    alt_calls = []

    def alternates(source, aid, **kw):
        alt_calls.append((source, aid))
        return [{"album_id": "sp_single", "tracks": SINGLE_NORM}]

    _wire(monkeypatch, alternates=alternates)
    pins = []
    # The library actually IS the deluxe (10 matching tracks) -> walk fits -> no expand.
    album_data = {
        "spotify_album_id": "sp_deluxe", "title": "Scatterbrain (Deluxe)",
        "artist_id": "a1", "artist_name": "The Band",
    }
    file_tracks = [{"duration_ms": t["duration_ms"], "title": t["name"]} for t in DELUXE_RAW]

    source, api_album, items = lr._resolve_source(
        album_data, "spotify",
        file_tracks=file_tracks,
        on_better_edition=lambda s, aid, sc: pins.append((s, aid, sc)),
    )

    assert source == "spotify" and api_album == ALBUM_META["sp_deluxe"]
    assert alt_calls == [], "a well-fitting edition must not trigger any alternate fetch"
    assert pins == [], "no pin written when the walk already fits"


def test_strict_source_never_expands(monkeypatch):
    # User explicitly picked the source in the modal -> their choice wins, even on
    # a misfit. No alternate search.
    alt_calls = []

    def alternates(source, aid, **kw):
        alt_calls.append((source, aid))
        return [{"album_id": "sp_single", "tracks": SINGLE_NORM}]

    _wire(monkeypatch, alternates=alternates)
    album_data = {"spotify_album_id": "sp_deluxe", "title": "Scatterbrain"}
    file_tracks = [{"duration_ms": 129_000, "title": "Scatterbrain"}]

    source, api_album, items = lr._resolve_source(
        album_data, "spotify", strict_source=True, file_tracks=file_tracks,
    )
    assert source == "spotify" and api_album == ALBUM_META["sp_deluxe"]
    assert alt_calls == [], "strict_source must not trigger alternate expansion"
