"""available_sources(): which providers can serve right now.

The watchlist panel's "View Discography" link pins ONE source, and the artist
page treats a pinned source as exclusive — it 503s with "provider is
unavailable" rather than falling back, deliberately (falling back would look
up a foreign provider's artist id, miss, and search by NAME, which can serve
a DIFFERENT artist's discography under this one's name).

So the UI must never ASK for a dead provider, and only this module knows
which are alive. Reported on Discord: Spotify switched off entirely, artist
matched only on Spotify, watchlist → artist page = guaranteed error, while
the same artist opened from Discover worked.
"""

from __future__ import annotations

from core.metadata import registry as metadata_registry


def test_only_resolvable_sources_come_back(monkeypatch):
    alive = {"deezer", "itunes"}
    monkeypatch.setattr(
        metadata_registry, "get_client_for_source",
        lambda source: object() if source in alive else None,
    )
    assert metadata_registry.available_sources(
        ("spotify", "itunes", "deezer", "discogs", "musicbrainz")
    ) == ["itunes", "deezer"]


def test_a_raising_probe_counts_as_unavailable(monkeypatch):
    def boom(source):
        if source == "discogs":
            raise RuntimeError("client init exploded")
        return object() if source == "deezer" else None

    monkeypatch.setattr(metadata_registry, "get_client_for_source", boom)
    # Never propagates: this feeds a UI affordance, not a correctness call.
    assert metadata_registry.available_sources(("discogs", "deezer")) == ["deezer"]


def test_input_is_normalized_and_junk_skipped(monkeypatch):
    monkeypatch.setattr(
        metadata_registry, "get_client_for_source",
        lambda source: object() if source == "deezer" else None,
    )
    assert metadata_registry.available_sources((" Deezer ", "", None)) == ["deezer"]
    assert metadata_registry.available_sources(()) == []
    assert metadata_registry.available_sources(None) == []
