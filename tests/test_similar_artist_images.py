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


# ── the ID-resolving sources (spotify / deezer / itunes) ─────────────────────
# The name fallback is a MusicBrainz-only branch. Every other source resolves
# by artist id, so `name` changes nothing for them — worth pinning, because it
# is the difference between "this fixes wishx's install" and "this fixes it
# only if he's on MusicBrainz".

def test_an_id_source_resolves_without_a_name(monkeypatch):
    calls = []
    monkeypatch.setattr(artist_image, "_get_artist_image_from_source",
                        lambda src, aid: calls.append((src, aid)) or "http://img/by-id.jpg")

    out = artist_image.get_artist_image_url("4tZwfgrHOc3mvqYlEYSvVi",
                                            source_override="spotify")

    assert out == "http://img/by-id.jpg"
    assert calls == [("spotify", "4tZwfgrHOc3mvqYlEYSvVi")]


def test_passing_a_name_does_not_disturb_an_id_source(monkeypatch):
    """The #1201 fix adds `name` to EVERY request, not just MusicBrainz ones —
    so prove it is inert where the id is what matters."""
    seen = []
    monkeypatch.setattr(artist_image, "_get_artist_image_from_source",
                        lambda src, aid: seen.append((src, aid)) or "http://img/deezer.jpg")

    with_name = artist_image.get_artist_image_url(
        "12345", source_override="deezer", artist_name="Plumtree")
    without = artist_image.get_artist_image_url("12345", source_override="deezer")

    assert with_name == without == "http://img/deezer.jpg"
    assert seen == [("deezer", "12345"), ("deezer", "12345")]


def test_an_id_source_that_has_no_photo_returns_nothing_not_a_wrong_one(monkeypatch):
    """No silent name-fallback for id sources: taking the first hit for a name
    is how a same-named artist hijacks the photo (#1036)."""
    monkeypatch.setattr(artist_image, "_get_artist_image_from_source",
                        lambda src, aid: None)
    called = []
    monkeypatch.setattr(artist_image, "_lookup_artist_image_by_name",
                        lambda name: called.append(name) or "http://img/WRONG.jpg")

    out = artist_image.get_artist_image_url(
        "12345", source_override="deezer", artist_name="Korn")

    assert out is None
    assert called == []


# ── the real #1201: raw third-party CDN urls ────────────────────────────────
# Measured in chromium with every non-SoulSync request aborted (what Brave
# Shields and LibreWolf do by default):
#     before: 18 bubbles, 0 images, 18 fallbacks
#     after : 18 bubbles, 16 images, 2 fallbacks (those two have no photo)
# wishx's hover-preview extension fetches from the EXTENSION context, which
# page-level blocking does not touch — which is why the image was provably
# "there" while the page could not show it. Every other artwork surface in
# SoulSync already goes out first-party through the image cache; these bubbles
# were the one place still handing the browser a raw CDN link.

def test_similar_artist_payload_serves_images_first_party(monkeypatch):
    from core.metadata import similar_artists as sa

    monkeypatch.setattr(sa, "_extract_artist_image_url",
                        lambda _d: "https://cdn-images.dzcdn.net/images/artist/abc/1000x1000.jpg")
    monkeypatch.setattr("core.image_cache.cached_image_url",
                        lambda url: "/api/image-cache/deadbeef")

    payload = sa._build_similar_artist_payload(
        {"id": 42, "name": "Klingande"}, "deezer")

    assert payload["image_url"] == "/api/image-cache/deadbeef"
    assert not str(payload["image_url"]).startswith("http")


def test_a_cache_failure_falls_back_to_the_original_url(monkeypatch):
    """Art must never be lost to a caching problem — fail OPEN."""
    from core.metadata import similar_artists as sa

    raw = "https://cdn-images.dzcdn.net/images/artist/abc/1000x1000.jpg"

    def _boom(_url):
        raise RuntimeError("cache offline")

    monkeypatch.setattr(sa, "_extract_artist_image_url", lambda _d: raw)
    monkeypatch.setattr("core.image_cache.cached_image_url", _boom)

    payload = sa._build_similar_artist_payload({"id": 1, "name": "X"}, "deezer")
    assert payload["image_url"] == raw


def test_a_missing_image_stays_none(monkeypatch):
    from core.metadata import similar_artists as sa

    monkeypatch.setattr(sa, "_extract_artist_image_url", lambda _d: None)
    payload = sa._build_similar_artist_payload({"id": 1, "name": "X"}, "deezer")
    assert payload["image_url"] is None


def test_the_lazy_load_endpoint_also_returns_a_first_party_url():
    """The second path: a bubble with no image in the payload asks
    /api/artist/<id>/image, which must not hand back a raw CDN url either."""
    src = (_ROOT / "api" / "artist_detail.py").read_text(encoding="utf-8")
    fn = src[src.index("def get_artist_image("):]
    fn = fn[:fn.index("@bp.route", 10)]
    assert "cached_image_url" in fn


def test_the_itunes_enrichment_path_also_caches(monkeypatch):
    """A late-enrichment branch re-assigns image_url AFTER the builder ran.
    It was writing raw CDN urls straight onto the payload, so an
    iTunes-sourced bubble kept exactly the third-party link #1201 is about.
    Found by checking the LIVE response rather than trusting the diff."""
    src = (_ROOT / "core" / "metadata" / "similar_artists.py").read_text(encoding="utf-8")
    branch = src[src.index("if source == 'itunes'"):]
    branch = branch[:branch.index("if target_name")]
    assert "payload['image_url'] = image_url" not in branch
    assert "payload['image_url'] = album_image_url" not in branch
    assert branch.count("_cached_artist_image_url(") == 2
