"""a MusicBrainz id is no promise of Cover Art Archive art. the lookup used
to return the CAA link unchecked and stop: the fix saved a dead link as the
album's cover and no other source was tried (third #1062 review P2C-2)."""

from __future__ import annotations

from types import SimpleNamespace

from core.library2 import provider_adapters as pa


def test_a_release_with_no_caa_art_falls_through_to_the_next_source(monkeypatch):
    monkeypatch.setattr(pa, "_caa_has_front", lambda scope, mbid: False)
    deezer = SimpleNamespace(get_album=lambda _id, *a, **k: {
        "id": "dz-1", "images": [{"url": "https://cdn-images.dzcdn.net/x/1000x1000.jpg"}]})
    monkeypatch.setattr("core.metadata.registry.get_client_for_source",
                        lambda source: deezer if source == "deezer" else None)
    monkeypatch.setattr(pa, "_exact_identity_matches", lambda payload, source, pid: True)

    result = pa.fetch_artwork_url(
        "album", artist_name="A", album_title="B",
        source_ids={"musicbrainz": "mb-rel", "deezer": "dz-1"},
        source_order=("musicbrainz", "deezer"), allow_search=False)

    assert result is not None and result.source == "deezer"


def test_a_release_that_has_caa_art_still_uses_it(monkeypatch):
    monkeypatch.setattr(pa, "_caa_has_front", lambda scope, mbid: True)
    result = pa.fetch_artwork_url(
        "album", artist_name="A", album_title="B",
        source_ids={"musicbrainz": "mb-rel"}, source_order=("musicbrainz",), allow_search=False)
    assert result.source == "caa" and "mb-rel" in result.url


def test_only_the_archives_own_no_drops_the_link(monkeypatch):
    """full suite follow-up: a timeout or no network says nothing about the
    art; dropping the link then lost real covers on a blip"""
    import io
    import urllib.error
    import urllib.request

    def answer(exc=None, body=b'{"images": []}'):
        def _open(*_a, **_k):
            if exc is not None:
                raise exc
            return io.BytesIO(body)
        return _open

    probe = pa._caa_front_answer
    monkeypatch.setattr(urllib.request, "urlopen",
                        answer(urllib.error.HTTPError("u", 404, "nf", {}, None)))
    assert probe("release", "x") is False
    monkeypatch.setattr(urllib.request, "urlopen", answer(urllib.error.URLError("offline")))
    assert probe("release", "x") is True
    monkeypatch.setattr(urllib.request, "urlopen", answer(body=b'{"images": [{"front": false}]}'))
    assert probe("release", "x") is False
    monkeypatch.setattr(urllib.request, "urlopen", answer(body=b'{"images": [{"front": true}]}'))
    assert probe("release", "x") is True
