"""#1201 (wishx): similar-artist bubbles never show a photo.

The CSS was cleared first — a real-Chromium probe of the exact markup
shared-helpers.js builds showed the <img> painting at full size and winning
elementFromPoint over the name gradient. So the markup and styling are fine
and the URL simply never arrives.

It never arrives because of a contract the caller half-used: the artist-image
endpoint takes an optional ``name``, and the resolver DOCUMENTS it as required
for sources that store no artist image of their own. MusicBrainz is exactly
that case — it resolves through url-relations first and otherwise falls back to
an iTunes/Deezer lookup by name. The similar-artists lazy loader sent
``source`` and ``plugin`` but never ``name``, so that fallback could not run and
the bubble kept its placeholder forever.
"""

from __future__ import annotations

from pathlib import Path

from core.metadata import artist_image

_ROOT = Path(__file__).resolve().parents[1]
_HELPERS = (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(
    encoding="utf-8", errors="replace")


def _no_relations(monkeypatch):
    """A MusicBrainz artist whose relations yield nothing — the fallback case."""
    monkeypatch.setattr(artist_image, "_image_from_musicbrainz_relations",
                        lambda _id: None)


def test_musicbrainz_artist_resolves_nothing_without_a_name(monkeypatch):
    _no_relations(monkeypatch)
    called = []
    monkeypatch.setattr(artist_image, "_lookup_artist_image_by_name",
                        lambda name: called.append(name) or "http://img/x.jpg")

    out = artist_image.get_artist_image_url("mbid-1", source_override="musicbrainz")

    assert out is None            # ...the empty bubble wishx sees
    assert called == []           # the by-name fallback never even ran


def test_the_same_artist_resolves_once_the_name_is_passed(monkeypatch):
    _no_relations(monkeypatch)
    monkeypatch.setattr(artist_image, "_lookup_artist_image_by_name",
                        lambda name: f"http://img/{name}.jpg")

    out = artist_image.get_artist_image_url(
        "mbid-1", source_override="musicbrainz", artist_name="Plumtree")

    assert out == "http://img/Plumtree.jpg"


def test_a_musicbrainz_relation_still_wins_over_the_name_lookup(monkeypatch):
    """#1036: the name lookup takes the first hit, and a same-named artist can
    hijack the photo. Relations must stay the first choice."""
    monkeypatch.setattr(artist_image, "_image_from_musicbrainz_relations",
                        lambda _id: "http://img/exact.jpg")
    monkeypatch.setattr(artist_image, "_lookup_artist_image_by_name",
                        lambda name: "http://img/WRONG-same-name.jpg")

    out = artist_image.get_artist_image_url(
        "mbid-1", source_override="musicbrainz", artist_name="Korn")

    assert out == "http://img/exact.jpg"


def test_the_lazy_loader_actually_sends_the_name():
    """The resolver can only use what the caller sends."""
    fn = _HELPERS[_HELPERS.index("async function lazyLoadSimilarArtistImages"):]
    fn = fn[:fn.index("\n}")]
    assert "params.set('name', artistName)" in fn, (
        "the by-name fallback cannot run without it — this is #1201")
    # and the image goes in as a NODE, not interpolated markup: an artist name
    # carrying a quote used to break out of the alt attribute
    assert "imageContainer.innerHTML = `<img" not in fn
    assert "document.createElement('img')" in fn
